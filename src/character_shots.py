"""三五句 brief → 编号分镜提示词。

给军事号里需要生成角色表演的镜头用。调用本仓库已有的 GLM
（``src.script._chat``，``ZHIPU_API_KEY``，默认 ``glm-4-flash``）。
不生成视频，不接入 ``autopost.py``、投稿、幻灯片或成片质检。

「禁止静帧平移」只用于生成出来的角色镜。史料静帧的 Ken Burns
（推近 / 拉远 / 左移 / 右移）和静帧滑动保持允许，检查不因此判失败，
重写时也不改运镜方向。生成角色镜的总规格必须写明「生成表演」。
"""
import os
import re
from dataclasses import dataclass, field

DEFAULT_MODEL = "glm-4-flash"
_PLACEHOLDER_KEYS = {"", "your_zhipu_key_here"}

BLOCKS = ("总规格", "锁定元素", "按秒分镜", "禁止项")
CHECKS = ("手脚重心", "字先可读", "浅色遮罩")
_FIELD_NAMES = BLOCKS + CHECKS + ("时码", "镜类", "提示词", "检查", "状态")

_CUT_HEAD = re.compile(
    r"(?m)^[ \t]*(?:#{1,3}[ \t]*)?CUT[ \t]*[:：]?[ \t]*(\d+)"
    r"(?:[ \t\u3000]+(\d{2}:\d{2}\.\d{2}\s*[–—\-]\s*\d{2}:\d{2}\.\d{2}))?"
    r"(?:[ \t\u3000]+(\S.*?))?[ \t\u3000]*$"
)
_FIELD = re.compile(
    r"(?m)^[ \t]*[-*]?[ \t]*(" + "|".join(_FIELD_NAMES) + r")[ \t]*[:：][ \t]*(.*)$"
)
_FENCE = re.compile(r"```(?:markdown|md)?\s*(.*?)```", re.S | re.I)

# 角色镜的「按秒分镜」若只在滑一张静帧，判失败。禁止项里的「禁止静帧平移」不在这里看。
_STILL_SLIDE = re.compile(
    r"静帧(?:平移|滑动|推拉|滑过|从)|"
    r"(?:整张|一张|全身)(?:角色)?静帧|"
    r"手脚和重心(?:都)?(?:保持)?不动|"
    r"缓慢移动全身|站桩|"
    r"只是(?:画面|整张|人物|角色)在(?:滑|移|推|拉)|"
    r"(?:人物|角色|人)不动"
)
_SLIDE_MOVE = re.compile(
    r"(?:从左|从右|向左|向右|自左|自右).{0,16}(?:平移|滑到|滑过|移到|挪到)"
)
_KEN_BURNS = re.compile(r"Ken Burns|肯伯恩斯", re.I)
_MOTION = re.compile(
    r"摆臂|摆动|蹬地|交替|起跳|双臂|双脚|"
    r"重心(?:下沉|起伏|前移|转移|跟着|随)|"
    r"连续(?:跑|冲|跳|翻|滑|表演|动作|起)|"
    r"手脚(?!和重心(?:都)?(?:保持)?不动).{0,8}(?:在动|摆|蹬|交替)|"
    r"迈步|跨步|后空翻|前滚|"
    r"走入|走过|迈开|停步|举手|平举|步伐"
)
# 「不禁止」「并未禁止」不算把运镜写成禁止项。
_FORBID_PAN = re.compile(
    r"(?<![不未])(?:禁止|严禁|不要|不能)[^。\n]{0,24}"
    r"(?:静帧平移|静帧滑动|静帧推拉|Ken Burns|肯伯恩斯|推近|拉远|左移|右移)"
)
_AO_TOKEN = re.compile(r"(?<![A-Za-z])AO(?![A-Za-z])")

HEADER = """# 编号分镜提示词

由 brief 生成，给军事号里需要生成角色表演的镜头粘贴。不接入日更生产线，也不生成视频。
「禁止静帧平移」只约束生成角色镜。史料静帧的 Ken Burns（推近 / 拉远 / 左移 / 右移）、缩放和静帧滑动保持允许，禁止项不得把它们写成失败。
生成的角色镜须标明是生成表演，不得写成史料实拍或历史照片。
镜数、时长、画幅以 brief 为准。AO 预告的 13 镜只是格式示例，不是这份清单的结构。
不添加 brief 里没有的情节、道具、台词、武器或结局，不编造史实。
"""

