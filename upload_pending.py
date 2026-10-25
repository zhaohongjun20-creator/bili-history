"""重投被风控拦下的成片（output/pending/ 里备好的 final+cover+manifest）。

用法：python upload_pending.py [winterwar|spitfire|all]
B站 21566「投稿过于频繁」冷却后，用户叫发时跑这个，不重新制作。

可靠性（整改 P1）：
- 重投前校验成片/封面/manifest 完整性与大小，缺一项不投；
- 已重投过（*_manifest.uploaded）或选题已 published 的直接跳过——防重复重投；
- 投稿成功后状态回写数据库（published + BV 入库）。
"""
import json, re, sys
from pathlib import Path
import yaml

import sau_bridge

PENDING = Path("output/pending")

MIN_VIDEO_BYTES = 1_000_000     # 正常成片远大于此；防半截文件
MIN_COVER_BYTES = 10_000


def _validate(tag: str) -> tuple[dict, str, str]:
    """完整性校验，返回 (manifest, video, cover)；不合格抛 ValueError。"""
    m_path = PENDING / f"{tag}_manifest.json"
    if not m_path.exists():
        raise ValueError(f"缺 manifest: {m_path}")
    m = json.loads(m_path.read_text(encoding="utf-8"))
    for k in ("title", "desc"):
        if not m.get(k):
            raise ValueError(f"manifest 缺字段: {k}")
    video = PENDING / f"{tag}_final.mp4"
    cover = PENDING / f"{tag}_cover.jpg"
    if not video.exists() or video.stat().st_size < MIN_VIDEO_BYTES:
        raise ValueError(f"成片缺失或过小: {video}")
    if not cover.exists() or cover.stat().st_size < MIN_COVER_BYTES:
        raise ValueError(f"封面缺失或过小: {cover}")
    return m, str(video), str(cover)


def upload_one(tag: str) -> bool:
    if (PENDING / f"{tag}_manifest.uploaded").exists():
        print(f"[{tag}] 已重投过（manifest.uploaded），跳过——防重复重投")
        return False
    try:
        m, video, cover = _validate(tag)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"[{tag}] 完整性校验不通过: {e}")
        return False

    from src.store import Store
    store = Store("data/topics.db")
    topic = m.get("topic", m["title"])
    row = store.get_topic(topic)
    if row and row["status"] == "published":
        print(f"[{tag}] 选题已 published（{row['updated_at']}），跳过——防重复重投")
        return False

    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    out = sau_bridge.upload(
        video_path=video, title=m["title"], desc=m["desc"],
        tags=m.get("tags") or cfg["bilibili"]["tags_extra"],
        tid=cfg["bilibili"]["tid"], cover=cover, source_url="",
        copyright_self=True)
    bv = re.search(r"BV[0-9A-Za-z]{10}", out or "")
    print(f"[{tag}] 投稿成功 {bv.group(0) if bv else out[:80]}")
    # 状态回写：旧通道投稿成功也要入库，否则下次自动运行会把同题整期重做再投（重复投稿）
    store.mark_published(topic, m["title"])
    if bv:
        store.record_video(bv.group(0), m["title"], "军史")
    (PENDING / f"{tag}_manifest.json").rename(
        PENDING / f"{tag}_manifest.uploaded")
    return True


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    tags = [which] if which != "all" else [
        p.name.split("_")[0] for p in sorted(PENDING.glob("*_manifest.json"))]
    for t in tags:
        try:
            upload_one(t)
        except Exception as e:
            print(f"[{t}] 失败: {str(e)[:200]}")
