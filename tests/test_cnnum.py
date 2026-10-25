"""数字规范化边界测试（方案 P2）：年份/日期/数量/百分比/编号/区间全覆盖。

验收不只看"不含阿拉伯数字"，还检查关键读法是否符合自然中文。
"""
import re

from src.cnnum import normalize


def _no_digits(s: str) -> bool:
    return not re.search(r"\d", s)


def test_years():
    assert normalize("1944年") == "一九四四年"
    assert normalize("2000年") == "二零零零年"
    assert normalize("1900年") == "一九零零年"


def test_year_ranges():
    assert normalize("1941-1945年") == "一九四一至一九四五年"
    assert normalize("1941—1945年") == "一九四一至一九四五年"   # 中文破折号
    assert normalize("1941至1945年") == "一九四一至一九四五年"
    assert normalize("1941年至1945年") == "一九四一年至一九四五年"


def test_dates():
    assert normalize("6月4日") == "六月四日"
    assert normalize("12月31日") == "十二月三十一日"
    assert normalize("1944年6月") == "一九四四年六月"


def test_quantities():
    assert normalize("350架") == "三百五十架"
    assert normalize("2100辆") == "两千一百辆"
    assert normalize("30万人") == "三十万人"
    assert normalize("872天") == "八百七十二天"
    assert normalize("5分钟") == "五分钟"


def test_decimal_percent():
    assert normalize("3.5%") == "百分之三点五"
    assert normalize("80%") == "百分之八十"
    assert normalize("12.5公里") == "十二点五公里"


def test_equipment_codes_untouched():
    assert normalize("B-17") == "B-17"
    assert normalize("T-34坦克") == "T-34坦克"
    assert normalize("F-35与B-17编队") == "F-35与B-17编队"


def test_ordinals_and_ranges():
    assert normalize("第12次") == "第十二次"
    assert normalize("0胜") == "零胜"


def test_large_bare_numbers():
    assert normalize("伤亡100000人") == "伤亡十万人"
    assert normalize("约100000") == "约十万"
    assert normalize("300") == "三百"


def test_bare_decimal_not_lost():
    """裸小数不得丢小数部分（旧规则只转整数组，3.5 → '三'）。"""
    assert normalize("战损率达3.5") == "战损率达三点五"
    assert normalize("约12.5") == "约十二点五"
    assert normalize("半径0.5") == "半径零点五"


def test_mixed_sentence_natural_reading():
    s = "1943年7月，德军投入2100辆坦克，损失30万人，战损率3.5%，B-17轰炸机第12次出击。"
    out = normalize(s)
    # 装备编号（B-17）合法保留，其余不得残留数字
    assert _no_digits(re.sub(r"[A-Za-z]+-?\d+", "", out))
    assert "一九四三年七月" in out
    assert "两千一百辆" in out
    assert "三十万" in out
    assert "百分之三点五" in out
    assert "B-17" in out
    assert "第十二次" in out


def test_output_never_contains_digits_for_narration_inputs():
    samples = [
        "1941年至1945年，苏军累计动员3,000万人",   # 千分位逗号
        "6月22日凌晨4时", "库尔斯克会战双方伤亡约100000人",
        "第9集团军", "时速35英里",
    ]
    for s in samples:
        assert _no_digits(normalize(s)), s
    assert "三千万人" in normalize("3,000万人")