DELIVERY = """## 交片提醒

1. 先交一条完整、可以看完的成片母带，再按时间码切出带编号的短片段，文件名写上源片时间范围。不要只交静帧或压缩包。24 帧对时码可能略有偏差，切片以源片时间码为准。
2. 提示词过关不等于成片过关。有角色、有字、有浅色衣服或旗帜的镜头，还要在成片上查手脚和重心是否真的在动、字会不会被挡住、浅色遮罩有没有破边、破洞、手或装具脱落。
3. 浅色遮罩破了不要用生成像素去补，重拍或换镜。标了「未通过」的镜不要拿去生成。
4. 史料静帧段落标「Ken Burns，不查角色手脚」。生成表演不得写成史料实拍或历史照片。
"""


@dataclass
class Shot:
    number: int
    timecode: str = ""
    kind: str = ""
    blocks: dict = field(default_factory=dict)
    checks: dict = field(default_factory=dict)


def _strip_fence(text: str) -> str:
    m = _FENCE.search(text or "")
    if m and re.search(r"CUT", m.group(1)):
        return m.group(1)
    return text or ""


def _extract_fields(body: str) -> dict:
    matches = list(_FIELD.finditer(body))
    fields = {}
    for i, m in enumerate(matches):
        name = m.group(1)
        if name in ("提示词", "检查", "状态"):
            continue
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        inline = m.group(2).strip()
        tail = body[m.end():end].strip()
        fields[name] = (inline + ("\n" + tail if tail else "")).strip()
    return fields


def _section_end(raw: str, start: int, next_cut: int) -> int:
    """镜头正文停在下一镜，也停在交片提醒这类后附标题，避免检查项把后文吞进去。"""
    m = re.search(r"(?m)^#{1,3}[ \t]+(?!CUT\b)", raw[start:next_cut])
    if m:
        return start + m.start()
    return next_cut


def parse_shots(text: str) -> list[Shot]:
    """按 CUT 编号切开。四段和三项检查缺了就留空，交给 shot_problems。"""
    raw = _strip_fence(text)
    heads = list(_CUT_HEAD.finditer(raw))
    shots = []
    for i, head in enumerate(heads):
        next_cut = heads[i + 1].start() if i + 1 < len(heads) else len(raw)
        end = _section_end(raw, head.end(), next_cut)
        fields = _extract_fields(raw[head.end():end])
        kind = (fields.get("镜类") or (head.group(3) or "")).strip()
        timecode = (fields.get("时码") or (head.group(2) or "")).strip()
        shots.append(Shot(
            number=int(head.group(1)),
            timecode=timecode,
            kind=kind,
            blocks={name: fields.get(name, "").strip() for name in BLOCKS},
            checks={name: fields.get(name, "").strip() for name in CHECKS},
        ))
    return shots


def classify(kind: str) -> str:
    """史料 / 海报 / 纯图形不按角色静帧平移判失败。其余有镜类的镜头视为角色镜。"""
    if "史料" in kind:
        return "archival"
    if "海报" in kind:
        return "poster"
    if "纯图形" in kind or "只有眼睛" in kind:
        return "graphic"
    if kind.strip():
        return "character"
    return ""


def still_slide_only(performance: str) -> bool:
    """按秒分镜只在滑一张静帧、没有手脚和重心的连续动作。"""
    text = performance or ""
    if not text.strip():
        return False
    slid = bool(_STILL_SLIDE.search(text) or _SLIDE_MOVE.search(text)
                or _KEN_BURNS.search(text))
    return slid and not _MOTION.search(text)


def forbids_archival_pan(prohibition: str) -> bool:
    return bool(_FORBID_PAN.search(prohibition or ""))


def shot_problems(shot: Shot) -> list[str]:
    """便宜的结构检查，外加角色镜静帧平移、史料镜误禁 Ken Burns。"""
    problems = []
    if not shot.timecode.strip():
        problems.append("缺少时码")
    if not shot.kind.strip():
        problems.append("缺少镜类")
    for name in BLOCKS:
        if not shot.blocks.get(name, "").strip():
            problems.append(f"缺少{name}")
    for name in CHECKS:
        if not shot.checks.get(name, "").strip():
            problems.append(f"缺少检查「{name}」")
    kind = classify(shot.kind)
    if kind == "character" and "生成表演" not in shot.blocks.get("总规格", ""):
        problems.append("角色镜总规格未标明是生成表演")
    if kind == "character" and still_slide_only(shot.blocks.get("按秒分镜", "")):
        problems.append("角色镜只有静帧平移，没有连续表演")
    if kind == "archival" and forbids_archival_pan(shot.blocks.get("禁止项", "")):
        problems.append("史料静帧把 Ken Burns 或静帧平移写成了禁止项")
    return problems


