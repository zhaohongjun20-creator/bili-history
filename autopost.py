"""bili-history 入口：军事史主题 → GLM讲稿 → Wikimedia配图 → TTS配音 → Ken Burns成片
→ 自动质检 → 投稿。

用法：
  python autopost.py --dry-run [主题序号(从1)]
  python autopost.py [主题序号]
  python autopost.py --redo 主题序号           # 显式重做（重置状态，默认复用已有产物缓存）
  python autopost.py --redo 主题序号 --fresh   # 连缓存一起废弃，从头制作
  python autopost.py --publish-only [主题序号]  # 只投稿已过质检的成片（人工核实后）

可靠性（对应整改方案）：
- 启动前 preflight 检查配置/密钥/路径/BGM/ffmpeg/登录态，不花额度就暴露问题；
- 选题状态机 pending→generating→review→ready→published/failed（src/store.py）；
- 每期任务独立目录 data/jobs/<job_id>/ + manifest，任何阶段失败后重跑自动续跑
  （缓存键=输入内容+模型+参数版本，输入没变就不重复花钱）；
- 审查异常不再自动放行：置 failed(review) 等待重试；
- 互斥锁原子获取（src/singleinstance.py），崩溃残留过期后可接管。
"""
import argparse
import hashlib
import json
import logging
import os
import random
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse as _up

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from src import script as script_mod
from src import wikiimg
from src import narrator
from src import slideshow
from src import jobs as jobs_mod
from src import preflight
from src import qc as qc_mod
from src import review as review_mod
from src.singleinstance import InstanceLock
from src.store import Store, BLOCKING
import sau_bridge

ROOT = Path(__file__).parent
LOG_DIR, DATA_DIR = "logs", "data"
JOBS_DIR = Path(DATA_DIR) / "jobs"

# 缓存键版本号：提示词/流程实质变更时 +1，让旧缓存自然失效
# v2: 段落渲染去掉 -shortest（画面停留修复）+ 混音 0.85 余量 + SRT 时间轴修正
V_SCRIPT, V_REVIEW, V_TTS, V_IMG, V_CLIP, V_COMPOSE = "v1", "v1", "v1", "v1", "v2", "v2"


class StageError(RuntimeError):
    """阶段失败：manifest 已由阶段函数标记，主流程据此更新选题状态。"""
    def __init__(self, stage: str, reason: str):
        super().__init__(f"[{stage}] {reason}")
        self.stage = stage
        self.reason = str(reason)


class HumanGate(RuntimeError):
    """审查要求人工介入（fix 无修法/复审仍 fix）：
    store 已置 review-human，流程停住，等 --redo 重做或 --approve-review 放行。"""


def assign_images(pool_size: int, n: int, max_reuse: int = 1) -> list:
    """配图分配：返回 [(pool_idx, reused_from_or_None)]（按段序）。

    第一轮各段用不同图；池不够时才有序复用，每张图最多服务
    1+max_reuse 段（防"整集反复同一画面"）；池连复用上限都撑不起
    就抛错进入待处理，而不是循环塞无关图（方案 P1-5）。
    """
    if pool_size <= 0:
        raise ValueError("配图池为空")
    if n > pool_size * (1 + max_reuse):
        raise ValueError(
            f"配图池不足: {pool_size}张撑不起{n}段"
            f"（每图最多复用{max_reuse}次），需补搜或人工补素材")
    out = []
    for i in range(n):
        if i < pool_size:
            out.append((i, None))
        else:
            j = (i - pool_size) % pool_size
            out.append((j, j))          # 首轮即按段序取图，pool_idx==首次使用的段号
    return out


def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(f"{LOG_DIR}/{datetime.now():%Y-%m}.log", encoding="utf-8"),
                  logging.StreamHandler()])


def load_config():
    import yaml
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _account_file(cfg: dict) -> Path:
    af = cfg.get("bilibili", {}).get("account_file")
    return (ROOT / af).resolve() if af else sau_bridge._default_account_file()


# ── 阶段实现：已缓存则跳过，否则执行并写 manifest ──────────────────────

