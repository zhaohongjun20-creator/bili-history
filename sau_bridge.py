"""桥接层：以 social-auto-upload 的方式管理 biliup 并投稿到B站。

bilibili 运行时逻辑内联自 social-auto-upload/uploader/bilibili_uploader/runtime.py
（原作者项目要求 Python <3.13，本机 3.14，故内联其仅依赖 requests 的核心部分）：
自动从 biliup 官方 GitHub Release 下载对应平台二进制、记录版本、限流回退。
"""
import os
import platform
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path

import requests

GITHUB_RELEASE_API = "https://api.github.com/repos/biliup/biliup/releases/latest"
RUNTIME_ROOT = Path.home() / ".social-auto-upload" / "tools" / "biliup"

# 与 bili-autopost-sau 共享同一登录态（双副本各自 renew 会互相失效）。
# 路径可覆盖：环境变量 BILIUP_ACCOUNT_FILE 或 configure()（config.yaml bilibili.account_file），
# 不再写死目录结构——仓库搬移/独立部署时不至于找不到登录态。
_DEFAULT_ACCOUNT = Path(__file__).parent.parent / "bili-autopost-sau" / "data" / "biliup-account.json"


def _default_account_file() -> Path:
    env = os.environ.get("BILIUP_ACCOUNT_FILE", "").strip()
    return Path(env) if env else _DEFAULT_ACCOUNT


ACCOUNT_FILE = _default_account_file()


def configure(account_file) -> Path:
    """显式指定登录态文件（autopost 启动时按 config.yaml 调用）。"""
    global ACCOUNT_FILE
    ACCOUNT_FILE = Path(account_file)
    return ACCOUNT_FILE


class UploadError(RuntimeError):
    """投稿失败。code 为平台错误码（未知为 None；超时未核实为 -1）。"""
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


class RateLimited(UploadError):
    """平台风控限流（如 21566 投稿过于频繁）：冷却后重投即可，成片不必重做。"""


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
# 凭证脱敏：错误信息/日志里不得出现 Cookie 值（SESSDATA/bili_jct 等）
_SECRET_RE = re.compile(
    r"(SESSDATA|bili_jct|DedeUserID|access_key|token|authorization|cookie)"
    r"\s*[=:]\s*[^\s;,\"}]+", re.IGNORECASE)


def _clean(msg: str) -> str:
    """去 ANSI 色码 + 凭证脱敏（biliup 输出带颜色，入库/日志难读且可能带凭证）。"""
    out = _ANSI_RE.sub("", msg or "")
    return _SECRET_RE.sub(r"\1=***", out).strip()


def _classify(result: subprocess.CompletedProcess):
    """非零返回码 → 分类异常。错误信息不含任何凭证内容。"""
    raw = _clean(result.stderr or "") + "\n" + _clean(result.stdout or "")
    m = re.search(r"code:\s*(\d+)", raw)
    code = int(m.group(1)) if m else None
    if code == 21566 or "投稿过于频繁" in raw:
        return RateLimited(f"B站风控限流(code={code or 21566})：冷却后重投，成片保留", code)
    if code == -101 or re.search(r"账号未登录|cookie|Cookie|登录失效", raw):
        return UploadError("B站登录态失效——重新扫码登录后重试", code)
    return UploadError(raw.strip()[:400] or "biliup upload failed", code)


def _norm_system(name: str | None = None) -> str:
    v = (name or platform.system()).strip().lower()
    return "macos" if v == "darwin" else v


def _norm_machine(name: str | None = None) -> str:
    v = (name or platform.machine()).strip().lower()
    return {"amd64": "x86_64", "x64": "x86_64", "arm64": "aarch64"}.get(v, v)


def _platform_key() -> str:
    return f"{_norm_system()}-{_norm_machine()}"


def _binary_path() -> Path:
    exe = "biliup.exe" if _norm_system() == "windows" else "biliup"
    return RUNTIME_ROOT / _platform_key() / exe


def _version_path() -> Path:
    return _binary_path().with_name("version.txt")


