"""重投被风控拦下的成片（output/pending/ 里备好的 final+cover+manifest）。

用法：python upload_pending.py [winterwar|spitfire|all]
B站 21566「投稿过于频繁」冷却后，用户叫发时跑这个，不重新制作。
"""
import json, sys
from pathlib import Path
import yaml

import sau_bridge

PENDING = Path("output/pending")


def upload_one(tag: str) -> bool:
    m = json.loads((PENDING / f"{tag}_manifest.json").read_text(encoding="utf-8"))
    video = str(PENDING / f"{tag}_final.mp4")
    cover = str(PENDING / f"{tag}_cover.jpg")
    if not Path(video).exists():
        print(f"[{tag}] 缺成片: {video}")
        return False
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    out = sau_bridge.upload(
        video_path=video, title=m["title"], desc=m["desc"],
        tags=m.get("tags") or cfg["bilibili"]["tags"],
        tid=cfg["bilibili"]["tid"], cover=cover, source_url="",
        copyright_self=True)
    import re
    bv = re.search(r"BV[0-9A-Za-z]{10}", out or "")
    print(f"[{tag}] 投稿成功 {bv.group(0) if bv else out[:80]}")
    return True


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    tags = [which] if which != "all" else [
        p.name.split("_")[0] for p in sorted(PENDING.glob("*_manifest.json"))]
    for t in tags:
        try:
            if upload_one(t):
                (PENDING / f"{t}_manifest.json").rename(
                    PENDING / f"{t}_manifest.uploaded")
        except Exception as e:
            print(f"[{t}] 失败: {str(e)[:200]}")