def stage_script(job, topic, cfg, log) -> dict:
    """① 讲稿：字数不达标重试一次。产物 script.json。"""
    vc = cfg["video"]
    key = jobs_mod.cache_key(
        topic=topic, v=V_SCRIPT, model=cfg["copywriter"]["model"],
        temperature=cfg["copywriter"]["temperature"],
        n_min=vc["sections_min"], n_max=vc["sections_max"],
        c_lo=vc["chars_per_section"][0], c_hi=vc["chars_per_section"][1])
    if job.reusable("script", key) and Path(job.path("script.json")).exists():
        doc = json.loads(Path(job.path("script.json")).read_text(encoding="utf-8"))
        log.info("讲稿复用缓存: 《%s》%d段", doc["title"], len(doc["sections"]))
        return doc

    job.mark_running("script", key)
    min_chars = vc["sections_min"] * vc["chars_per_section"][0]
    try:
        for attempt in range(2):
            doc = script_mod.generate(
                os.environ.get("ZHIPU_API_KEY", ""), topic,
                model=cfg["copywriter"]["model"], temperature=cfg["copywriter"]["temperature"],
                n_min=vc["sections_min"], n_max=vc["sections_max"],
                c_lo=vc["chars_per_section"][0], c_hi=vc["chars_per_section"][1])
            total = sum(len(s["text"]) for s in doc["sections"])
            if total >= min_chars:
                break
            log.warning("讲稿太短(%d字 < %d)，重试", total, min_chars)
    except Exception as e:
        job.mark_failed("script", e)
        raise StageError("script", e) from e
    Path(job.path("script.json")).write_text(
        json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    job.mark_done("script", key, {"script": "script.json"})
    log.info("讲稿就绪: 《%s》 %d段 / %d字", doc["title"], len(doc["sections"]),
             sum(len(s["text"]) for s in doc["sections"]))
    return doc


def stage_images(job, secs, cfg, log) -> list:
    """② 配图池：主题核心词 1-2 次大搜索拉满池，段落按序取图；
    池不足时才用各段 image_query 补搜。产物 materials/pool.json。

    池条目带 query（来源检索词，写入素材台账）；分配表 assignment
    （段→池图索引，含复用标记）存 manifest，审查与渲染读同一份，
    保证"审查看到的图"与"实际用的图"一一对应（方案 P1-5）。
    """
    queries = [sec["image_query"] for sec in secs]
    core = list(dict.fromkeys(" ".join(q.split()[:3]) for q in queries))[:2]
    key = jobs_mod.cache_key(v=V_IMG, core=core, queries=queries)
    cached = job.stage("images") or {}
    if job.reusable("images", key) and Path(job.path("materials", "pool.json")).exists():
        pool = json.loads(Path(job.path("materials", "pool.json")).read_text(encoding="utf-8"))
        log.info("配图池复用缓存: %d张", len(pool))
        return pool

    job.mark_running("images", key)
    pool_imgs = []
    for q in core:
        pool_imgs += [{**r, "query": q}
                      for r in wikiimg.search_images(q, limit=max(15, len(secs)))]
        if len(pool_imgs) >= len(secs):
            break
    if len(pool_imgs) < len(secs):                       # 池不足，按段补搜
        for q in queries:
            if len(pool_imgs) >= len(secs) + 2:
                break
            pool_imgs += [{**r, "query": q}
                          for r in wikiimg.search_images(q, limit=4)]
    if not pool_imgs:
        job.mark_failed("images", "配图全池失败")
        raise StageError("images", "配图全池失败（可能处于限流窗口），稍后重跑")
    try:
        assignment = assign_images(len(pool_imgs), len(secs))
    except ValueError as e:
        job.mark_failed("images", str(e))
        raise StageError("images", f"{e}（宁缺毋滥，不循环塞无关图）") from e
    Path(job.path("materials", "pool.json")).write_text(
        json.dumps(pool_imgs, ensure_ascii=False, indent=1), encoding="utf-8")
    job.mark_done("images", key, {"pool": "materials/pool.json"},
                  extra={"count": len(pool_imgs),
                         "assignment": [list(a) for a in assignment]})
    log.info("配图池就绪: %d张 / %d段（复用%d段）", len(pool_imgs), len(secs),
             sum(1 for a in assignment if a[1] is not None))
    return pool_imgs


def stage_review(job, topic, doc, pool_imgs, cfg, log) -> dict:
    """③ 审查闸门（渲染/配音之前）。异常/无修法都不放行（方案 P1-4）。

    处置矩阵：
    - 服务异常（重试后）  → failed(review)，下次重跑续走，不发布；
    - reject             → review-rejected，出自动池（--redo 才回）；
    - fix + 有修正标题    → 应用改题后**重新审查一轮**（改过的题必须复审）；
    - fix 无修法 / 复审仍 fix → review-human（人工介入：--redo 或 --approve-review）；
    - pass               → 仅当返回结构合法、verdict 精确等于 pass、且确定性检查为空。
    审查看的是与实际渲染同源的分配表（assignment），复用图明确标注。
    """
    secs = doc["sections"]
    assignment = [(a[0], a[1]) for a in
                  ((job.stage("images") or {}).get("assignment") or [])] or \
        [(i, None) for i in range(len(secs))]
    img_titles = []
    for i, (pi, reused) in enumerate(assignment):
        t = pool_imgs[pi % len(pool_imgs)]["title"].replace("File:", "")[:60]
        img_titles.append(t + (f"（复用第{reused}段配图）" if reused is not None else ""))

    def _key(title: str) -> str:
        return jobs_mod.cache_key(
            v=V_REVIEW, title=title,
            text=json.dumps([s["text"] for s in secs], ensure_ascii=False),
            imgs=img_titles, model=cfg.get("review_model", "glm-4-flash"))

    cached = job.stage("review") or {}
    if job.reusable("review", _key(doc["title"])):
        log.info("审查结果复用缓存: verdict=%s", cached.get("verdict"))
        return cached

    def _human(reason: str, problems: list, details: list, key: str):
        job.manifest["stages"]["review"] = {
            "status": "needs-human", "key": key, "verdict": "fix",
            "problems": problems[:20], "details": details[:20]}
        job.save()
        raise HumanGate(reason)      # store 的 review-human 由 _pipeline 统一标记

    job.mark_running("review", _key(doc["title"]))
    verdict, details = None, []
    title = doc["title"]
    try:
        for round_no in (1, 2):
            v = review_mod.review(
                os.environ.get("ZHIPU_API_KEY", ""), title, secs[0]["text"][:120],
                secs, img_titles, model=cfg.get("review_model", "glm-4-flash"))
            verdict, details = v, v.get("details", [])
            if v["verdict"] == "fix" and v.get("title_fix") and round_no == 1:
                # fix 必须真的修正再复审；改题只允许一轮，防止无限改
                title = str(v["title_fix"]).strip().strip('"「」')
                log.info("🔍 审查官改题并复审: %s → %s", doc["title"], title)
                continue
            break
    except review_mod.ReviewUnavailable as e:
        job.mark_failed("review", e)
        raise StageError("review", f"{e}（等待重试，不自动放行）") from e
    except Exception as e:
        job.mark_failed("review", e)
        raise StageError("review", e) from e

    flat = verdict["problems"]
    for p in flat:
        log.warning("🔍 审查: %s", p)

    if verdict["verdict"] == "reject":
        job.mark_failed("review", "审查官否决")
        raise SystemExit(2)          # store 的 review-rejected 由 _pipeline 统一标记
    if verdict["verdict"] == "fix":
        # 复审后仍 fix（或首轮 fix 无修法）：人工介入，不放行。
        # 有改题先落盘——保证 --approve-review 后的缓存键与放行时一致
        if verdict.get("title_fix"):
            title = str(verdict["title_fix"]).strip().strip('"「」')
            log.info("🔍 保留审查官改题（待人工确认）: %s", title)
        if title != doc["title"]:
            doc["title"] = title
            Path(job.path("script.json")).write_text(
                json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
            job.set_title(title)
        _human("审查要求修正但未能自动通过——需人工判断", flat, details,
               _key(title))
        return {}                    # 不可达（_human 必抛）

    # verdict == pass：应用可能的一轮改题并落盘
    if title != doc["title"]:
        doc["title"] = title
        Path(job.path("script.json")).write_text(
            json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        job.set_title(title)
    key = _key(doc["title"])
    job.manifest["stages"]["review"] = {
        "status": "done", "key": key, "verdict": "pass",
        "severity": verdict.get("severity", "low"), "details": details}
    job.save()
    return {"verdict": "pass", "problems": flat, "details": details,
            "title_fix": "", "key": key}


def _voice_key(sec_text: str, narr: dict, voice: str, zhipu_cfg) -> str:
    return jobs_mod.cache_key(
        v=V_TTS, text=sec_text, voice=voice, rate=narr["rate"],
        backend=narr.get("backend", "edge"),
        cosy={k: narr.get("cosy", {}).get(k)
              for k in ("enable", "mode", "prompt_wav", "instruct")}
        if narr.get("cosy") else None,
        zhipu_model=(zhipu_cfg or {}).get("model"))


def stage_narration(job, cfg, log) -> list:
    """④ 配音：按段缓存——文本/音色/后端没变的段直接复用音频文件。

    cosy 本地大模型整批加载，仅补缺段（narrator 支持 indices）。
    返回 [{path, duration, text, key}]。
    """
    doc = json.loads(Path(job.path("script.json")).read_text(encoding="utf-8"))
    secs = doc["sections"]
    narr = cfg["narrator"]
    pool = narr.get("voices") or [narr["voice"]]
    voice = pool[datetime.now().timetuple().tm_yday % len(pool)]
    log.info("本期音色: %s（池%d个，按天轮换）", voice, len(pool))
    zhipu_cfg = {**cfg.get("zhipu_tts", {}), "api_key": os.environ.get("ZHIPU_API_KEY", "")} \
        if narr.get("backend") == "zhipu" else None

    keys = [_voice_key(s["text"], narr, voice, zhipu_cfg) for s in secs]
    out_dir = job.path("narration")
    prev = (job.stage("narration") or {}).get("items") or []
    voices, missing = [], []
    for i, k in enumerate(keys):
        ext = ".wav" if (narr.get("cosy") or {}).get("enable") else ".mp3"
        p = Path(out_dir) / f"sec_{i:02d}{ext}"
        if i < len(prev) and prev[i] == k and p.exists():
            voices.append({"path": str(p), "key": k, "text": secs[i]["text"]})
        else:
            missing.append(i)
            voices.append({"path": str(p), "key": k, "text": secs[i]["text"]})
    if not missing:
        log.info("配音全部复用缓存（%d段）", len(voices))
    else:
        stage_key = jobs_mod.cache_key(v=V_TTS, keys=keys)
        job.mark_running("narration", stage_key)
        # 进度即时持久化：逐段补缺时每段完成就写 manifest（崩溃不丢已完成段）
        saved_items = list(prev) + [None] * max(0, len(keys) - len(prev))
        try:
            if (narr.get("cosy") or {}).get("enable"):
                narrator._synth_cosy_batch(
                    [secs[i] for i in missing], out_dir, narr["cosy"],
                    indices=missing)
                for i in missing:
                    saved_items[i] = keys[i]
            else:
                for i in missing:
                    narrator.synth_section(
                        secs[i]["text"], str(Path(out_dir) / f"sec_{i:02d}.mp3"),
                        voice, narr["rate"], backend=narr.get("backend", "edge"),
                        zhipu=zhipu_cfg,
                        allow_sapi=bool(narr.get("allow_sapi_fallback", False)))
                    saved_items[i] = keys[i]
                    job.manifest["stages"]["narration"]["items"] = saved_items
                    job.save()
        except Exception as e:
            job.manifest["stages"]["narration"]["items"] = saved_items
            job.save()
            job.mark_failed("narration", e)
            raise StageError("narration", e) from e
        # 实际落盘后缀可能与计划不同（piper/sapi 产 wav），按存在文件校正
        for i in missing:
            cand = [Path(out_dir) / f"sec_{i:02d}{e}" for e in (".mp3", ".wav")]
            got = next((c for c in cand if c.exists()), None)
            if got is None:
                job.mark_failed("narration", f"第{i}段缺失")
                raise StageError("narration", f"配音第{i}段缺失（合成静默失败）")
            voices[i]["path"] = str(got)

    for v in voices:
        v["duration"] = narrator.audio_duration(v["path"])
    total = sum(v["duration"] for v in voices)
    if total <= 0:
        job.mark_failed("narration", "全部配音时长为0")
        raise StageError("narration", "配音时长异常（全为0），检查音频文件")
    (Path(out_dir) / "index.json").write_text(
        json.dumps([{k: v[k] for k in ("path", "duration", "text", "key")}
                    for v in voices], ensure_ascii=False, indent=1), encoding="utf-8")
    job.mark_done("narration", jobs_mod.cache_key(v=V_TTS, keys=keys),
                  {"index": "narration/index.json"}, extra={"items": keys})
    log.info("配音完成: %d段 共%.1f秒 (backend=%s, 复用%d段)", len(voices), total,
             narr.get("backend", "edge"), len(keys) - len(missing))
    return voices


def _img_hash(p: str) -> str:
    return hashlib.sha1(Path(p).read_bytes()).hexdigest()[:10] \
        if Path(p).exists() else "missing"


def stage_clips(job, voices, pool_imgs, cfg, log) -> tuple[list, list]:
    """⑤ 下载配图 + 逐段渲染：单段失败只重做该段（其余复用）。
    返回 (段落成片路径列表, 结构化素材台账 assets)。

    台账逐段记录：本段文件、来源标题/页面、许可、作者、检索词、
    是否复用自第几段（方案 P1-5：每段旁白可追溯到具体素材来源）。
    """
    doc = json.loads(Path(job.path("script.json")).read_text(encoding="utf-8"))
    secs = doc["sections"]
    fade = float(cfg.get("transition", {}).get("fade_seconds", 0.6))
    modes = ["in", "panright", "out", "panleft"]
    from src.sticker import resolve_stickers

    assignment = [(a[0], a[1]) for a in
                  ((job.stage("images") or {}).get("assignment") or [])] or \
        [(i, None) for i in range(len(secs))]

    # 素材落盘（幂等）：同一张池图只下载一次（src_池号），按段复制（img_段号）
    assets, imgs = [], []
    try:
        for i, (pi, reused) in enumerate(assignment):
            im = pool_imgs[pi % len(pool_imgs)]
            ext = Path(_up(im["url"]).path).suffix.lower() or ".jpg"
            src_p = Path(job.path("materials", f"src_{pi:02d}{ext}"))
            if not src_p.exists():
                wikiimg.download_image(im["url"], str(src_p))
            p = Path(job.path("materials", f"img_{i:02d}{ext}"))
            if not p.exists():
                shutil.copyfile(src_p, p)
            assets.append({
                "section": i, "file": f"materials/{p.name}",
                "source_title": im["title"], "source_url": im.get("url", ""),
                "license": im.get("license", "?"), "author": im.get("author", "?"),
                "query": im.get("query", ""),
                "reused_from": reused})
            imgs.append(str(p))
    except Exception as e:
        job.mark_failed("clips", f"素材下载: {e}")
        raise StageError("clips", f"素材下载失败: {e}") from e
    (Path(job.path("materials")) / "assets.json").write_text(
        json.dumps(assets, ensure_ascii=False, indent=1), encoding="utf-8")

    # 单段键：图内容+音频+运镜+时长+贴纸 → 变了的段才重渲染
    plans = []
    for i in range(len(secs)):
        im = pool_imgs[i % len(pool_imgs)]
        v = voices[i]
        tail = 0.0 if i == len(secs) - 1 else fade
        v["tail"] = tail
        stickers = resolve_stickers(secs[i].get("stickers"), v["duration"])
        plans.append({
            "i": i, "img": imgs[i], "v": v, "tail": tail,
            "mode": modes[i % 4], "stickers": stickers, "im": im,
            "key": jobs_mod.cache_key(
                v=V_CLIP, img=_img_hash(imgs[i]), voice=v["key"],
                duration=round(v["duration"] + tail, 2), mode=modes[i % 4],
                stickers=[(s["path"], round(s["at"], 2)) for s in stickers]),
        })

    prev_items = (job.stage("clips") or {}).get("items") or []
    todo = [pl["i"] for pl in plans
            if not (pl["i"] < len(prev_items)
                    and prev_items[pl["i"]] == pl["key"]
                    and Path(job.path("clips", f"sec_{pl['i']:02d}.mp4")).exists())]
    log.info("段落渲染: %d段需做 / %d段复用", len(todo), len(secs) - len(todo))
    job.mark_running("clips", jobs_mod.cache_key(
        v=V_CLIP, voices=[v["key"] for v in voices], n=len(secs)))
    if todo:
        from concurrent.futures import ThreadPoolExecutor
        log.info("并行渲染 %d 段（4 workers）...", len(todo))
        # 进度即时持久化：任一段完成就写 manifest，中途崩溃/失败后重跑只补缺段
        saved_items = list(prev_items) + [None] * max(0, len(secs) - len(prev_items))
        with ThreadPoolExecutor(max_workers=4) as pool:
            futs = {}
            for i in todo:
                pl = plans[i]
                futs[pool.submit(
                    slideshow.build_section_clip, pl["img"], pl["v"]["path"],
                    pl["v"]["duration"] + pl["tail"],
                    job.path("clips", f"sec_{i:02d}.mp4"),
                    mode=pl["mode"], stickers=pl["stickers"])] = i
            # 先收集全部结果再报错：失败的段落之后的已完成段落也入账，
            # 重跑时不必重渲染（并行兄弟段落常常已经做完）
            first_err = None
            for fut, i in futs.items():              # 按提交序收取
                try:
                    fut.result()
                except Exception as e:
                    if first_err is None:
                        first_err = (i, e)
                    continue
                saved_items[i] = plans[i]["key"]
                job.manifest["stages"]["clips"]["items"] = saved_items
                job.save()
                pl = plans[i]
                log.info("段落%d %.1fs(+%.1f尾) 运镜=%s 配图=%s",
                         i, pl["v"]["duration"], pl["tail"], pl["mode"],
                         pl["im"]["title"][5:40])
        if first_err is not None:
            i, e = first_err
            job.mark_failed("clips", f"第{i}段: {e}")
            raise StageError("clips", f"第{i}段渲染失败: {e}") from e
    job.mark_done("clips", extra={"items": [pl["key"] for pl in plans]})
    return [job.path("clips", f"sec_{i:02d}.mp4") for i in range(len(secs))], assets


def stage_compose(job, section_files, voices, cfg, log) -> tuple[str, str, str]:
    """⑥ 合成：xfade 转场轮换 + BGM ducking + 字幕烧录。
    BGM 选择入 manifest，断点重跑不换曲。返回 (final, srt, bgm)。"""
    bgms = sorted((ROOT / cfg["bgm_dir"]).glob("*.mp3"))
    if not bgms:
        raise StageError("compose", f"BGM 目录无 mp3: {cfg['bgm_dir']}")
    stage = job.stage("compose") or {}
    bgm_path = stage.get("bgm") if stage.get("bgm") in [str(b) for b in bgms] \
        else str(random.choice(bgms))
    trans_cfg = cfg.get("transition", {})
    fade = float(trans_cfg.get("fade_seconds", 0.6))
    subtitle_style = cfg.get("subtitle", {}).get("force_style") or None
    key = jobs_mod.cache_key(
        v=V_COMPOSE, clips=[Path(f).name for f in section_files],
        bgm=Path(bgm_path).name, bgm_volume=cfg.get("bgm_volume", 0.22),
        fade=fade,
        transitions=trans_cfg.get("styles"), subtitle_style=subtitle_style,
        durations=[round(v["duration"] + v.get("tail", 0), 2) for v in voices])
    final = job.path("final.mp4")
    if job.reusable("compose", key) and Path(final).exists():
        log.info("成片复用缓存: %s", final)
        srt = job.path("final.srt")
        if not Path(srt).exists():
            slideshow.make_srt(voices, srt)
        return final, srt, bgm_path

    job.mark_running("compose", key)
    try:
        srt = slideshow.make_srt(voices, job.path("final.srt"))
        slideshow.compose_final(section_files, bgm_path, srt, final,
                                bgm_volume=float(cfg.get("bgm_volume", 0.22)),
                                fade=fade,
                                transitions=trans_cfg.get("styles"),
                                subtitle_style=subtitle_style)
    except Exception as e:
        job.mark_failed("compose", e)
        raise StageError("compose", e) from e
    job.mark_done("compose", key, {"final": "final.mp4", "srt": "final.srt"},
                  extra={"bgm": bgm_path})
    log.info("成片: %s (%.1f MB)", final, os.path.getsize(final) / 1e6)
    return final, srt, bgm_path


def stage_cover(job, final, log) -> str:
    cover = job.path("cover.jpg")
    st = job.stage("cover") or {}
    if st.get("status") == "done" and Path(cover).exists():
        return cover
    from src.cover import extract_cover
    job.mark_running("cover")
    extract_cover(final, cover, at_second=2)
    job.mark_done("cover", outputs={"cover": "cover.jpg"})
    log.info("封面: %s", cover)
    return cover


def stage_qc(job, topic, final, srt, voices, cfg, log) -> None:
    """⑦ 自动质检：不合格置 failed(qc)，绝不带病投稿。"""
    stage = job.stage("qc") or {}
    if stage.get("status") == "done" and \
            stage.get("final_size") == Path(final).stat().st_size:
        log.info("质检结果复用（成片未变化）")
        return
    job.mark_running("qc")
    narr_total = sum(v["duration"] for v in voices)
    ok, problems = qc_mod.qc_video(final, srt, job.path("cover.jpg"),
                                   cfg["video"], thresholds=cfg.get("qc"),
                                   narration_total=narr_total)
    if not ok:
        job.mark_failed("qc", "; ".join(problems))
        raise StageError("qc", f"质检未通过: {'; '.join(problems)}")
    job.mark_done("qc", extra={"final_size": Path(final).stat().st_size,
                               "narration_total": round(narr_total, 1)})
    log.info("🧪 质检通过（旁白总长%.1fs）", narr_total)


def _desc_from_assets(title: str, doc: dict, assets: list) -> str:
    quotes = []
    for sec in doc["sections"]:
        q = sec.get("quote")
        if q:
            quotes.append(f"“{q.get('text', '')}”——{q.get('author', '')}"
                          f"（{q.get('context', '')}）" if q.get("context")
                          else f"“{q.get('text', '')}”——{q.get('author', '')}")
    seen, credits = set(), []
    for a in assets:
        st = a["source_title"].replace("File:", "")
        if st in seen:
            continue
        seen.add(st)
        credits.append(f"{st}（{a['license']}, {a['author']}）")
    desc = (f"{title}\n\n本片由 AI 辅助制作：文案/配音/剪辑自动化流水线，"
            f"史实内容欢迎评论区指正。\n图片来源（Wikimedia Commons）："
            + "；".join(credits))
    if quotes:
        desc += "\n\n名言引用：\n" + "\n".join(dict.fromkeys(quotes))
    desc += "\n贴纸: Twemoji (CC BY 4.0)\nBGM: Kevin MacLeod (incompetech.com), CC BY 4.0"
    return desc


def stage_upload(job, store, topic, doc, cfg, final, cover, assets, log,
                 dry_run: bool, confirm_upload: bool = False) -> None:
    """⑧ 投稿：复用成片与封面；失败分类入库；已上传过直接跳过（防重复投稿）。

    上次超时（publish.status=uncertain）→ 拒绝自动重投：客户端没收到成功响应
    不等于平台没收到视频，必须人工核实后 --confirm-upload 才重试（方案 P1-1）。
    """
    desc = _desc_from_assets(doc["title"], doc, assets)
    tags = list(dict.fromkeys(doc["tags"] + cfg["bilibili"]["tags_extra"]))[:10]
    job.set_publish(title=doc["title"], desc=desc, tags=tags,
                    final=final, cover=cover)

    if dry_run:
        log.info("[DRY-RUN] 成片: %s\n标题: %s\n简介:\n%s\n标签: %s",
                 final, doc["title"], desc, tags)
        store.mark_dryrun(topic, doc["title"], job_id=job.job_id)
        return

    if (job.stage("upload") or {}).get("status") == "done" or \
            job.manifest["publish"].get("bvid"):
        log.info("该任务已投稿（%s），跳过重复上传",
                 job.manifest["publish"].get("bvid"))
        return
    if job.manifest["publish"].get("status") == "uncertain" and not confirm_upload:
        raise StageError(
            "upload",
            "上次投稿超时，平台状态未核实——盲目重投有重复投稿风险。"
            "先到B站创作中心确认：未收到 → python autopost.py --confirm-upload "
            f"{cfg['topics'].index(topic) + 1} 重投；已收到 → "
            "python autopost.py --mark-uploaded <序号> --bvid BV号")

    job.mark_running("upload")
    try:
        out = sau_bridge.upload(
            video_path=final, title=doc["title"], desc=desc, tags=tags,
            tid=cfg["bilibili"]["tid"], cover=cover, source_url="",
            copyright_self=True)
    except sau_bridge.RateLimited as e:
        job.manifest["publish"]["status"] = "rate_limited"
        job.manifest["publish"]["fail_reason"] = str(e)[:300]
        job.mark_failed("upload", e)
        raise StageError("upload", f"{e}（成片保留，冷却后重跑自动续投）") from e
    except sau_bridge.UploadError as e:
        uncertain = (e.code == -1)   # 超时：平台状态未核实，盲目重投有重复投稿风险
        job.manifest["publish"]["status"] = "uncertain" if uncertain else "failed"
        job.manifest["publish"]["fail_reason"] = str(e)[:300]
        job.mark_failed("upload", e)
        raise StageError("upload",
                         f"{e}" + ("（状态未核实，先去B站确认再重跑！）"
                                   if uncertain else "")) from e
    except Exception as e:
        job.mark_failed("upload", e)
        raise StageError("upload", e) from e

    bvid_m = re.search(r"BV[0-9A-Za-z]{10}", out)
    bvid = bvid_m.group(0) if bvid_m else ""
    job.set_publish(status="published", bvid=bvid,
                    uploaded_at=datetime.now().isoformat(timespec="seconds"))
    job.mark_done("upload", outputs={"bvid": bvid})
    store.mark_published(topic, doc["title"], job_id=job.job_id)
    if bvid:
        store.record_video(bvid, doc["title"], "军史")
        log.info("投稿成功: %s（资产入库）", bvid)
    else:
        log.info("投稿成功（输出未解析到BV号）: %s", out[-200:])
    try:
        from src import stats as stats_mod
        stats_mod.collect_and_report(
            store, int(cfg["bilibili"].get("mid", 1518025639)), log)
    except Exception as e:
        log.warning("战报生成异常(不影响发布): %s", e)


# ── 主流程 ────────────────────────────────────────────────

def run(dry_run: bool, topic_index: int | None, redo: bool = False,
        fresh: bool = False, publish_only: bool = False,
        approve_review: bool = False, confirm_upload: bool = False,
        mark_uploaded: bool = False, bvid: str = ""):
    log = logging.getLogger("main")
    load_dotenv(ROOT / ".env")
    cfg = load_config()
    account_file = _account_file(cfg)
    publishing = not dry_run
    preflight.preflight_or_die(cfg, ROOT, publishing=publishing,
                               account_file=account_file, log=log)
    if not dry_run:
        sau_bridge.configure(account_file)
    store = Store(str(ROOT / DATA_DIR / "topics.db"))

    lock = InstanceLock(ROOT / ".autopost.lock")
    if not lock.acquire(log):
        log.warning("另一实例运行中（.autopost.lock），本次退出")
        return
    try:
        if publish_only:
            _publish_only(store, cfg, topic_index, log)
        elif mark_uploaded:
            _mark_uploaded(store, cfg, topic_index, bvid, log)
        else:
            _pipeline(store, cfg, topic_index, log, dry_run, redo, fresh,
                      approve_review=approve_review,
                      confirm_upload=confirm_upload, lock=lock)
    finally:
        lock.release()


def _pick_topic(store, cfg, topic_index, redo, log) -> str | None:
    topics = cfg["topics"]
    if topic_index is not None:
        if not 1 <= topic_index <= len(topics):
            log.error("主题序号超出范围: %d（共%d个）", topic_index, len(topics))
            return None
        topic = topics[topic_index - 1]
        if redo:
            store.reset_topic(topic)
        else:
            row = store.get_topic(topic)
            if row and row["status"] in BLOCKING:
                log.warning("选题状态 %s（%s），如需重做: python autopost.py --redo %d",
                            row["status"],
                            (row["stage"] or row["fail_reason"])[:60], topic_index)
                return None
        return topic
    claimed = store.select_and_claim(topics)
    if claimed is None:
        log.warning("主题池已全部做完/锁定，请在 config.yaml 中补充新主题")
        return None
    topic, prev = claimed
    if prev != "pending":
        log.info("重新领取选题（原状态 %s）", prev)
    return topic


def _pipeline(store, cfg, topic_index, log, dry_run, redo, fresh,
              approve_review=False, confirm_upload=False, lock=None):
    topics = cfg["topics"]
    if approve_review:
        if topic_index is None:
            log.error("--approve-review 需要主题序号")
            return
        topic = topics[topic_index - 1]
        job = jobs_mod.Job.find_resumable(JOBS_DIR, topic)
        if job is None or (job.stage("review") or {}).get("status") != "needs-human":
            log.warning("该题没有待人工审核的审查记录")
            return
        # 人工放行 = 把审查结论置为 pass（带人工标记），后续阶段照常走缓存
        job.manifest["stages"]["review"] = {
            "status": "done", "key": (job.stage("review") or {}).get("key", ""),
            "verdict": "pass", "approved_by": "manual",
            "severity": "low", "details": []}
        job.save()
        log.info("🔍 人工放行审查（《%s》），继续制作", topic)
        store.mark_generating(topic, job_id=job.job_id)
    else:
        topic = _pick_topic(store, cfg, topic_index, redo, log)
        if topic is None:
            return
        log.info("本期主题: %s", topic)
        job = None if fresh else jobs_mod.Job.find_resumable(JOBS_DIR, topic)
        if job is None:
            job = jobs_mod.Job.create(JOBS_DIR, topic)
            log.info("新建任务: %s", job.dir.name)
        else:
            log.info("续跑任务: %s", job.dir.name)
        store.mark_generating(topic, job_id=job.job_id)

    def _hb():
        job.heartbeat()          # manifest 心跳
        if lock:
            lock.heartbeat()     # 锁 mtime 心跳：挂死判定依据
    _hb()

    doc = None
    verdict, assets = None, []
    try:
        doc = stage_script(job, topic, cfg, log)
        _hb()
        pool_imgs = stage_images(job, doc["sections"], cfg, log)
        _hb()
        verdict = stage_review(job, topic, doc, pool_imgs, cfg, log)
        _hb()
        voices = stage_narration(job, cfg, log)
        _hb()
        section_files, assets = stage_clips(job, voices, pool_imgs, cfg, log)
        _hb()
        final, srt, _bgm = stage_compose(job, section_files, voices, cfg, log)
        cover = stage_cover(job, final, log)
        stage_qc(job, topic, final, srt, voices, cfg, log)
    except SystemExit:
        # 审查否决：store 状态在这里标记（stage_review 只管 manifest）
        store.mark_review_rejected(topic, (doc or {}).get("title", ""),
                                   job_id=job.job_id)
        log.error("🔍 审查官否决，本期不发布（宁缺毋滥）")
        return
    except HumanGate as e:
        store.mark_review_human(topic, (doc or {}).get("title", ""),
                                job_id=job.job_id)
        n = topics.index(topic) + 1
        log.warning("🔍 %s\n  处理: python autopost.py --redo %d 重做；"
                    "或阅读 manifest 里的问题清单后 "
                    "python autopost.py --approve-review %d 放行",
                    e, n, n)
        return
    except StageError as e:
        store.mark_failed(topic, (doc or {}).get("title", ""),
                          stage=e.stage, reason=e.reason[:200], job_id=job.job_id)
        log.error("本期中断于 %s 阶段: %s（重跑将自动续走）", e.stage, e.reason[:150])
        return

    # 高危审查问题 → 停在 ready 等人工核实，不自动发布（方案 P1-4 第三层）
    if any(d.get("severity") == "high" for d in (verdict or {}).get("details", [])):
        store.mark_ready(topic, doc["title"], job_id=job.job_id)
        log.warning("🔍 审查发现高危问题待人工核实——成片保留在 %s，"
                    "核实无误后运行: python autopost.py --publish-only %d",
                    job.dir, topics.index(topic) + 1)
        return

    store.mark_ready(topic, doc["title"], job_id=job.job_id)
    try:
        stage_upload(job, store, topic, doc, cfg, final, cover, assets, log,
                     dry_run, confirm_upload=confirm_upload)
    except StageError as e:
        store.mark_failed(topic, doc["title"], stage=e.stage,
                          reason=e.reason[:200], job_id=job.job_id)
        log.error("投稿未完成（%s）: %s", e.stage, e.reason[:150])
        return
    if not dry_run and cfg.get("douyin", {}).get("enable") and \
            (job.stage("upload") or {}).get("status") == "done":
        try:
            import douyin_bridge
            tag_str = " ".join(f"#{t}" for t in
                               (job.manifest["publish"].get("tags") or [])[:4])
            ok = douyin_bridge.upload(final, f"{doc['title']} {tag_str}")
            log.info("抖音分发: %s", "成功" if ok else "失败(见data/截图)")
        except Exception as e:
            log.warning("抖音分发异常(不影响B站): %s", e)


def _publish_only(store, cfg, topic_index, log):
    """只投稿：找已过质检未发布的任务（人工核实后的发布通道）。"""
    jobs_root = Path(JOBS_DIR)
    candidates = []
    if jobs_root.is_dir():
        for d in sorted(jobs_root.iterdir(), reverse=True):
            try:
                m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            st = m.get("stages") or {}
            if st.get("qc", {}).get("status") != "done" or \
                    st.get("upload", {}).get("status") == "done" or \
                    m.get("publish", {}).get("bvid"):
                continue
            if topic_index is not None and \
                    m.get("topic") != cfg["topics"][topic_index - 1]:
                continue
            candidates.append(d)
    if not candidates:
        log.warning("没有待投稿的已质检任务")
        return
    d = candidates[0]
    job = jobs_mod.Job(d)
    topic = job.manifest["topic"]
    log.info("投稿任务 %s：《%s》", d.name, job.manifest.get("title"))
    doc = json.loads((d / "script.json").read_text(encoding="utf-8"))
    assets = json.loads((d / "materials" / "assets.json").read_text(encoding="utf-8"))
    store.mark_generating(topic, job_id=job.job_id)
    try:
        stage_upload(job, store, topic, doc, cfg, job.path("final.mp4"),
                     job.path("cover.jpg"), assets, log, dry_run=False)
    except StageError as e:
        store.mark_failed(topic, doc["title"], stage=e.stage,
                          reason=e.reason[:200], job_id=job.job_id)
        log.error("投稿未完成（%s）: %s", e.stage, e.reason[:150])
        return


def _mark_uploaded(store, cfg, topic_index, bvid: str, log):
    """人工确认平台已收到投稿（超时未核实场景）后，把任务标记为已发布。
    不重新上传、不重新生成任何内容。"""
    if topic_index is None:
        log.error("--mark-uploaded 需要主题序号（可附 --bvid BV号）")
        return
    topic = cfg["topics"][topic_index - 1]
    job = jobs_mod.Job.find_resumable(JOBS_DIR, topic)
    if job is None:
        log.warning("该题没有未完结的任务")
        return
    doc = json.loads(Path(job.path("script.json")).read_text(encoding="utf-8"))
    job.set_publish(status="published", bvid=bvid or "",
                    confirmed_by="manual",
                    uploaded_at=datetime.now().isoformat(timespec="seconds"))
    job.mark_done("upload", outputs={"bvid": bvid or ""})
    store.mark_published(topic, doc["title"], job_id=job.job_id)
    if bvid:
        store.record_video(bvid, doc["title"], "军史")
    log.info("已标记为已投稿%s：%s", f"（{bvid}）" if bvid else "", doc["title"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--redo", action="store_true", help="重置指定主题状态并重做")
    ap.add_argument("--fresh", action="store_true", help="连同产物缓存一起废弃")
    ap.add_argument("--publish-only", action="store_true",
                    help="只投稿已过质检的成片（人工核实后）")
    ap.add_argument("--approve-review", action="store_true",
                    help="人工放行待审核的讲稿（配合主题序号）")
    ap.add_argument("--confirm-upload", action="store_true",
                    help="人工核实平台未收到上次投稿后，允许重投（配合主题序号）")
    ap.add_argument("--mark-uploaded", action="store_true",
                    help="人工核实平台已收到投稿，直接标记完成（配合主题序号与 --bvid）")
    ap.add_argument("--bvid", default="", help="--mark-uploaded 时填平台BV号")
    ap.add_argument("topic", nargs="?", type=int, help="主题序号(从1)")
    args = ap.parse_args()
    setup_logging()
    run(args.dry_run, args.topic, redo=args.redo, fresh=args.fresh,
        publish_only=args.publish_only, approve_review=args.approve_review,
        confirm_upload=args.confirm_upload, mark_uploaded=args.mark_uploaded,
        bvid=args.bvid)
