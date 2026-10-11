# -*- coding: utf-8 -*-
"""抖音投稿桥 v2：Edge 真浏览器 + 持久化配置文件。

抖音无个人开放上传 API，业界通行做法即浏览器自动化
（social-auto-upload / MoneyPrinterPlus 同路线）。

v2 变更：弃 Playwright 自带 Chromium + storage_state，改用系统 Edge
（channel=msedge）+ 独立持久化 profile（data/douyin-edge-profile/）——
真浏览器指纹 + 登录态原生留存，抗检测与稳定性都更好。
旧登录态（data/douyin-state.json）首次自动导入，无需重新扫码。

用法：
  python douyin_bridge.py login            # profile 无登录态时：弹 Edge 扫码
  python douyin_bridge.py upload <视频> <标题>  # 上传并发布（标题支持 #话题）
  python douyin_bridge.py open             # 打开发布页（演示/录屏，不自动操作）
"""
import sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent
PROFILE = ROOT / "data" / "douyin-edge-profile"
OLD_STATE = ROOT / "data" / "douyin-state.json"
UPLOAD_URL = "https://creator.douyin.com/creator-micro/content/upload"

# 直连（国内站走系统代理会被绕出国）+ GPU 禁用（canvas 动画在自动化下偶发崩溃）
LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled", "--no-proxy-server",
               "--disable-gpu"]
INIT_JS = ("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
           "window.chrome={runtime:{}};")


def _ctx(p):
    """启动持久化 Edge 上下文（登录态存 profile，天然跨会话）。"""
    PROFILE.mkdir(parents=True, exist_ok=True)
    return p.chromium.launch_persistent_context(
        str(PROFILE), channel="msedge", headless=False,
        viewport={"width": 1280, "height": 800}, locale="zh-CN",
        args=LAUNCH_ARGS)


def _import_old_cookies(ctx) -> bool:
    """把 v1 storage_state 的 cookie 导入 Edge profile（一次性迁移）。"""
    if not OLD_STATE.exists():
        return False
    try:
        import json
        state = json.loads(OLD_STATE.read_text(encoding="utf-8"))
        cookies = []
        for c in state.get("cookies", []):
            c.pop("sameSite", None)
            cookies.append(c)
        ctx.add_cookies(cookies)
        return True
    except Exception as e:
        print(f"旧cookie导入失败: {e}")
        return False


def _goto_upload(page):
    page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=45_000)
    time.sleep(3)


def _logged_in(page) -> bool:
    """新版上传页 input 藏在 shadow DOM，用上传框文案/可见 input 双判定。"""
    if "upload" not in page.url:
        return False
    return (page.locator("input[type=file]").count() > 0
            or page.locator("text=发视频").count() > 0)


def login() -> bool:
    """确保 Edge profile 处于登录态：先试旧cookie，不行弹码等人扫。"""
    with sync_playwright() as p:
        ctx = _ctx(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.add_init_script(INIT_JS)
            _goto_upload(page)
            if _logged_in(page):
                print("Edge profile 已是登录态")
                return True
            if _import_old_cookies(ctx):
                print("已导入 v1 登录态，验证中……")
                _goto_upload(page)
                if _logged_in(page):
                    print("v1 登录态迁移成功")
                    return True
            print("请在弹出的 Edge 窗口扫码登录抖音（手机上点【确认登录】）……")
            deadline = time.time() + 300
            while time.time() < deadline:
                try:
                    if _logged_in(page):
                        print("登录成功（profile 已持久化，以后免扫）")
                        return True
                    if "upload" not in page.url:   # 被重定向走了就再进
                        _goto_upload(page)
                except Exception as e:             # 窗口被关/崩溃：温和退出
                    print(f"窗口关闭（{type(e).__name__}），本次登录中断")
                    return False
                time.sleep(2)
            page.screenshot(path="data/douyin_login_fail.png")
            print("❌ 5分钟未确认登录（截图 data/douyin_login_fail.png）")
            return False
        finally:
            ctx.close()


def upload(video_path: str, title: str, timeout: int = 600) -> bool:
    """Edge 打开上传页 → 传文件 → 填描述 → 发布。"""
    video = Path(video_path).resolve()
    assert video.exists(), f"视频不存在 {video}"
    with sync_playwright() as p:
        ctx = _ctx(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.add_init_script(INIT_JS)
            _goto_upload(page)
            if not _logged_in(page) and _import_old_cookies(ctx):
                _goto_upload(page)
            # 首次跳转可能被重定向到首页（软风控），多试两轮导航再判死
            for _ in range(3):
                if _logged_in(page):
                    break
                _goto_upload(page)
            if not _logged_in(page):
                print("未登录：请先执行 python douyin_bridge.py login")
                return False

            # ① 传文件：优先 input；新版页面用 file chooser 事件兜底
            inputs = page.locator("input[type=file]")
            if inputs.count():
                inputs.first.set_input_files(str(video))
            else:
                with page.expect_file_chooser() as fc:
                    box = page.locator("[class*='upload']").first
                    (box if box.count() else page.locator("text=发视频").first).click()
                fc.value.set_files(str(video))
            print("已选文件，等待上传+转码……")
            page.wait_for_selector("text=重新上传", timeout=timeout * 1000)
            time.sleep(3)
            page.screenshot(path="data/douyin_step1_uploaded.png")

            # ② 填描述（抖音要求作品描述；多选择器兜底）
            filled = False
            for sel in ("[contenteditable='true']", ".ql-editor", "textarea"):
                loc = page.locator(sel).first
                try:
                    if loc.count() and loc.is_visible():
                        loc.click()
                        page.keyboard.press("Control+a")
                        page.keyboard.press("Delete")
                        page.keyboard.type(title, delay=30)
                        filled = True
                        print(f"描述已填入 via {sel}")
                        break
                except Exception:
                    continue
            if not filled:
                print("警告：未找到描述编辑器（截图见 step1），尝试直接发布")
            time.sleep(1)
            page.screenshot(path="data/douyin_step2_filled.png")

            # ③ 发布（精确匹配"发布"，避免误点）
            publish_btn = page.locator("button:text-is('发布')").first
            if not publish_btn.count():
                publish_btn = page.locator("button:has-text('发布')").last
            for _ in range(30):
                if publish_btn.count() and publish_btn.is_enabled():
                    break
                time.sleep(1)
            publish_btn.click()
            print("已点击发布，等待结果……")

            # ④ 结果监听
            for _ in range(90):
                if "content/manage" in page.url or page.locator("text=发布成功").count():
                    print("✅ 抖音发布成功")
                    return True
                time.sleep(1)
            page.screenshot(path="data/douyin_step3_result.png")
            print(f"⚠️ 未确认成功（结果截图 data/douyin_step3_result.png，当前 {page.url}）")
            return False
        finally:
            ctx.close()


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "login":
        sys.exit(0 if login() else 1)
    elif len(sys.argv) >= 2 and sys.argv[1] == "open":
        with sync_playwright() as p:
            ctx = _ctx(p)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=45_000)
            print("发布页已打开（演示模式）。关闭窗口即退出。")
            while True:
                time.sleep(60)
    elif len(sys.argv) >= 4 and sys.argv[1] == "upload":
        sys.exit(0 if upload(sys.argv[2], sys.argv[3]) else 1)
    else:
        print(__doc__)
