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


def test_falsy_problems_values_rejected(monkeypatch):
    """problems 假值不得被 `or []` 洗成空列表放行（0.7.4 补充整改 P1）。
    必填字段缺失/假值/非列表 → 一律重试后 ReviewUnavailable。"""
    bad_bodies = [
        '{"verdict": "pass", "problems": ""}',
        '{"verdict": "pass", "problems": null}',
        '{"verdict": "pass", "problems": 0}',
        '{"verdict": "pass", "problems": false}',
        '{"verdict": "pass", "problems": {}}',
        '{"verdict": "pass", "problems": "没有发现问题"}',
        '{"verdict": "pass", "problems": -1}',
        '{"verdict": "pass"}',                       # 必填字段缺失
    ]
    for bad in bad_bodies:
        monkeypatch.setattr(review.requests, "post", _fake_post(bad))
        with pytest.raises(review.ReviewUnavailable):
            review.review("k", "为什么这场战役只用了7天", "d",
                          [{"text": "无数字旁白" * 10}], [], retries=0)


def test_falsy_problems_rejected_after_retry(monkeypatch):
    """假值 problems 触发重试（不是首轮直接抛错），耗尽后 ReviewUnavailable。"""
    calls = []
    monkeypatch.setattr(review.requests, "post",
                        _fake_post('{"verdict": "pass", "problems": null}',
                                   calls=calls))
    with pytest.raises(review.ReviewUnavailable):
        review.review("k", "为什么这场战役只用了7天", "d",
                      [{"text": "无数字旁白" * 10}], [], retries=2)
    assert len(calls) == 3


def test_valid_empty_problems_list_accepted(monkeypatch):
    """problems 为正常空列表是合法响应（真·无问题）。"""
    monkeypatch.setattr(review.requests, "post",
                        _fake_post('{"verdict": "pass", "problems": [], "title_fix": ""}'))
    out = review.review("k", "为什么这场战役只用了7天", "d",
                        [{"text": "无数字旁白" * 10}], [])
    assert out["verdict"] == "pass" and out["problems"] == []


def test_top_level_severity_enum_enforced(monkeypatch):
    """顶层 severity 不在三值枚举 → 整体无效，不得部分采纳。"""
    monkeypatch.setattr(review.requests, "post",
                        _fake_post('{"verdict": "pass", "problems": [], "severity": "critical"}'))
    with pytest.raises(review.ReviewUnavailable):
        review.review("k", "为什么这场战役只用了7天", "d",
                      [{"text": "无数字旁白" * 10}], [], retries=0)


def test_top_level_title_fix_type_enforced(monkeypatch):
    """顶层 title_fix 非字符串 → 整体无效。"""
    monkeypatch.setattr(review.requests, "post",
                        _fake_post('{"verdict": "fix", "problems": [], "title_fix": 123}'))
    with pytest.raises(review.ReviewUnavailable):
        review.review("k", "为什么这场战役只用了7天", "d",
                      [{"text": "无数字旁白" * 10}], [], retries=0)


def test_hallucinated_quote_problem_dropped(monkeypatch):
    """审查官虚构"拿破仑的名言"（原文只有丘吉尔引用）→ 该问题被确定性过滤，
    无剩余真实问题时 verdict 降级为 pass——审查幻觉不得永久阻塞生产。"""
    body = ('{"verdict": "fix", "severity": "medium", "problems": ['
            '{"issue": "年代穿越，拿破仑的名言与敦刻尔克战役无关", "section": -1,'
            ' "severity": "medium", "suggest": "删除"}], "title_fix": ""}')
    monkeypatch.setattr(review.requests, "post", _fake_post(body))
    secs = [{"text": "丘吉尔说：我们将在海滩作战。三十万大军撤离了敦刻尔克。"}]
    out = review.review("k", "为什么这场战役只用了7天", "d", secs, [])
    assert out["verdict"] == "pass"
    assert out["problems"] == []
    assert any("拿破仑" in d for d in out["hallucinated_dropped"])


def test_real_quote_problem_kept(monkeypatch):
    """问题点名的作者真实出现在原文 → 保留，不误杀真问题。"""
    body = ('{"verdict": "fix", "severity": "medium", "problems": ['
            '{"issue": "丘吉尔的名言引用语境不当", "section": 1,'
            ' "severity": "medium", "suggest": "调整"}], "title_fix": ""}')
    monkeypatch.setattr(review.requests, "post", _fake_post(body))
    secs = [{"text": "丘吉尔说：我们将在海滩作战。"}]
    out = review.review("k", "为什么这场战役只用了7天", "d", secs, [])
    assert out["verdict"] == "fix"
    assert len(out["problems"]) == 1


def test_non_quote_problems_never_filtered(monkeypatch):
    """非名言类问题（配图匹配等）不受幻觉过滤影响，一律保留。"""
    body = ('{"verdict": "fix", "severity": "medium", "problems": ['
            '{"issue": "配图与内容无关", "section": 1, "severity": "medium",'
            ' "suggest": "换图"}], "title_fix": ""}')
    monkeypatch.setattr(review.requests, "post", _fake_post(body))
    secs = [{"text": "完全无关的旁白内容，没有名言。"}]
    out = review.review("k", "为什么这场战役只用了7天", "d", secs, [])
    assert out["verdict"] == "fix"          # 无 title_fix → 仍走人工闸门
    assert len(out["problems"]) == 1


def test_mixed_fabricated_and_real_problems(monkeypatch):
    """虚构+真实问题混合：虚构的丢弃，真实的保留，verdict 维持 fix。"""
    body = ('{"verdict": "fix", "severity": "medium", "problems": ['
            '{"issue": "拿破仑的名言与战役无关", "section": -1, "severity": "medium", "suggest": ""},'
            '{"issue": "配图偏现代", "section": 2, "severity": "medium", "suggest": "核对"}],'
            ' "title_fix": ""}')
    monkeypatch.setattr(review.requests, "post", _fake_post(body))
    secs = [{"text": "旁白甲。"}, {"text": "旁白乙提到配图相关。"}, {"text": "旁白丙。"}]
    out = review.review("k", "为什么这场战役只用了7天", "d", secs, [])
    assert out["verdict"] == "fix"
    assert len(out["problems"]) == 1 and "配图偏现代" in out["problems"][0]
    assert len(out["hallucinated_dropped"]) == 1


def test_reject_never_downgraded_by_filter(monkeypatch):
    """reject 结论不因问题被过滤而降级——否决是审查官的明确意志。"""
    body = ('{"verdict": "reject", "severity": "high", "problems": ['
            '{"issue": "拿破仑的名言属于年代穿越", "section": -1, "severity": "high",'
            ' "suggest": "删除"}], "title_fix": ""}')
    monkeypatch.setattr(review.requests, "post", _fake_post(body))
    secs = [{"text": "没有名言的旁白。"}]
    out = review.review("k", "为什么这场战役只用了7天", "d", secs, [])
    assert out["verdict"] == "reject"


def test_valid_severity_and_fix_title_accepted(monkeypatch):
    """合法 severity + 可采纳的 fix 标题正常通过。"""
    body = ('{"verdict": "fix", "severity": "high", "problems": [],'
            ' "title_fix": "凭什么7天就输了这场仗"}')
    monkeypatch.setattr(review.requests, "post", _fake_post(body))
    out = review.review("k", "为什么这场战役只用了7天", "d",
                        [{"text": "无数字旁白" * 10}], [])
    assert out["verdict"] == "fix" and out["title_fix"].startswith("凭什么")


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
