"""Wikimedia Commons 史料图搜索下载：仅接受 Public domain / CC 许可，返回署名元数据。

含速率节流（请求间隔 ≥2s）与 429 退避重试，符合 Wikimedia API 礼仪。
"""
import re, time
from pathlib import Path
from urllib.parse import urlparse
import requests

API = "https://commons.wikimedia.org/w/api.php"
# 规范 UA（Wikimedia 政策要求带项目页/联系方式，含 python-requests 字样会被限流）
UA = {"User-Agent": "bili-history/1.0 (https://github.com/zhaohongjun20-creator/bili-history; educational documentary bot)"}
OK_LICENSES = ("public domain", "cc0", "cc by", "cc by-sa")
IMG_EXTS = {".jpg", ".jpeg", ".png"}   # 仅静态图；排除 webm/ogv/svg/tif/pdf
_last_request = 0.0
MIN_INTERVAL = 2.0


def _throttle():
    global _last_request
    wait = MIN_INTERVAL - (time.time() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.time()


def _get_with_retry(params: dict) -> dict:
    for attempt in range(3):
        _throttle()
        r = requests.get(API, params=params, headers=UA, timeout=30)
        if r.status_code == 429:
            time.sleep(15 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("wikimedia api rate limited after retries")


def _license_ok(extmeta: dict) -> bool:
    lic = (extmeta.get("LicenseShortName", {}) or {}).get("value", "").lower()
    return any(lic.startswith(k) or lic == k for k in OK_LICENSES) or "public domain" in lic


def _title_tokens(q: str) -> set:
    return {t for t in re.split(r"[^a-z0-9]+", q.lower()) if len(t) >= 4}


def search_images(query: str, limit: int = 4) -> list:
    """返回 [{title, url, license, author}]，仅含许可合规图片。

    相关性闸门：标题必须含查询词的实词 token 之一（防人名撞地名：
    搜 Montgomery 将军 结果全是阿拉巴马州首府照片这类事故）。
    同系列图（前3词相同）最多取 2 张，防整池同款。
    """
    data = _get_with_retry({
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": query, "gsrnamespace": 6, "gsrlimit": limit,
        "prop": "imageinfo", "iiprop": "url|extmetadata|size",
    })
    pages = (data.get("query") or {}).get("pages") or {}
    out, family = [], {}
    toks = _title_tokens(query)
    for p in pages.values():
        ii = (p.get("imageinfo") or [{}])[0]
        meta = ii.get("extmetadata") or {}
        title_l = p["title"].lower()
        if toks and not any(t in title_l for t in toks):
            continue
        key = " ".join(title_l.split(":")[-1].split()[:3])  # 系列图家族键
        family[key] = family.get(key, 0) + 1
        if family[key] > 2:
            continue
        if not _license_ok(meta):
            continue
        ext = Path(urlparse(ii.get("url", "")).path).suffix.lower()
        if ext not in IMG_EXTS:
            continue
        w, h = ii.get("width", 0), ii.get("height", 0)
        if w < 640 or w <= h:          # 横图，Ken Burns 会放大补足
            continue
        out.append({
            "title": p["title"],
            "url": ii.get("url", ""),
            "license": (meta.get("LicenseShortName", {}) or {}).get("value", "?"),
            "author": re.sub(r"<[^>]+>", "", (meta.get("Artist", {}) or {}).get("value", "未知")).strip()[:60] or "未知",
        })
    return out


def download_image(url: str, dest: str) -> str:
    clean = url.split("?")[0]           # 去掉 utm 等追踪参数
    for attempt in range(3):
        _throttle()
        r = requests.get(clean, headers=UA, timeout=60)
        if r.status_code == 429:
            time.sleep(15 * (attempt + 1))
            continue
        r.raise_for_status()
        with open(dest, "wb") as f:
            f.write(r.content)
        return dest
    raise RuntimeError(f"image download rate limited: {url}")
