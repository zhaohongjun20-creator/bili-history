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


def test_missing_verdict_rejected(monkeypatch):
    """JSON 合法但缺 verdict 字段 → 无效返回。"""
    monkeypatch.setattr(review.requests, "post",
                        _fake_post('{"problems": [], "severity": "low"}'))
    with pytest.raises(review.ReviewUnavailable):
        review.review("k", "为什么这场战役只用了7天", "d",
                      [{"text": "无数字旁白" * 10}], [], retries=0)


def test_problems_not_a_list_rejected(monkeypatch):
    """problems 不是列表（字符串/数字）→ 结构非法 → ReviewUnavailable。"""
    for bad in ('{"verdict": "pass", "problems": "没有问题"}',
                '{"verdict": "pass", "problems": 123}'):
        monkeypatch.setattr(review.requests, "post", _fake_post(bad))
        with pytest.raises(review.ReviewUnavailable):
            review.review("k", "为什么这场战役只用了7天", "d",
                          [{"text": "无数字旁白" * 10}], [], retries=0)


def test_invalid_problem_elements_rejected(monkeypatch):
    """逐元素 schema 校验：任一元素非法 → 整体无效 → ReviewUnavailable（不静默过滤）。"""
    bad_bodies = [
        '{"verdict": "pass", "problems": [null]}',                       # 非对象
        '{"verdict": "pass", "problems": [{}]}',                         # 缺 issue
        '{"verdict": "pass", "problems": [{"issue": " ", "section": -1, "severity": "low"}]}',  # issue 空白
        '{"verdict": "pass", "problems": [{"issue": "x", "section": "2", "severity": "low"}]}', # section 非整数
        '{"verdict": "pass", "problems": [{"issue": "x", "section": -2, "severity": "low"}]}',  # section 越界
        '{"verdict": "pass", "problems": [{"issue": "x", "section": -1, "severity": "unknown"}]}',  # severity 非法
        '{"verdict": "pass", "problems": [{"issue": "x", "section": -1, "severity": "low", "suggest": 3}]}',  # suggest 非字符串
    ]
    for bad in bad_bodies:
        monkeypatch.setattr(review.requests, "post", _fake_post(bad))
        with pytest.raises(review.ReviewUnavailable):
            review.review("k", "为什么这场战役只用了7天", "d",
                          [{"text": "无数字旁白" * 10}], [], retries=0)


def test_pass_with_nonempty_problems_is_contradiction(monkeypatch):
    """pass 携带非空 problems = 结论矛盾：不得默默保留 pass 丢弃问题。"""
    body = ('{"verdict": "pass", "problems": [{"issue": "第2段配图存疑",'
            ' "section": 2, "severity": "medium", "suggest": "核对"}], "title_fix": ""}')
    monkeypatch.setattr(review.requests, "post", _fake_post(body))
    with pytest.raises(review.ReviewUnavailable):
        review.review("k", "为什么这场战役只用了7天", "d",
                      [{"text": "无数字旁白" * 10}], [], retries=0)


def test_valid_schema_accepted(monkeypatch):
    """完整合法结构正常通过；problems 规范化为可读字符串 + details 保留结构。"""
    body = ('{"verdict": "fix", "severity": "medium", "problems": ['
            '{"issue": "第2段配图偏现代", "section": 2, "severity": "medium", "suggest": "核对年代"}],'
            ' "title_fix": ""}')
    monkeypatch.setattr(review.requests, "post", _fake_post(body))
    out = review.review("k", "为什么这场战役只用了7天", "d",
                        [{"text": "无数字旁白" * 10}], [])
    assert out["verdict"] == "fix"
    assert out["problems"][0].startswith("第2段[medium]")
    assert out["details"][0]["section"] == 2


def test_service_timeout_raises(monkeypatch):
    """请求超时（requests.Timeout 是 RequestException 子类）→ 重试后 ReviewUnavailable。"""
    import requests as _rq
    calls = []

    def timeout_post(*a, **kw):
        calls.append(1)
        raise _rq.Timeout("timed out")

    monkeypatch.setattr(review.requests, "post", timeout_post)
    with pytest.raises(review.ReviewUnavailable):
        review.review("k", "为什么这场战役只用了7天", "d",
                      [{"text": "无数字旁白" * 10}], [], retries=1)
    assert len(calls) == 2


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
    body = ('```json\n{"verdict": "fix", "severity": "medium", "problems": '
            '[{"issue": "配图偏现代", "section": 2, "severity": "medium",'
            ' "suggest": "核对拍摄年代"}], "title_fix": ""}\n```')
    monkeypatch.setattr(review.requests, "post", _fake_post(body))
    out = review.review("k", "为什么这场战役只用了7天", "d",
                        [{"text": "无数字旁白" * 10}], [])
    assert out["verdict"] == "fix"
    assert out["problems"][0].startswith("第2段")
    assert out["details"][0]["section"] == 2


def test_title_fix_rejected_if_no_hook(monkeypatch):
    body = ('{"verdict": "fix", "problems": [], "title_fix": "某某战役解析"}')
    monkeypatch.setattr(review.requests, "post", _fake_post(body))
    out = review.review("k", "为什么这场战役只用了7天", "d",
                        [{"text": "无数字旁白" * 10}], [])
    assert out["title_fix"] == ""   # 审查官给的标题无钩子 → 不采纳


def test_high_severity_survives_for_human_gate(monkeypatch):
    body = ('{"verdict": "fix", "severity": "high", "problems": '
            '[{"issue": "伤亡数字存疑", "section": -1, "severity": "high",'
            ' "suggest": "核对战史"}], "title_fix": ""}')
    monkeypatch.setattr(review.requests, "post", _fake_post(body))
    out = review.review("k", "为什么这场战役只用了7天", "d",
                        [{"text": "无数字旁白" * 10}], [])
    assert any(d.get("severity") == "high" for d in out["details"])
