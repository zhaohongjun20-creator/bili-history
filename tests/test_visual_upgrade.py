

"""0.8.0 观感升级测试：分句字幕 / 缓动表达式 / 设计封面降级路径。"""
import re

from src.slideshow import MAX_CUE_CHARS, _kenburns, _split_cues, make_srt


def test_split_cues_basic():
    cues = _split_cues("一九四四年八月，诺曼底的战局已见分晓。一个月前，盟军撕开德军防线。")
    assert len(cues) == 2 and cues[0].endswith("。") and cues[1].endswith("。")


def test_split_cues_long_sentence_packed_by_clauses():
    long = "德军第七集团军和第五装甲集团军且战且退，被盟军一路压向法莱斯镇，缺口成了唯一的通道。"
    cues = _split_cues(long)
    assert len(cues) >= 2
    for c in cues:
        assert len(c) <= MAX_CUE_CHARS + 12   # 贪心打包容忍少量超宽


def test_make_srt_sentence_split_weighted(tmp_path):
    voices = [
        {"duration": 10.0, "text": "第一句很短。第二句比第一句长了一倍不止，用来验证加权分时。"},
        {"duration": 4.0, "text": "末段一整句。"},
    ]
    p = tmp_path / "t.srt"
    make_srt(voices, str(p))
    body = p.read_text(encoding="utf-8")
    assert "1\n00:00:00,000 --> " in body
    assert "末段一整句。" in body          # 末段字幕存在
    assert "第二句比第一句长了一倍不止" in body
    # 条数 = 段1按句拆（2）+ 段2（1）= 3 条，块间分隔 2 个空行
    assert body.count("\n\n") == 2


def test_make_srt_ending_never_exceeds_total(tmp_path):
    voices = [{"duration": 5.0, "text": "甲句。乙句。丙句。"}]
    p = tmp_path / "t.srt"
    make_srt(voices, str(p))
    ends = re.findall(r"--> (\d+):(\d+):(\d+),(\d+)", p.read_text(encoding="utf-8"))
    last = sum(int(x) * f for x, f in zip(ends[-1], (3600, 60, 1, 0.001)))
    assert last <= 5.001                 # 末条不超出该段总时长


def test_kenburns_has_easing():
    for mode in ("in", "out", "panright", "panleft"):
        expr = _kenburns(mode, 750)
        assert "cos(PI*on/" in expr      # 余弦缓入缓出
        assert "0.0006*on" not in expr   # 旧线性推拉下线
