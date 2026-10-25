"""bili-history 入口：军事史主题 → GLM讲稿 → Wikimedia配图 → TTS配音 → Ken Burns成片 → 投稿。

用法：
  python autopost.py --dry-run [主题序号(从1)]
  python autopost.py [主题序号]
"""
import argparse, logging, os, random, sys, time
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from src import script as script_mod
from src import wikiimg
from src import narrator
from src import slideshow
from src.store import Store
import sau_bridge

LOG_DIR, DL_DIR, IMG_DIR, AUD_DIR, DATA_DIR = "logs", "downloads", "imgs", "audio", "data"


def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(f"{LOG_DIR}/{datetime.now():%Y-%m}.log", encoding="utf-8"),
                  logging.StreamHandler()])


def load_config():
    import yaml
    with open("config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


_lock_acquired = False


def _try_lock() -> bool:
    """产线互斥锁：批量连发/手动运行与定时任务并发时，后到者直接退出，
    防止选题撞车与 GPU 抢占。锁文件 6 小时自动失效（防崩溃残留死锁）。"""
    global _lock_acquired
    import time
    lock = Path(".autopost.lock")
    if lock.exists():
        try:
            if time.time() - lock.stat().st_mtime < 6 * 3600:
                return False
            logging.getLogger("main").warning("发现残留锁(>6h)，视为死锁并接管")
        except OSError:
            pass
    lock.write_text(str(os.getpid()))
    _lock_acquired = True
    return True


def _release_lock():
    if _lock_acquired:
        Path(".autopost.lock").unlink(missing_ok=True)


def run(dry_run: bool, topic_index: int | None):
    log = logging.getLogger("main")
    if not _try_lock():
        log.warning("另一实例运行中（.autopost.lock），本次退出")
        return
    load_dotenv()
    cfg = load_config()
    store = Store(f"{DATA_DIR}/topics.db")

    # ① 主题（跳过做过的）
    topics = [t for i, t in enumerate(cfg["topics"], 1)
              if topic_index is None or i == topic_index]
    fresh = [t for t in topics if not store.is_topic_done(t)]
    if not fresh:
        log.warning("主题池已全部做完，请在 config.yaml 中补充新主题")
        return
    topic = random.choice(fresh)
    log.info("本期主题: %s", topic)

    # ② 讲稿（字数不达标重试一次）
    vc = cfg["video"]
    min_chars = vc["sections_min"] * vc["chars_per_section"][0]
    for attempt in range(2):
        doc = script_mod.generate(
            os.environ["ZHIPU_API_KEY"], topic,
            model=cfg["copywriter"]["model"], temperature=cfg["copywriter"]["temperature"],
            n_min=vc["sections_min"], n_max=vc["sections_max"],
            c_lo=vc["chars_per_section"][0], c_hi=vc["chars_per_section"][1])
        total_chars = sum(len(s["text"]) for s in doc["sections"])
        if total_chars >= min_chars:
            break
        log.warning("讲稿太短(%d字 < %d)，重试", total_chars, min_chars)
    secs = doc["sections"]
    log.info("讲稿就绪: 《%s》 %d段 / %d字", doc["title"], len(secs),
             sum(len(s["text"]) for s in secs))

    # ③ 配音（可插拔后端：edge 免费 / 智谱 cogtts 大模型，后者失败自动降级）
    # 音色池按天轮换：同一天保持统一音色，跨天换声（多样化 A/B，抄 MPT 音色池思路）
    narr = cfg["narrator"]
    pool = narr.get("voices") or [narr["voice"]]
    voice = pool[datetime.now().timetuple().tm_yday % len(pool)]
    log.info("本期音色: %s（池%d个，按天轮换）", voice, len(pool))
    zhipu_cfg = cfg.get("zhipu_tts", {})
    zhipu_cfg = {**zhipu_cfg, "api_key": os.environ.get("ZHIPU_API_KEY", "")} \
        if narr.get("backend") == "zhipu" else None
    voices = []
    for tts_attempt in range(3):
        try:
            voices = narrator.narrate(
                secs, AUD_DIR, voice, narr["rate"],
                backend=narr.get("backend", "edge"), zhipu=zhipu_cfg,
                allow_sapi=bool(narr.get("allow_sapi_fallback", False)),
                cosy=narr.get("cosy"))
            break
        except RuntimeError as e:
            if tts_attempt == 2:
                log.error("TTS 神经音色持续不可用，今日不发布（质量闸门：宁缺毋滥，%s）",
                          str(e)[:60])
                return
            log.warning("TTS 不可用(第%d次)，30分钟后自动重试: %s", tts_attempt + 1,
                        str(e)[:60])
            time.sleep(1800)
    total = sum(v["duration"] for v in voices)
    log.info("配音完成: %d段 共%.1f秒 (backend=%s, voice=%s)", len(voices), total,
             narr.get("backend", "edge"), voice)

    # ④ 配图（主池策略：主题核心词 1-2 次大搜索拉满池，段落按序取图；
    #          池不足时才用各段 image_query 补搜——请求量从 20+ 降到 2-4 次）
    core_queries = list(dict.fromkeys(
        " ".join(sec["image_query"].split()[:3]) for sec in secs))[:2]
    pool_imgs = []
    for q in core_queries:
        pool_imgs += wikiimg.search_images(q, limit=max(15, len(secs)))
        if len(pool_imgs) >= len(secs):
            break
    if len(pool_imgs) < len(secs):                       # 池不足，按段补搜
        for sec in secs:
            if len(pool_imgs) >= len(secs) + 2:
                break
            pool_imgs += wikiimg.search_images(sec["image_query"], limit=4)
    if not pool_imgs:
        raise RuntimeError("配图全池失败，稍后重跑（可能处于限流窗口）")

    fade = float(cfg.get("transition", {}).get("fade_seconds", 0.6))
    modes = ["in", "panright", "out", "panleft"]
    credits, quotes = [], []
    from urllib.parse import urlparse as _up
    from src.sticker import resolve_stickers
    from concurrent.futures import ThreadPoolExecutor

    # ④.5 审查闸门（无记忆审查官）：渲染前拦下史实硬伤/配图跑偏/标题问题
    from src.review import review as review_video
    _img_titles = [pool_imgs[i % len(pool_imgs)]["title"].replace("File:", "")[:60]
                   for i in range(len(secs))]
    try:
        verdict = review_video(os.environ["ZHIPU_API_KEY"], doc["title"],
                               secs[0]["text"][:120], secs, _img_titles)
        for p in verdict["problems"]:
            log.warning("🔍 审查: %s", p)
        if verdict["verdict"] == "reject":
            store.mark_topic(topic, doc["title"], "review-rejected")
            log.error("🔍 审查官否决，本期不发布（宁缺毋滥）")
            return
        if verdict.get("title_fix"):
            log.info("🔍 审查官改题: %s → %s", doc["title"], verdict["title_fix"])
            doc["title"] = verdict["title_fix"]
    except Exception as e:
        log.warning("🔍 审查通道异常（放行）: %s", e)

    # 阶段一（快）：集中下载配图、准备贴纸与运镜参数
    render_jobs = []
    for i, (sec, v) in enumerate(zip(secs, voices)):
        im = pool_imgs[i % len(pool_imgs)]
        ext = Path(_up(im["url"]).path).suffix.lower() or ".jpg"
        p = f"{IMG_DIR}/sec_{i:02d}{ext}"
        wikiimg.download_image(im["url"], p)
        credits.append(f"{im['title'].replace('File:', '')}（{im['license']}, {im['author']}）")
        if sec.get("quote"):
            q = sec["quote"]
            quotes.append(f"“{q.get('text', '')}”——{q.get('author', '')}"
                          f"（{q.get('context', '')}）" if q.get("context")
                          else f"“{q.get('text', '')}”——{q.get('author', '')}")
        stickers = resolve_stickers(sec.get("stickers"), v["duration"])
        if stickers:
            log.info("段落%d 贴纸: %s", i,
                     ", ".join(f"{Path(s['path']).stem}@{s['at']:.1f}s" for s in stickers))

        # 尾部留 fade 秒（除末段）：旁白说完画面停留后溶解切换
        tail = 0.0 if i == len(secs) - 1 else fade
        v["tail"] = tail
        render_jobs.append((i, p, v, tail, modes[i % 4], stickers, im))

    # 阶段二（慢）：4 路并行渲染段落（每段独立 ffmpeg；并发 6 时重段落会被
    # CPU 饿死触发超时，降到 4 留余量；timeout 也放宽到 1500s 双保险）
    log.info("并行渲染 %d 段（4 workers）...", len(render_jobs))
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(
                    slideshow.build_section_clip, p, v["path"],
                    v["duration"] + tail, f"{DL_DIR}/sec_{i:02d}.mp4",
                    mode=m, stickers=s): (i, im, v, tail, m)
                for i, p, v, tail, m, s, im in render_jobs}
        for fut, (i, im, v, tail, m) in futs.items():  # 按提交序收取（dict 保序）
            fut.result()  # 任一段失败即抛错（与原串行行为一致）
            log.info("段落%d %.1fs(+%.1f尾) 运镜=%s 配图=%s",
                     i, v["duration"], tail, m, im["title"][5:40])
    section_files = [f"{DL_DIR}/sec_{i:02d}.mp4" for i in range(len(render_jobs))]

    # ⑤ 合成（xfade 转场轮换 + BGM ducking + 字幕烧录）
    bgms = sorted(Path(cfg["bgm_dir"]).glob("*.mp3"))
    final = f"{DL_DIR}/final.mp4"
    srt = slideshow.make_srt(voices, f"{DL_DIR}/final.srt")
    trans_cfg = cfg.get("transition", {})
    subtitle_style = cfg.get("subtitle", {}).get("force_style") or None
    slideshow.compose_final(section_files, str(random.choice(bgms)), srt, final,
                            bgm_volume=float(cfg["bgm_volume"]), fade=fade,
                            transitions=trans_cfg.get("styles"),
                            subtitle_style=subtitle_style)
    log.info("成片: %s (%.1f MB, %d段, 转场池%s, BGM ducking)",
             final, os.path.getsize(final) / 1e6, len(secs),
             trans_cfg.get("styles") or slideshow.DEFAULT_TRANSITIONS)

    # 封面用第一段画面
    cover = f"{DL_DIR}/final_cover.jpg"
    from src.cover import extract_cover
    extract_cover(final, cover, at_second=2)
    log.info("封面: %s", cover)

    desc = (f"{doc['title']}\n\n本片由 AI 辅助制作：文案/配音/剪辑自动化流水线，"
            f"史实内容欢迎评论区指正。\n图片来源（Wikimedia Commons）："
            + "；".join(dict.fromkeys(credits)))
    if quotes:
        desc += "\n\n名言引用：\n" + "\n".join(dict.fromkeys(quotes))
    desc += "\n贴纸: Twemoji (CC BY 4.0)\nBGM: Kevin MacLeod (incompetech.com), CC BY 4.0"

    # manifest 持久化：进程中断后仍可恢复发布（元数据不再依赖内存）
    import json
    json.dump({"topic": topic, "title": doc["title"], "desc": desc,
               "tags": list(dict.fromkeys(doc["tags"] + cfg["bilibili"]["tags_extra"]))[:10],
               "final": final, "cover": cover},
              open(f"{DL_DIR}/manifest.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    tags = list(dict.fromkeys(doc["tags"] + cfg["bilibili"]["tags_extra"]))[:10]

    if dry_run:
        log.info("[DRY-RUN] 成片: %s\n标题: %s\n简介:\n%s\n标签: %s",
                 final, doc["title"], desc, tags)
        store.mark_topic(topic, doc["title"], "dryrun")
        return

    # ⑥ 投稿（自制类型：AI辅助创作）
    try:
        out = sau_bridge.upload(
            video_path=final, title=doc["title"], desc=desc, tags=tags,
            tid=cfg["bilibili"]["tid"], cover=cover, source_url="",
            copyright_self=True)
        store.mark_topic(topic, doc["title"], "published")
        log.info("投稿成功:\n%s", out[-300:])
        # 数据反馈闭环：记录资产 + 全频道快照战报（借鉴 MatrixMedia / autopilot-shots）
        import re as _re
        _m = _re.search(r"BV[0-9A-Za-z]{10}", out)
        if _m:
            store.record_video(_m.group(0), doc["title"], "军史")
            log.info("资产入库: %s", _m.group(0))
        try:
            from src import stats as stats_mod
            stats_mod.collect_and_report(
                store, int(cfg["bilibili"].get("mid", 1518025639)), log)
        except Exception as e:
            log.warning("战报生成异常(不影响发布): %s", e)
    except Exception as e:
        store.mark_topic(topic, doc["title"], f"failed:{e!r}")
        log.exception("投稿失败")

    # ⑦ 抖音分发（可选：Playwright 驱动创作中心，登录态 data/douyin-state.json）
    if cfg.get("douyin", {}).get("enable"):
        try:
            import douyin_bridge
            tag_str = " ".join(f"#{t}" for t in tags[:4])
            ok = douyin_bridge.upload(final, f"{doc['title']} {tag_str}")
            log.info("抖音分发: %s", "成功" if ok else "失败(见data/截图)")
        except Exception as e:
            log.warning("抖音分发异常(不影响B站): %s", e)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("topic", nargs="?", type=int, help="主题序号(从1)")
    args = ap.parse_args()
    setup_logging()
    try:
        run(args.dry_run, args.topic)
    finally:
        _release_lock()