def _select_asset(assets: list[dict]) -> dict:
    patterns = {
        "windows-x86_64": ("x86_64-windows.zip",),
        "linux-x86_64": ("x86_64-linux.tar.xz",),
        "macos-x86_64": ("x86_64-macos.tar.xz",),
        "macos-aarch64": ("aarch64-macos.tar.xz",),
    }.get(_platform_key())
    if not patterns:
        raise RuntimeError(f"unsupported platform: {_platform_key()}")
    for a in assets:
        if any(p in a.get("name", "") for p in patterns):
            return {"tag": "", "name": a["name"], "url": a.get("browser_download_url", "")}
    raise RuntimeError("no matching biliup release asset")


def ensure_biliup_binary(force_check: bool = False) -> Path:
    """确保 biliup 二进制存在：本地有则复用；否则查 GitHub 最新版下载。"""
    binary = _binary_path()
    if binary.exists() and not force_check:
        return binary
    r = requests.get(GITHUB_RELEASE_API, headers={"User-Agent": "bili-autopost-sau"},
                     timeout=30)
    r.raise_for_status()
    payload = r.json()
    asset = _select_asset(payload.get("assets", []))
    tag = payload.get("tag_name", "")
    if binary.exists() and _version_path().read_text(encoding="utf-8").strip() == tag:
        return binary
    binary.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="biliup-") as td:
        archive = Path(td) / asset["name"]
        with requests.get(asset["url"], stream=True, timeout=180) as resp:
            resp.raise_for_status()
            with archive.open("wb") as f:
                for chunk in resp.iter_content(1 << 20):
                    if chunk:
                        f.write(chunk)
        extract = Path(td) / "x"
        extract.mkdir()
        if archive.suffix.lower() == ".zip":
            with zipfile.ZipFile(archive) as z:
                z.extractall(extract)
        else:
            with tarfile.open(archive, "r:xz") as t:
                t.extractall(extract)
        cands = [p for p in extract.rglob("*")
                 if p.is_file() and p.name.lower() in {"biliup", "biliup.exe", "biliupr", "biliupr.exe"}]
        if not cands:
            raise RuntimeError("downloaded archive has no biliup executable")
        shutil.copy2(sorted(cands, key=lambda p: len(str(p)))[0], binary)
    if _norm_system() != "windows":
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    _version_path().write_text(tag, encoding="utf-8")
    return binary


def _run(args: list[str], interactive: bool = False,
         timeout: float | None = None) -> subprocess.CompletedProcess:
    cmd = [str(ensure_biliup_binary()), *args]
    if interactive:
        return subprocess.run(cmd, check=False)
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", check=False,
                              timeout=timeout)
    except subprocess.TimeoutExpired as e:
        # 平台侧状态未知：不能当作失败直接重投（有重复投稿风险），由调用方核实
        raise UploadError(f"biliup 命令超时({timeout}s)，平台状态未核实", code=-1) from e


def login_interactive():
    """终端显示二维码（或生成 ./qrcode.png），用B站APP扫码登录。"""
    ACCOUNT_FILE.parent.mkdir(parents=True, exist_ok=True)
    return _run(["-u", str(ACCOUNT_FILE), "login"], interactive=True)


def check_account() -> bool:
    """登录态检查（renew 非零即过期）。"""
    if not ACCOUNT_FILE.exists():
        return False
    return _run(["-u", str(ACCOUNT_FILE), "renew"], timeout=120).returncode == 0


UPLOAD_TIMEOUT = 1800  # 大文件慢传兜底；biliup 自身有分片重试


def upload(video_path: str, title: str, desc: str, tags: list,
           tid: int = 160, cover: str | None = None,
           source_url: str = "", copyright_self: bool = False) -> str:
    """投稿，成功返回 biliup 输出（含 BV 号）；失败抛分类异常。

    copyright_self=True 按自制（AI辅助创作）；否则转载 + source。
    失败分类见 UploadError/RateLimited；超时抛 code=-1（状态未核实，勿盲目重投）。
    """
    args = ["-u", str(ACCOUNT_FILE), "upload", video_path,
            "--title", title, "--desc", desc, "--tid", str(tid),
            "--tag", ",".join(tags)]
    if copyright_self:
        args += ["--copyright", "1"]
    else:
        args += ["--copyright", "2", "--source", source_url or "https://www.pexels.com"]
    if cover:
        args += ["--cover", cover]
    result = _run(args, timeout=UPLOAD_TIMEOUT)
    if result.returncode != 0:
        raise _classify(result)
    return _clean(result.stdout)
