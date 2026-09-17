"""bili-history 入口：军事史主题 → GLM讲稿 → Wikimedia配图 → TTS配音 → Ken Burns成片 → 投稿。

用法：
  python autopost.py --dry-run [主题序号(从1)]
  python autopost.py [主题序号]
"""
import argparse, logging, os, random, sys
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


def run(dry_run: bool, topic_index: int | None):
    log = logging.getLogger("main")
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

    # ③ 配音
    narr = cfg["narrator"]
    voices = narrator.narrate(secs, AUD_DIR, narr["voice"], narr["rate"])
    total = sum(v["duration"] for v in voices)
    log.info("配音完成: %d段 共%.1f秒", len(voices), total)

    # ④ 配图（主池策略：主题核心词 1-2 次大搜索拉满池，段落按序取图；
    #          池不足时才用各段 image_query 补搜——请求量从 20+ 降到 2-4 次）
    core_queries = list(dict.fromkeys(
        " ".join(sec["image_query"].split()[:2]) for sec in secs))[:2]
    pool_imgs = []
    for q in core_queries:
        pool_imgs += wikiimg.search_images(q, limit=15)
        if len(pool_imgs) >= len(secs):
            break
    if len(pool_imgs) < len(secs):                       # 池不足，按段补搜
        for sec in secs:
            if len(pool_imgs) >= len(secs) + 2:
                break
            pool_imgs += wikiimg.search_images(sec["image_query"], limit=4)
    if not pool_imgs:
        raise RuntimeError("配图全池失败，稍后重跑（可能处于限流窗口）")

    section_files, credits = [], []
    from urllib.parse import urlparse as _up
    from src.sticker import resolve_stickers
    for i, (sec, v) in enumerate(zip(secs, voices)):
        im = pool_imgs[i % len(pool_imgs)]
        ext = Path(_up(im["url"]).path).suffix.lower() or ".jpg"
        p = f"{IMG_DIR}/sec_{i:02d}{ext}"
        wikiimg.download_image(im["url"], p)
        credits.append(f"{im['title'].replace('File:', '')}（{im['license']}, {im['author']}）")
        stickers = resolve_stickers(sec.get("stickers"), v["duration"])
        if stickers:
            log.info("段落%d 贴纸: %s", i,
                     ", ".join(f"{Path(s['path']).stem}@{s['at']:.1f}s" for s in stickers))
        clip = f"{DL_DIR}/sec_{i:02d}.mp4"
        slideshow.build_section_clip(p, v["path"], v["duration"], clip,
                                     mode="in" if i % 2 == 0 else "out",
                                     stickers=stickers)
        section_files.append(clip)
        log.info("段落%d %.1fs 配图=%s", i, v["duration"], im["title"][5:45])

    # ⑤ 合成
    bgms = sorted(Path(cfg["bgm_dir"]).glob("*.mp3"))
    final = f"{DL_DIR}/final.mp4"
    srt = slideshow.make_srt(voices, f"{DL_DIR}/final.srt")
    slideshow.compose_final(section_files, str(random.choice(bgms)), srt, final,
                            bgm_volume=float(cfg["bgm_volume"]))
    log.info("成片: %s (%.1f MB)", final, os.path.getsize(final) / 1e6)

    # 封面用第一段画面
    cover = f"{DL_DIR}/final_cover.jpg"
    from src.cover import extract_cover
    extract_cover(final, cover, at_second=2)
    log.info("封面: %s", cover)

    desc = (f"{doc['title']}\n\n本片由 AI 辅助制作：文案/配音/剪辑自动化流水线，"
            f"史实内容欢迎评论区指正。\n图片来源（Wikimedia Commons）："
            + "；".join(dict.fromkeys(credits)) +
            "\n贴纸: Twemoji (CC BY 4.0)\nBGM: Kevin MacLeod (incompetech.com), CC BY 4.0")
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
    except Exception as e:
        store.mark_topic(topic, doc["title"], f"failed:{e!r}")
        log.exception("投稿失败")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("topic", nargs="?", type=int, help="主题序号(从1)")
    args = ap.parse_args()
    setup_logging()
    run(args.dry_run, args.topic)
