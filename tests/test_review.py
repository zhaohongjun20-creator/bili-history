"""审查闸门测试（方案 P1-4）：异常不再放行、重试、结构化问题、改题校验。"""
import pytest

from src import review


def test_deterministic_label_reject():
    secs = [{"text": "这是一段没有数字的旁白" * 5}]
    out = review.review("k", "反差错位：某某战役", "d", secs, [])
    assert out["verdict"] == "reject"
    assert any("标签" in p for p in out["problems"])


def test_deterministic_all_issues_reject(monkeypatch):
    """确定性检查全量硬拒：无钩子/超长也 reject，不允许"记录后继续"。"""
    calls = []
    monkeypatch.setattr(review.requests, "post", _fake_post("", calls=calls))
    secs = [{"text": "无数字旁白" * 10}]
    out = review.review("k", "某某战役完整解析报告", "d", secs, [])  # 无钩子
    assert out["verdict"] == "reject"
    assert calls == []                     # 不该花 GLM


def test_equipment_codes_exempt_from_digit_check():
    """cnnum 有意保留 B-17/T-34 装备编号——不得当残留数字拒稿。"""
    secs = [{"text": "第501重型坦克营的T-34与B-17编队出场，共计三百五十辆。"}]
    issues = review._deterministic("为什么这场战役只用了7天", secs)
    assert not any("残留数字" in p for p in issues)


def test_unknown_verdict_rejected(monkeypatch):
    """verdict 不在 pass/fix/reject 内 → 视为无效返回，重试后 ReviewUnavailable。"""
    body = '{"verdict": "maybe", "problems": []}'
    monkeypatch.setattr(review.requests, "post", _fake_post(body))
    with pytest.raises(review.ReviewUnavailable):
        review.review("k", "为什么这场战役只用了7天", "d",
                      [{"text": "无数字旁白" * 10}], [], retries=1)


def _fake_post(content: str, fail_first=0, calls=None):
    """构造 requests.post 替身：前 fail_first 次抛连接错误，之后返回 content。"""
    import requests

    def post(url, headers=None, json=None, timeout=None):
        if calls is not None:
            calls.append(1)
        if fail_first and len(calls) <= fail_first:
            raise requests.ConnectionError("boom")
        class R:
            def raise_for_status(self):
                pass
            def json(self):
                return {"choices": [{"message": {"content": content}}]}
        return R()
    return post


def test_service_down_raises_not_pass(monkeypatch):
    """审查服务持续不可用 → ReviewUnavailable（绝不自动放行，方案 P1-4 验收）。"""
    calls = []
    monkeypatch.setattr(review.requests, "post", _fake_post("", fail_first=99, calls=calls))
    with pytest.raises(review.ReviewUnavailable):
        review.review("k", "为什么这场战役只用了7天", "d",
                      [{"text": "无数字旁白" * 10}], [], retries=1)
    assert len(calls) == 2          # 重试了 1 次


def test_illegal_json_retries_then_raises(monkeypatch):
    calls = []
    monkeypatch.setattr(review.requests, "post", _fake_post("不是JSON", calls=calls))
    with pytest.raises(review.ReviewUnavailable):
        review.review("k", "为什么这场战役只用了7天", "d",
                      [{"text": "无数字旁白" * 10}], [], retries=2)
    assert len(calls) == 3


def test_valid_verdict_parsed(monkeypatch):
    body = ('```json\n{"verdict": "pass", "severity": "low", "problems": '
            '[{"issue": "配图偏现代", "section": 2, "severity": "medium",'
            ' "suggest": "核对拍摄年代"}], "title_fix": ""}\n```')
    monkeypatch.setattr(review.requests, "post", _fake_post(body))
    out = review.review("k", "为什么这场战役只用了7天", "d",
                        [{"text": "无数字旁白" * 10}], [])
    assert out["verdict"] == "pass"
    assert out["problems"][0].startswith("第2段")
    assert out["details"][0]["section"] == 2


def test_title_fix_rejected_if_no_hook(monkeypatch):
    body = ('{"verdict": "fix", "problems": [], "title_fix": "某某战役解析"}')
    monkeypatch.setattr(review.requests, "post", _fake_post(body))
    out = review.review("k", "为什么这场战役只用了7天", "d",
                        [{"text": "无数字旁白" * 10}], [])
    assert out["title_fix"] == ""   # 审查官给的标题无钩子 → 不采纳


def test_high_severity_survives_for_human_gate(monkeypatch):
    body = ('{"verdict": "pass", "severity": "high", "problems": '
            '[{"issue": "伤亡数字存疑", "section": -1, "severity": "high",'
            ' "suggest": "核对战史"}], "title_fix": ""}')
    monkeypatch.setattr(review.requests, "post", _fake_post(body))
    out = review.review("k", "为什么这场战役只用了7天", "d",
                        [{"text": "无数字旁白" * 10}], [])
    assert any(d.get("severity") == "high" for d in out["details"])
