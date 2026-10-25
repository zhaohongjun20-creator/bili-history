"""中文数字规范化：阿拉伯数字 → 汉字，旁路 TTS 文本正则化（TN）。

背景：CosyVoice2 的 wetext TN 不可靠——"1944年"被读成"十九四四年"。
产线在送合成前把数字统一转成汉字（年份逐位读"一九四四"、数量按中文
进位"三百五十"、日期"六月四日"），TTS 只负责读，不再猜。

军史旁白高频数字形态：年份/日期/数量(架辆艘人…)/百分比/序数(第N)。
字母编号（B-17、T-34、F-35）不动——TTS 读字母本身没问题。
"""
import re

DIGITS = "零一二三四五六七八九"


def _int_to_cn(n: int) -> str:
    """整数 → 中文数量读法（三百五十/一千零八/两万三千）。"""
    if n < 10:
        return DIGITS[n]
    if n < 100:
        t, o = divmod(n, 10)
        s = "十" if t == 1 else DIGITS[t] + "十"
        return s + (DIGITS[o] if o else "")
    if n < 1000:
        h, r = divmod(n, 100)
        s = DIGITS[h] + "百"
        if r == 0:
            return s
        return s + ("零" + DIGITS[r] if r < 10 else _int_to_cn(r))
    if n < 10000:
        th, r = divmod(n, 1000)
        s = DIGITS[th] + "千"
        if r == 0:
            return s
        return s + ("零" + _int_to_cn(r) if r < 100 else _int_to_cn(r))
    w, r = divmod(n, 10000)
    s = _int_to_cn(w) + "万"
    if r == 0:
        return s
    return s + ("零" + _int_to_cn(r) if r < 1000 else _int_to_cn(r))


def _num_to_cn(num: str) -> str:
    """支持小数：3.5 → 三点五。"""
    if "." in num:
        i, f = num.split(".")
        return (_int_to_cn(int(i)) if i else "零") + "点" + \
               "".join(DIGITS[int(c)] for c in f)
    return _int_to_cn(int(num))


def _digits_to_cn(num: str) -> str:
    """逐位读：1944 → 一九四四（年份/编号用）。"""
    return "".join(DIGITS[int(c)] for c in num)


# 保护：字母/连字符旁的数字是装备编号（B-17、F-35、T-34），不动
_LB = r"(?<![A-Za-z0-9.\-])"
_LA = r"(?![A-Za-z0-9])"

_RULES = [
    # 百分比：80% → 百分之八十
    (re.compile(r"(\d+(?:\.\d+)?)\s*[%％]"), lambda m: "百分之" + _num_to_cn(m.group(1))),
    # 年份（含区间后年）：1944年 / 1941-1945年 → 逐位读
    (re.compile(_LB + r"(1[89]\d{2}|20\d{2})\s*-\s*((?:1[89]\d{2}|20\d{2}))\s*年"),
     lambda m: _digits_to_cn(m.group(1)) + "至" + _digits_to_cn(m.group(2)) + "年"),
    (re.compile(_LB + r"(1[89]\d{2}|20\d{2})\s*年"),
     lambda m: _digits_to_cn(m.group(1)) + "年"),
    # 日期：6月4日 → 六月四日
    (re.compile(_LB + r"(\d{1,2})\s*月"), lambda m: _num_to_cn(m.group(1)) + "月"),
    (re.compile(_LB + r"(\d{1,3})\s*日"), lambda m: _num_to_cn(m.group(1)) + "日"),
    # 序数：第2次 → 第二次
    (re.compile(_LB + r"第\s*(\d+)"), lambda m: "第" + _num_to_cn(m.group(1))),
    # 带单位数量：350架/872天/30万/5分钟/2100辆 → 中文进位
    (re.compile(_LB + r"(\d+(?:\.\d+)?)(架|辆|艘|人|次|天|小时|分钟|秒|米|公里|"
                 r"毫米|厘米|吨|门|发|个|座|名|师|团|营|连|排|万|亿|倍|度|轮|波|批|处)"),
     lambda m: _num_to_cn(m.group(1)) + m.group(2)),
    # 剩余独立数字（≤4位，非编号）：300 → 三百
    (re.compile(_LB + r"(\d{1,4})(?:\.\d+)?" + _LA),
     lambda m: _num_to_cn(m.group(1))),
]


def normalize(text: str) -> str:
    """把旁白文本里的数字全部转成汉字。"""
    out = text
    for pat, fn in _RULES:
        out = pat.sub(fn, out)
    return out.replace("二千", "两千").replace("二万", "两万")
