# -*- coding: utf-8 -*-
"""数据反馈闭环（借鉴 autopilot-shots / MatrixMedia）：

每日流水线发布后，用 Playwright 无头浏览器读自己 B站 主页的全部视频播放数，
写入 snapshots 表，并生成"战报"（频道聚合 + 涨幅榜）——让选题有据可依。

无 API 依赖：公开 stat/member 接口对脚本风控（412/404），读主页页面最稳。
"""
import re
from datetime import date, timedelta
from playwright.sync_api import sync_playwright

SPACE_URL = "https://space.bilibili.com/{mid}/upload/video"

# 播放数归一化："1.2万" → 12000
def _num(s: str) -> int:
    s = (s or "").strip()
    if "万" in s:
        return int(float(s.replace("万", "")) * 10000)
    return int(s) if s.isdigit() else 0


def _bili_cookies() -> list:
    """从 biliup 账号文件取 B站 cookie（匿名访问主页作品列表不渲染，需登录态）。"""
    import json
    from pathlib import Path
    acc = Path(__file__).resolve().parent.parent / \
        "../bili-autopost-sau/data/biliup-account.json"
    try:
        d = json.loads(acc.resolve().read_text(encoding="utf-8"))
        out = []
        for c in d["cookie_info"]["cookies"]:
            item = {"name": c["name"], "value": c["value"],
                    "domain": ".bilibili.com", "path": "/"}
            out.append(item)
        return out
    except Exception:
        return []


def collect(mid: int, max_pages: int = 5) -> list:
    """读主页全部投稿，返回 [{bvid,title,plays}]。无头+登录cookie，失败抛异常由调用方降级。"""
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-proxy-server"])
        page = browser.new_page(
            viewport={"width": 1280, "height": 800}, locale="zh-CN",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
        try:
            ck = _bili_cookies()
            if ck:
                page.context.add_cookies(ck)
            page.goto(SPACE_URL.format(mid=mid), wait_until="domcontentloaded",
                      timeout=45_000)
            page.wait_for_selector("a[href*='/video/BV']", timeout=30_000)
            out = {}
            for _ in range(max_pages):
                page.wait_for_timeout(1500)
                for bv, title, stats in page.evaluate(
                        """() => {
                    const seen = new Map();
                    document.querySelectorAll("a[href*='/video/BV']").forEach(a => {
                        const m = a.href.match(/BV[0-9A-Za-z]+/); if (!m) return;
                        const card = a.closest('[class*="card"]') || a.parentElement;
                        if (!seen.has(m[0]))
                            seen.set(m[0], [(a.getAttribute('title')||a.innerText||'').trim().slice(0,60),
                                            (card?.innerText||'')]);
                    });
                    return [...seen.entries()].map(([k, v]) => [k, v[0], v[1]]);
                }"""):
                    lines = [l.strip() for l in stats.split("\n") if l.strip()]
                    plays = 0
                    for l in lines:          # 统计行 = 播放|弹幕|时长，取首个纯数字即播放数
                        if re.match(r"^[\d.]+万?$", l):
                            plays = _num(l)
                            break
                    out[bv] = {"title": title, "plays": plays}
                nxt = page.locator("button:has-text('下一页')")
                if not nxt.count() or "disabled" in (nxt.first.get_attribute("class") or ""):
                    break
                nxt.first.click()
            return [{"bvid": k, **v} for k, v in out.items()]
        finally:
            browser.close()


def _channel_of(bvid: str, title: str, known: dict) -> str:
    if bvid in known:
        return known[bvid]
    if any(k in title for k in ("战役", "坦克", "战", "行动", "舰队", "战机", "战斗机",
                                "装甲", "军团", "军")):
        return "军史"
    return "其他"


def collect_and_report(store, mid: int, log, channel_hint: str = "军史") -> None:
    """采集快照 + 输出战报。任何异常只警告不影响发布主链路。"""
    try:
        rows = collect(mid)
    except Exception as e:
        log.warning("数据采集失败(不影响发布): %s %s", type(e).__name__, str(e)[:80])
        return
    known = {b: c for b, t, c in store.videos_all() if c}
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    for r in rows:
        store.upsert_snapshot(r["bvid"], r["plays"], today)
        if not any(b == r["bvid"] for b, *_ in store.videos_all()):
            store.record_video(r["bvid"], r["title"],
                               _channel_of(r["bvid"], r["title"], known))

    # ── 战报 ──
    agg, deltas = {}, []
    for b, t, c in store.videos_all():
        cur = next((r["plays"] for r in rows if r["bvid"] == b), None)
        if cur is None:
            continue
        prev = store.prev_plays(b, today)
        ch = c or "其他"
        a = agg.setdefault(ch, [0, 0])          # [总播放, 视频数]
        a[0] += cur
        a[1] += 1
        if prev is not None and cur > prev:
            deltas.append((cur - prev, t, ch))
    log.info("📊 战报(%s)：", today)
    for ch, (tot, n) in sorted(agg.items(), key=lambda x: -x[1][0]):
        log.info("   %s 频道：%d条 / 总播放%d / 均值%.0f",
                 ch, n, tot, tot / n if n else 0)
    for d, t, ch in sorted(deltas, reverse=True)[:5]:
        log.info("   涨幅 +%d《%s》(%s)", d, t[:18], ch)
    if not deltas:
        log.info("   （暂无对比昨日数据，明天起出涨幅榜）")
