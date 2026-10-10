# -*- coding: utf-8 -*-
"""抖音投稿桥：Playwright 驱动抖音创作中心（网页版）。

抖音无个人开放上传 API，业界通行做法即浏览器自动化
（social-auto-upload / MoneyPrinterPlus 同路线，MIT 生态惯例）。

用法：
  python douyin_bridge.py login            # 首次：弹浏览器扫码，登录态存 data/douyin-state.json
  python douyin_bridge.py upload <视频> <标题>  # 上传并发布（标题支持 #话题）
"""
import sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright

STATE = Path(__file__).parent / "data" / "douyin-state.json"
UPLOAD_URL = "https://creator.douyin.com/creator-micro/content/upload"

# 反自动化检测 + 强制直连（抖音是国内站，走系统代理反而会被绕出国导致资源挂起）
# --disable-gpu：抖音登录页 canvas/WebGL 动画在 Windows 有头模式下偶发 GPU 崩溃
LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled", "--no-proxy-server",
               "--disable-gpu"]
INIT_JS = (
    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
    "window.chrome={runtime:{}};"
)


def _pw(headless: bool):
    p = sync_playwright().start()
    return p, lambda: p.chromium.launch(headless=headless, args=LAUNCH_ARGS)


def login(retries: int = 5):
    """弹出可见浏览器，等待扫码；窗口崩溃/被误关自动重开（最多 retries 轮）。"""
    for attempt in range(1, retries + 1):
        try:
            if _login_once(attempt):
                return True
        except Exception as e:
            print(f"第{attempt}轮异常: {type(e).__name__}: {e}")
        print("窗口被关闭或崩溃，2秒后重新弹出（请勿手动关窗口）……")
        time.sleep(2)
    return False


def _login_once(attempt: int) -> bool:
    p, launch = _pw(headless=False)
    browser = launch()
    logged = False
    try:
        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        ctx.add_init_script(INIT_JS)
        page = ctx.new_page()
        try:
            page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=45_000)
        except Exception as e:
            print(f"goto 异常（可能仍在加载，继续）: {type(e).__name__}")
        print(f"[第{attempt}轮] 当前URL: {page.url}")
        Path("data").mkdir(exist_ok=True)
        page.screenshot(path="data/douyin_login_debug.png")
        print("请在弹出的浏览器窗口中扫码登录抖音（扫码后手机上点【确认登录】）……")
        # 登录成功判定（宽松版）：上传入口出现；或二维码消失且离开登录页后重进出现入口。
        # 二维码在场时绝不刷新页面，避免打断扫码。窗口崩溃等异常向上抛给 login() 重试。
        deadline = time.time() + 300
        while time.time() < deadline:
            if page.locator("input[type=file]").count():
                logged = True
                break
            qr = page.locator("canvas, [class*='qrcode'], [class*='web-login']").count()
            if qr == 0:   # 二维码没了＝大概率已登录，重进上传页确认
                try:
                    page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=30_000)
                    page.wait_for_selector("input[type=file]", timeout=15_000)
                    logged = True
                    break
                except Exception:
                    pass
            time.sleep(2)
        if logged:
            STATE.parent.mkdir(exist_ok=True)
            ctx.storage_state(path=str(STATE))
            print(f"登录成功，状态已存 {STATE}")
        else:
            page.screenshot(path="data/douyin_login_fail.png")
            print("❌ 本轮5分钟未确认登录，截图 data/douyin_login_fail.png")
    finally:
        try:
            browser.close()
        except Exception:
            pass
        try:
            p.stop()
        except Exception:
            pass
    return logged


def _logged_in(page) -> bool:
    try:
        page.wait_for_selector("input[type=file]", timeout=8_000)
        return True
    except Exception:
        return False


def upload(video_path: str, title: str, timeout: int = 600) -> bool:
    """上传视频并发布。title 里可带 #话题（抖音用 # 分隔话题）。"""
    if not STATE.exists():
        print("未登录：先执行 python douyin_bridge.py login")
        return False
    video = Path(video_path).resolve()
    assert video.exists(), f"视频不存在 {video}"

    p, launch = _pw(headless=False)   # 抖音对 headless 敏感，全程有头
    browser = launch()
    ctx = browser.new_context(
        storage_state=str(STATE), viewport={"width": 1280, "height": 800},
        locale="zh-CN")
    ctx.add_init_script(INIT_JS)
    page = ctx.new_page()
    try:
        page.goto(UPLOAD_URL, wait_until="domcontentloaded")
        if not _logged_in(page):
            print("登录态失效：请重新执行 login 扫码")
            return False

        # ① 传文件
        inputs = page.locator("input[type=file]")
        inputs.first.set_input_files(str(video))
        print("已选文件，等待上传+转码……")
        page.wait_for_selector("text=重新上传", timeout=timeout * 1000)
        time.sleep(3)   # 等表单（封面/识别）稳定
        page.screenshot(path="data/douyin_step1_uploaded.png")

        # ② 填描述（抖音要求作品描述，否则发布按钮无效；多选择器兜底）
        filled = False
        for sel in (".ql-editor", "[contenteditable='true']", "textarea"):
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

        # ③ 发布（精确匹配文本"发布"的按钮，避免误点"发布视频"等）
        publish_btn = page.locator("button:text-is('发布')").first
        if not publish_btn.count():
            publish_btn = page.locator("button:has-text('发布')").last
        for _ in range(30):
            if publish_btn.count() and publish_btn.is_enabled():
                break
            time.sleep(1)
        publish_btn.click()
        print("已点击发布，等待结果……")

        # ④ 结果监听：成功=跳转内容管理或出现成功提示；失败=错误 toast
        for _ in range(90):
            url = page.url
            if "content/manage" in url or page.locator("text=发布成功").count():
                print("✅ 抖音发布成功")
                ctx.storage_state(path=str(STATE))
                return True
            page.screenshot(path="data/douyin_step3_result.png")
            time.sleep(1)
            if _ % 10 == 9:
                page.screenshot(path="data/douyin_step3_result.png")
        print(f"⚠️ 未确认成功（结果截图 data/douyin_step3_result.png，当前 {page.url}）")
        return False
    finally:
        browser.close(); p.stop()


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "login":
        login()
    elif len(sys.argv) >= 4 and sys.argv[1] == "upload":
        ok = upload(sys.argv[2], sys.argv[3])
        sys.exit(0 if ok else 1)
    else:
        print(__doc__)
