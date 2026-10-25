"""投稿桥接测试：错误分类（风控/登录失效/超时）、ANSI 清理、登录态路径配置。"""
import subprocess
from pathlib import Path

import pytest

import sau_bridge


def _result(stderr="", stdout="", returncode=1):
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


def test_ansi_stripped():
    raw = "\x1b[1mError\x1b[22m code: 21566"
    assert sau_bridge._clean(raw) == "Error code: 21566"


def test_rate_limit_classified():
    err = sau_bridge._classify(_result(
        stderr='ResponseData { code: 21566, message: "投稿过于频繁" }'))
    assert isinstance(err, sau_bridge.RateLimited)
    assert err.code == 21566


def test_auth_failure_classified():
    err = sau_bridge._classify(_result(stderr="账号未登录，请重新扫码"))
    assert isinstance(err, sau_bridge.UploadError)
    assert not isinstance(err, sau_bridge.RateLimited)
    assert "登录" in str(err)


def test_generic_failure_classified():
    err = sau_bridge._classify(_result(stderr="disk full"))
    assert isinstance(err, sau_bridge.UploadError)
    assert err.code is None


def test_error_message_no_credentials():
    """错误信息里不能带 Cookie/凭证内容。"""
    err = sau_bridge._classify(_result(
        stderr='SESSDATA=abc123; code: -101 账号未登录'))
    msg = str(err)
    assert "SESSDATA=abc123" not in msg       # 分类只留关键句
    assert "登录" in msg


def test_configure_account_file(tmp_path):
    p = tmp_path / "my-account.json"
    sau_bridge.configure(p)
    assert sau_bridge.ACCOUNT_FILE == p
    assert not sau_bridge.check_account()      # 文件不存在 → False，不抛


def test_default_account_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("BILIUP_ACCOUNT_FILE", str(tmp_path / "env.json"))
    assert sau_bridge._default_account_file() == tmp_path / "env.json"


def test_run_timeout_converted_to_uncertain(monkeypatch):
    """_run 超时 → UploadError(code=-1)：平台状态未核实，不得当普通失败重投。"""
    def fake_run(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="biliup", timeout=10)
    monkeypatch.setattr(sau_bridge.subprocess, "run", fake_run)
    with pytest.raises(sau_bridge.UploadError) as ei:
        sau_bridge._run(["-u", "x.json", "renew"], timeout=10)
    assert ei.value.code == -1


def test_upload_surfaces_upload_error(monkeypatch):
    def run(*a, **kw):
        raise sau_bridge.UploadError("平台状态未核实", code=-1)
    monkeypatch.setattr(sau_bridge, "_run", run)
    with pytest.raises(sau_bridge.UploadError) as ei:
        sau_bridge.upload("v.mp4", "t", "d", ["x"])
    assert ei.value.code == -1