def expected_count(brief: str) -> int | None:
    """brief 写了镜数才核对。没写就不替片子规定镜数，也不默认 13。"""
    labeled = re.findall(
        r"(?:镜数|共|只要|做成)\s*[:：]?\s*(\d+)\s*(?:镜|个镜头)", brief)
    if not labeled:
        labeled = re.findall(r"镜数\s*[:：]\s*(\d+)", brief)
    if labeled:
        return int(labeled[-1])
    nums = re.findall(r"(\d+)\s*镜", brief)
    if len(nums) == 1:
        return int(nums[0])
    return None


def document_issue(brief: str, shots: list[Shot]) -> str | None:
    if not shots:
        return "没有解析到任何 CUT"
    count = expected_count(brief)
    if count is not None and len(shots) != count:
        return (f"镜数是 {len(shots)}，brief 要求 {count}。"
                "不要套用 AO 预告的 13 镜")
    if not _AO_TOKEN.search(brief) and any(
            _AO_TOKEN.search("\n".join(
                [s.kind, *s.blocks.values(), *s.checks.values()]))
            for s in shots):
        return "输出里出现了 brief 没有的角色 AO。AO 的 13 镜只是格式示例"
    return None


def _rules_block() -> str:
    return """你按本仓库的军事号规则，把 brief 写成带编号的分镜提示词。只给需要生成角色表演的镜头用。不要写纪录片讲稿，不要生成视频。

必须遵守：
1. 有跑、跳、翻、滑或其他身体表演的生成角色镜，按秒分镜要写连续表演：手脚和重心在动，走入、停步、举手也算表演。禁止把整个人写成一张静帧再平移、推拉或滑动。这条只管生成出来的角色，不管史料图。
2. 「禁止静帧平移」只写在生成角色镜的禁止项里。镜类可以是史料静帧。史料静帧继续允许 Ken Burns：推近、拉远、左移、右移，以及缩放和静帧滑动。史料镜的禁止项不要写禁止静帧平移，也不要改它的运镜方向，不要把史料图改成角色在表演。
3. 片上的字先完整可读，再让角色重叠。浅色军装、浅色装备、旗帜不要贴浅色、雪地、灰墙或网格背景硬抠。交片前查破边、破洞、手和装具脱落。浅色遮罩破了写明重拍或换镜，不用生成像素去补。
4. 可以定格，但必须是动作做到那一帧再停。海报定格允许静帧。纯图形镜（或只有眼睛的近景）不查手脚。
5. 不添加 brief 里没有的情节、道具、台词、武器或结局，不编造史实。
6. 生成的角色镜须在总规格标明是生成表演，不得写成史料实拍或历史照片。
7. 镜数、时长、画幅、风格和表演方式都以 brief 为准，开拍前一次定死。AO 预告的 13 镜只是格式示例，不是必填结构，不要套用那里的镜数、角色或台词。军史格式示例（一镜史料静帧加一镜角色走入）只是占位，人物、地点、服装都不是史实，brief 没写就不要抄进去。

每一镜用下面的格式，检查三项分行写，不要合成一行：

## CUT 01
时码: 00:00.00–00:00.00
镜类: 纯图形 / 角色表演 / 图形+角色 / 动作后定格 / 海报定格 / 史料静帧
提示词:
  总规格：画幅、本镜时长、风格、这一镜做什么。角色镜写明「生成表演」
  锁定元素：已锁定的角色、服装、光线、场景、片上的字
  按秒分镜：角色镜写连续表演（手脚、重心、视线、走入、停步、举手）。史料静帧只写 Ken Burns 方向，不要写成角色在动
  禁止项：角色镜才写禁止静帧平移，并写明不得伪装成史料。史料静帧不要把 Ken Burns 写成禁止项
检查:
  手脚重心：不查 / 必须连续动 / 可在动作做到后定格 / 史料静帧（Ken Burns，不按静帧平移判失败）
  字先可读：无字 / 先完整可读再重叠
  浅色遮罩：不查 / 查破边、破洞、手和装具脱落；破了重拍或换镜，不用生成像素补

只输出这些 CUT。不要代码围栏，不要交片清单（交片提醒由程序另附）。"""


def build_prompt(brief: str) -> str:
    return _rules_block() + "\n\nbrief：\n" + brief.strip() + "\n"


def render_shot(shot: Shot, problems: list[str] | None = None) -> str:
    problems = problems if problems is not None else shot_problems(shot)
    lines = [
        f"## CUT {shot.number:02d}",
        "",
        f"时码: {shot.timecode}",
        f"镜类: {shot.kind}",
        "提示词:",
    ]
    for name in BLOCKS:
        lines.append(f"  {name}：{shot.blocks.get(name, '').strip()}")
    lines.append("检查:")
    for name in CHECKS:
        lines.append(f"  {name}：{shot.checks.get(name, '').strip()}")
    if problems:
        lines.append("状态: 未通过。" + "；".join(problems))
    return "\n".join(lines)


