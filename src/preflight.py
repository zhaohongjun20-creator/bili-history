"""启动前检查（方案 P0-3）：花任何 API 额度之前把配置/路径/素材/工具查一遍。

原则：
- 一次收集全部问题再报，不在日志里输出密钥值；
- 登录态只在需要发布（非 dry-run）时检查，生成不应因未登录而失败；
- 网络检查轻量且区分"网络不通"与"配置缺失"，不发送密钥。
"""
import json
import os
from pathlib import Path

PLACEHOLDER_VALUES = {"", "your_zhipu_key_here", "your_pexels_key_here",
                      "changeme", "xxx"}

# config.yaml 必需字段（缺失/类型不对即报错）
_REQUIRED_CFG = [
    ("topics", list),
    ("video", dict),
    ("narrator", dict),
    ("copywriter", dict),
    ("bilibili", dict),
    ("bgm_dir", str),
]


class PreflightError(RuntimeError):
    def __init__(self, problems: list[str]):
        self.problems = problems
        msg = f"启动前检查未通过（共{len(problems)}项）:\n  - " + "\n  - ".join(problems)
        super().__init__(msg)


def check_config(cfg: dict) -> list[str]:
    problems = []
    for key, typ in _REQUIRED_CFG:
        v = cfg.get(key)
        if not isinstance(v, typ) or (typ in (list, dict) and not v):
            problems.append(f"config.yaml 缺少必需配置项或为空: {key}")
            return problems          # 结构不全时后续字段检查无意义
    v = cfg["video"]
    if not (v.get("sections_min") and v.get("sections_max")
            and isinstance(v.get("chars_per_section"), list)
            and len(v["chars_per_section"]) == 2):
        problems.append("config.yaml video 段配置不完整（sections_min/max、chars_per_section）")
    n = cfg["narrator"]
    if not (n.get("backend") and (n.get("voices") or n.get("voice")) and n.get("rate")):
        problems.append("config.yaml narrator 段配置不完整（backend、voice(s)、rate）")
    if not cfg["copywriter"].get("model"):
        problems.append("config.yaml copywriter.model 未配置")
    b = cfg["bilibili"]
    if not b.get("tid") or not b.get("tags_extra"):
        problems.append("config.yaml bilibili 段配置不完整（tid、tags_extra）")
    if not isinstance(cfg.get("bgm_volume"), (int, float)):
        problems.append("config.yaml bgm_volume 未配置（数字，0-1）")
    if not cfg["topics"]:
        problems.append("主题池为空，请在 config.yaml 补充 topics")
    return problems


def check_api_key() -> list[str]:
    key = os.environ.get("ZHIPU_API_KEY", "")
    if key.strip().lower() in PLACEHOLDER_VALUES:
        return ["缺少 ZHIPU_API_KEY（.env 或环境变量），讲稿/审查/TTS 均无法工作"]
    return []


def check_paths(repo_root: Path, cfg: dict) -> list[str]:
    problems = []
    for d in ("logs", "data", "data/jobs"):
        try:
            (repo_root / d).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            problems.append(f"目录不可创建: {d} ({e})")
    bgm_dir = repo_root / cfg["bgm_dir"]
    if not bgm_dir.is_dir():
        problems.append(f"BGM 目录不存在: {bgm_dir}（config.yaml bgm_dir）")
    elif not list(bgm_dir.glob("*.mp3")):
        problems.append(f"BGM 目录里没有 mp3: {bgm_dir}（合成阶段 random.choice 会失败）")
    return problems


def check_ffmpeg() -> list[str]:
    try:
        import imageio_ffmpeg
        import subprocess
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        r = subprocess.run([exe, "-version"], capture_output=True, timeout=30)
        if r.returncode != 0:
            return [f"ffmpeg 不可用: {exe}"]
        return []
    except Exception as e:
        return [f"ffmpeg 解析失败: {type(e).__name__} {e}（安装依赖: pip install imageio-ffmpeg）"]


def check_network() -> list[str]:
    """轻量连通性：只区分网络不通，不做认证探测（不发送密钥）。"""
    import requests
    for host in ("open.bigmodel.cn", "commons.wikimedia.org"):
        try:
            requests.get(f"https://{host}/", timeout=8,
                         headers={"User-Agent": "bili-history-preflight"})
        except requests.RequestException as e:
            return [f"网络不通: {host} ({type(e).__name__})——检查代理/网络后重试"]
    return []


def check_login(publishing: bool, account_file: Path | None) -> list[str]:
    """发布登录态：只在需要投稿时检查（生成/试跑不依赖）。"""
    if not publishing:
        return []
    if account_file is None:
        return []
    if not account_file.exists():
        return [f"投稿登录态缺失: {account_file}——先运行 python sau_bridge 登录 "
                f"（或用 bilibili.account_file 配置正确路径）"]
    try:
        json.loads(account_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [f"投稿登录态文件损坏（不是合法 JSON）: {account_file}——重新扫码登录"]
    return []


def run_preflight(cfg: dict, repo_root: Path, publishing: bool,
                  account_file: Path | None = None,
                  network: bool = True) -> list[str]:
    """收集全部问题返回（空列表=通过）。network=False 跳过连通性检查（离线测试用）。"""
    problems = []
    problems += check_config(cfg)
    problems += check_api_key()
    problems += check_paths(repo_root, cfg)
    problems += check_ffmpeg()
    if network:
        problems += check_network()
    problems += check_login(publishing, account_file)
    return problems


def preflight_or_die(cfg: dict, repo_root: Path, publishing: bool,
                     account_file: Path | None = None, log=None,
                     network: bool = True):
    problems = run_preflight(cfg, repo_root, publishing, account_file, network)
    if problems:
        for p in problems:
            (log.error if log else print)(f"[preflight] {p}")
        raise PreflightError(problems)
    if log:
        log.info("[preflight] 启动检查通过（publishing=%s）", publishing)
