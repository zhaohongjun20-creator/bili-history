"""名言编造防线测试：写稿层剥除 + 审核层确定性兜底（四稿名言翻车根因修复）。

背景：glm-4-flash 逐段扩写会在旁白正文里自行编造“正如拿破仑所说…”式名言，
quote 白名单只管 quote 结构化字段管不到正文——审查官连续四稿正确否决。
"""
from src.script import QUOTE_AUTHOR_WHITELIST, _strip_fabricated_quotes
from src.review import _deterministic


def test_strip_removes_nonwhitelisted_attribution():
    text = ("1940年5月英法联军陷入绝境。就像拿破仑曾说的，“战争是勇敢者的游戏。”"
            "这句话道出了残酷。部队开始撤退。")
    out, stripped = _strip_fabricated_quotes(text, set(QUOTE_AUTHOR_WHITELIST))
    assert "拿破仑" not in out
    assert len(stripped) == 1
    assert "部队开始撤退" in out          # 其余句子保留


def test_strip_keeps_whitelisted_authors():
    text = "丘吉尔说：“我们将在海滩作战。”朱可夫在回忆录中写道：“反攻开始了。”"
    out, stripped = _strip_fabricated_quotes(text, set(QUOTE_AUTHOR_WHITELIST))
    assert stripped == [] and out == text


def test_strip_allows_section_own_quote_author():
    """本段 quote 字段的作者即使不在全局白名单也放行。"""
    text = "亚历山大曾说：“撤退也是艺术。”随后部队渡海。"
    out, stripped = _strip_fabricated_quotes(text, {"亚历山大"})
    assert stripped == [] and "亚历山大" in out


def test_review_deterministic_catches_fabricated_attribution():
    """审核层兜底：正文含白名单外署名引语 → 确定性 reject（写稿层漏网时）。"""
    secs = [{"text": "正如拿破仑所说：“战争是勇敢者的游戏。”部队撤退了。"}]
    issues = _deterministic("为什么这场战役只用了7天", secs)
    assert any("白名单外署名引语" in p and "拿破仑" in p for p in issues)


def test_review_deterministic_passes_clean_sections():
    secs = [{"text": "丘吉尔说：“我们将在海滩作战。”三十万人得以撤离。"}]
    issues = _deterministic("为什么这场战役只用了7天", secs)
    assert not any("署名引语" in p for p in issues)


def test_strip_allows_adverbial_modifier():
    """名字与动词之间的状语（"坚定地"）不吞进名字——白名单作者不误伤。"""
    text = "在混乱的撤退中，丘吉尔坚定地说：“我们失去了装备。”随后部队渡海。"
    out, stripped = _strip_fabricated_quotes(text, set(QUOTE_AUTHOR_WHITELIST))
    assert stripped == [] and "丘吉尔坚定地说" in out


def test_strip_still_catches_adverbial_fabrication():
    """状语修饰的编造署名仍要抓：拿破仑沉痛地说道 → 剥除。"""
    text = "拿破仑沉痛地说道：“装备失去了。”部队撤退。"
    out, stripped = _strip_fabricated_quotes(text, set(QUOTE_AUTHOR_WHITELIST))
    assert "拿破仑" not in out and len(stripped) == 1