def render(shots: list[Shot], note: str | None = None) -> str:
    failed = []
    for shot in shots:
        if shot_problems(shot):
            failed.append(shot)
    parts = [HEADER.rstrip()]
    bits = []
    if note:
        bits.append(note)
    if failed:
        bits.append("、".join(f"CUT {shot.number:02d}" for shot in failed))
    if bits:
        parts.append("> 检查未通过，不要直接送去生成：" + "；".join(bits) + "。")
    for shot in shots:
        parts.append(render_shot(shot))
    parts.append(DELIVERY.rstrip())
    return "\n\n".join(parts) + "\n"


def build_rewrite_prompt(brief: str, shots: list[Shot], doc_issue: str | None) -> str:
    lines = [
        build_prompt(brief),
        "",
        "上一次输出没有通过检查。只重写有问题的镜头，镜号保持不变。",
        "不要添加 brief 里没有的情节、道具、台词、武器或结局，不编造史实。",
        "「禁止静帧平移」只改生成角色镜。史料 Ken Burns / 静帧滑动保持原方向，不要改写，也不要禁止。",
    ]
    if doc_issue:
        lines.append("整份问题：" + doc_issue)
        lines.append("请按 brief 重写全部 CUT。")
    else:
        lines.append("只返回下面这些 CUT 的完整四段和三项检查。已经通过的镜头不要改。")
    for shot in shots:
        problems = shot_problems(shot)
        if not doc_issue and not problems:
            continue
        lines.append(render_shot(shot, problems))
        if problems:
            lines.append("问题：" + "；".join(problems))
        if classify(shot.kind) == "archival":
            lines.append(
                "这是史料静帧。保留原来的 Ken Burns 方向（推近、拉远、左移、右移或静帧滑动），"
                "不要改成角色在动，禁止项里不要写禁止静帧平移。")
        elif classify(shot.kind) == "character" and problems:
            lines.append(
                "这是生成角色镜。总规格必须标明「生成表演」，"
                "不得写成史料实拍或历史照片。")
            if any("静帧平移" in p for p in problems):
                lines.append(
                    "按秒分镜必须写出手脚和重心的连续动作，"
                    "不能只把一张静帧平移、推拉或滑动。禁止项写明禁止静帧平移。"
                    "不要把这条禁令套到史料 Ken Burns 上。")
    return "\n\n".join(lines) + "\n"


def _merge(original: list[Shot], rewritten: list[Shot],
           replace_all: bool) -> list[Shot]:
    if replace_all:
        return rewritten or original
    by_num = {s.number: s for s in rewritten}
    merged = []
    for shot in original:
        if shot_problems(shot) and shot.number in by_num:
            merged.append(by_num[shot.number])
        else:
            merged.append(shot)
    return merged


def generate_shot_markdown(brief: str, chat) -> str:
    """chat(prompt) -> 模型原文。结构不合格或角色镜只是静帧平移时，重写一次。"""
    text = (brief or "").strip()
    if not text:
        raise ValueError("brief 是空的")
    shots = parse_shots(chat(build_prompt(text)))
    issue = document_issue(text, shots)
    failed = [s for s in shots if shot_problems(s)]
    if issue or failed or not shots:
        rewritten = parse_shots(chat(build_rewrite_prompt(text, shots, issue)))
        shots = _merge(shots, rewritten, replace_all=bool(issue) or not shots)
    if not shots:
        raise RuntimeError(
            "模型没有返回可解析的分镜（需要带编号的 CUT，以及总规格、锁定元素、"
            "按秒分镜、禁止项和三项检查）")
    shots = sorted(shots, key=lambda s: s.number)
    return render(shots, document_issue(text, shots))


def _call_glm(prompt: str, model: str) -> str:
    """走智谱 chat completions。不读 magpie 网关，也不换别的供应商。"""
    key = os.environ.get("ZHIPU_API_KEY", "").strip()
    if key in _PLACEHOLDER_KEYS:
        raise SystemExit(
            "缺少 ZHIPU_API_KEY。在 .env 或环境变量里填写，与讲稿用的是同一把 GLM 钥匙。")
    from src.script import _chat
    return _chat(key, prompt, model, 0.3)


def generate_from_brief(brief: str, chat=None, model: str = DEFAULT_MODEL) -> str:
    if chat is None:
        def chat(prompt: str, _model=model) -> str:
            return _call_glm(prompt, _model)
    return generate_shot_markdown(brief, chat)
