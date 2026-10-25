"""启动前检查测试（方案 P0-3）：坏配置/假密钥/空BGM/登录态按需检查。"""
from pathlib import Path

from src import preflight


def _cfg(**over):
    cfg = {
        "topics": ["主题一"],
        "video": {"width": 1920, "height": 1080, "fps": 30,
                  "sections_min": 12, "sections_max": 16,
                  "chars_per_section": [80, 130]},
        "narrator": {"backend": "edge", "voice": "v", "rate": "+5%"},
        "copywriter": {"model": "glm-4-flash", "temperature": 0.7},
        "bilibili": {"tid": 239, "tags_extra": ["t"]},
        "bgm_dir": "bgm",
        "bgm_volume": 0.22,
    }
    cfg.update(over)
    return cfg


def test_valid_config_passes(tmp_path, monkeypatch):
    (tmp_path / "bgm").mkdir()
    (tmp_path / "bgm" / "a.mp3").write_bytes(b"x")
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.setenv("ZHIPU_API_KEY", "realkey123")
    problems = preflight.run_preflight(_cfg(), tmp_path, publishing=False,
                                       network=False)
    assert problems == []


def test_missing_key_detected(tmp_path, monkeypatch):
    (tmp_path / "bgm").mkdir()
    (tmp_path / "bgm" / "a.mp3").write_bytes(b"x")
    monkeypatch.setenv("ZHIPU_API_KEY", "your_zhipu_key_here")
    problems = preflight.run_preflight(_cfg(), tmp_path, publishing=False,
                                       network=False)
    assert any("ZHIPU_API_KEY" in p for p in problems)


def test_empty_bgm_dir_detected(tmp_path, monkeypatch):
    (tmp_path / "bgm").mkdir()          # 空目录
    monkeypatch.setenv("ZHIPU_API_KEY", "k")
    problems = preflight.run_preflight(_cfg(), tmp_path, publishing=False,
                                       network=False)
    assert any("BGM" in p for p in problems)


def test_missing_bgm_dir_detected(tmp_path, monkeypatch):
    monkeypatch.setenv("ZHIPU_API_KEY", "k")
    problems = preflight.run_preflight(_cfg(), tmp_path, publishing=False,
                                       network=False)
    assert any("BGM 目录不存在" in p for p in problems)


def test_incomplete_config_detected(tmp_path, monkeypatch):
    monkeypatch.setenv("ZHIPU_API_KEY", "k")
    bad = _cfg()
    bad["narrator"] = {"backend": "edge"}          # 缺 voice/rate
    problems = preflight.run_preflight(bad, tmp_path, publishing=False,
                                       network=False)
    assert any("narrator" in p for p in problems)


def test_login_checked_only_when_publishing(tmp_path, monkeypatch):
    (tmp_path / "bgm").mkdir()
    (tmp_path / "bgm" / "a.mp3").write_bytes(b"x")
    monkeypatch.setenv("ZHIPU_API_KEY", "k")
    missing = tmp_path / "no" / "account.json"
    # 生成模式：登录态缺失不拦截
    assert preflight.run_preflight(_cfg(), tmp_path, publishing=False,
                                   account_file=missing, network=False) == []
    # 发布模式：拦截
    problems = preflight.run_preflight(_cfg(), tmp_path, publishing=True,
                                       account_file=missing, network=False)
    assert any("登录态缺失" in p for p in problems)


def test_corrupt_account_file_detected(tmp_path, monkeypatch):
    (tmp_path / "bgm").mkdir()
    (tmp_path / "bgm" / "a.mp3").write_bytes(b"x")
    monkeypatch.setenv("ZHIPU_API_KEY", "k")
    bad = tmp_path / "account.json"
    bad.write_text("{not json", encoding="utf-8")
    problems = preflight.run_preflight(_cfg(), tmp_path, publishing=True,
                                       account_file=bad, network=False)
    assert any("损坏" in p for p in problems)


def test_preflight_error_message_lists_all(tmp_path, monkeypatch):
    monkeypatch.setenv("ZHIPU_API_KEY", "")
    try:
        preflight.preflight_or_die(_cfg(), tmp_path, publishing=False,
                                   network=False)
        raise AssertionError("应当抛 PreflightError")
    except preflight.PreflightError as e:
        assert "ZHIPU_API_KEY" in str(e) and "BGM" in str(e)
