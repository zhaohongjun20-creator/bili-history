"""表情贴纸：Twemoji（Twitter 开源彩色 emoji，CC-BY 4.0）下载缓存。

贴纸时机由 GLM 在讲稿里自标注（at=段内相对时间 0-1），实现"AI 自行判断加梗"。
overlay 编排见 slideshow.build_section_clip。
"""
import time
from pathlib import Path
import requests

CACHE_DIR = Path(__file__).parent.parent / "imgs" / "stickers"
CDN = "https://cdn.jsdelivr.net/gh/jdecked/twemoji@15.1.0/assets/72x72"
SHOW_SECONDS = 2.6
_last = 0.0


def get_sticker(emoji: str) -> Path | None:
    """emoji 字符 → 本地缓存 PNG（Twemoji 72px，CC-BY 4.0）；失败返回 None。"""
    if not emoji or len(emoji) > 8:
        return None
    name = "-".join(f"{ord(c):x}" for c in emoji if ord(c) != 0xFE0F)
    cache = CACHE_DIR / f"{name}.png"
    if cache.exists():
        return cache
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    global _last
    for _ in range(2):
        wait = 0.4 - (time.time() - _last)
        if wait > 0:
            time.sleep(wait)
        _last = time.time()
        try:
            r = requests.get(f"{CDN}/{name}.png", timeout=30)
            if r.status_code == 200 and r.content[:4] == b"\x89PNG":
                cache.write_bytes(r.content)
                return cache
        except requests.RequestException:
            continue
    return None


def resolve_stickers(stickers: list, section_duration: float) -> list:
    """把 GLM 标注的 [{emoji, at}] 解析为可 overlay 的描述（下载失败即跳过）。"""
    out = []
    for st in (stickers or [])[:2]:   # 每段最多 2 个
        path = get_sticker(str(st.get("emoji", "")).strip())
        if not path:
            continue
        at = max(0.0, min(section_duration - SHOW_SECONDS,
                          float(st.get("at", 0.5)) * section_duration))
        out.append({"path": str(path), "at": at})
    return out
