"""GLM 军事史讲稿生成（两步法）：大纲（标题/要点/配图词/标签）→ 逐段展开控字数。

单段展开对字数指令的服从性远好于整篇生成。
"""
import json, re, requests

ZHIPU_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"

OUTLINE_PROMPT = """你是B站军事历史区编导，策划一期战争史科普视频（只讲历史，不涉当代时政）。
风格：严肃史实为主，但允许在叙述的间隙**适度玩梗**（B站流行语/网感吐槽，每1-2段至多一处，不破坏纪录片质感，涉及伤亡惨烈处禁用梗）。

主题：{topic}

输出 JSON，不要其他内容：
{{"title": "标题规则见下，30字内中文标题，不用感叹号",
  "tags": ["6个中文标签"],
  "sections": [{{"point": "该段要讲的史实要点（一句话，含具体细节如时间地点数字；若玩梗在此注明梗的位置和内容）",
                 "image_query": "2-4个英文单词的Wikimedia搜索词，如 'Midway aircraft 1942'。必须以战役地名/武器名开头（如 El Alamein/Tobruk/Tiger tank），禁止只用人物姓氏——人名会匹配到同名城市（Montgomery将军→阿拉巴马州蒙哥马利市）",
                 "stickers": [{{"emoji": "一个emoji字符", "at": 0.5, "why": "情绪点说明"}}],
                 "quote": {{"text": "名言原文", "author": "出自谁", "context": "何时何地说/写"}}}}]}}
共 {n_min}-{n_max} 段，构成起承转合。

标题规则（**硬性要求，平铺直叙即失败**）：不许用教科书式命名（如"XX战役：XX的兴衰/解析"）。
必须至少命中一种钩子结构，优先多个叠加（注意：钩子要体现在内容里，**严禁把"具体数字""悬念""反差"这类结构标签词面写进标题**）：
1. 反差错位——小人物×大武器 / 强者翻车（参考爆款："日本小伙把F35当回天鱼雷用"）
2. 悬念留白——"到底/为什么/凭什么/竟"或故意省略关键信息逼人点开
3. 具体数字——"7天""300人""0胜"，数字自带点开欲
4. 错误类比——"把X当Y用""XX的末路始于一次XX"

stickers 规则：**自行判断**哪些段落值得加表情贴纸——只在情绪高点/反差/玩梗处加（全篇0-4处，可以没有），at=贴纸出现时机（段内相对时间0-1），选与该句情绪匹配的 emoji。

quote 规则（**已连续多稿因名言翻车，从严执行**）：
- 只允许引用以下**当事人著作/演说**的原文：克劳塞维茨《战争论》、丘吉尔（战时演说或回忆录）、
  朱可夫《回忆与思考》、曼施坦因《失去的胜利》、艾森豪威尔《远征欧陆》、戴高乐《战争回忆录》。
- **禁用**：拿破仑、邓小平、林则徐等与本题战役无直接关系的名人名言；"据说/相传"的话；
  研究者总结的观点冒充当事人原话；拿不准出处的一律不引用。
- 数量：全篇 **0-2 处**，宁缺毋滥——没有合适的话就不设 quote 字段，不要硬凑。
  该段旁白应自然织入名言（引用后紧跟一句编导视角的回应或解读）。"""

EXPAND_PROMPT = """把以下史实要点扩写成纪录片旁白段落，要求：
- 中文 {c_lo}-{c_hi} 字（硬性要求，不足或超出都算失败）
- 口语化叙述，有细节有节奏，克制不煽情
- **严禁自行引入任何名人名言/人物语录**（如"正如拿破仑所说""XX曾言"）——
  除非要点中包含名言，且只能原样织入要点给的那一句，绝不添加其他人的话。
  编造名言是纪录片大忌，审查官会因此整稿否决。
- 只输出旁白正文，不要任何前缀、标题、引号包裹全文

要点：{point}"""

TITLE_FIX_PROMPT = """原标题「{title}」是平铺直叙的教科书式命名，没人想点。
按钩子规则重写军事史视频标题（30字内、不用感叹号、必须含具体人/武器/数字等冲突元素）：
1 反差错位 2 悬念留白 3 具体数字 4 错误类比。
只输出新标题，不要引号和其他内容。

视频主题：{topic}"""

_LABEL_RE = re.compile(r"反差错位|悬念留白|具体数字|错误类比|钩子")
_HOOK_RE = re.compile(
    r"到底|为什么|凭什么|为何|竟然|居然|究竟|谁|背后|真相|秘密|无人|没人"
    r"|把.{1,8}当|却|反被|败给|骗过|逼疯|终结|末路|翻车|救了|毁掉|\d|[?？]")


def _has_hook(title: str) -> bool:
    """标题钩子检测：命中钩子结构、非教科书式命名、且不带结构标签词面。"""
    return (bool(title) and len(title) <= 40
            and bool(_HOOK_RE.search(title)) and not _LABEL_RE.search(title))


def _refine_title(api_key: str, title: str, topic: str, model: str) -> str:
    """平铺直叙标题 → 单独重写一次（便宜，单条调用），失败保留原题。"""
    try:
        raw = _chat(api_key, TITLE_FIX_PROMPT.format(
            title=title, topic=topic), model, 0.9)
        # GLM 常返回多个候选（编号列表），拆行去编号，选第一个过钩子的
        cands = [re.sub(r'^\s*\d+[.、)]\s*', '', ln).strip().strip('"「」')
                 for ln in raw.splitlines()]
        for c in cands:
            if _has_hook(c):
                return c
        return title
    except requests.RequestException:
        return title


def _chat(api_key: str, prompt: str, model: str, temperature: float) -> str:
    r = requests.post(
        ZHIPU_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": model, "temperature": temperature,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _parse(raw: str):
    if not raw:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S)
    cands = [m.group(1)] if m else []
    brace = re.search(r"\{.*\}", raw, re.S)
    if brace:
        cands.append(brace.group(0))
    for c in cands:
        try:
            return json.loads(c)
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def _expand(api_key: str, point: str, model: str, temperature: str,
            c_lo: int, c_hi: int) -> str:
    """单段展开，字数不达标重试一次。"""
    text = ""
    for _ in range(2):
        text = _chat(api_key, EXPAND_PROMPT.format(
            point=point, c_lo=c_lo, c_hi=c_hi), model, temperature).strip()
        if c_lo - 15 <= len(text) <= c_hi + 30:
            return text
    return text


# 名言白名单：只允许当事人著作/演说（与 OUTLINE_PROMPT quote 规则一致）
QUOTE_AUTHOR_WHITELIST = ("丘吉尔", "克劳塞维茨", "朱可夫", "曼施坦因",
                          "艾森豪威尔", "戴高乐")
# 署名引语模式："正如拿破仑曾说""他沉痛地说道：""丘吉尔坚定地说："等——白名单外视为编造
# 捕获"名字+修饰语"整个前缀（含曾/军衔/状语），由 Python 在其中解析白名单作者
_ATTRIBUTION_RE = re.compile(
    r"[像如]?([\u4e00-\u9fa5A-Za-z·]{2,10}?)"
    r"(?:所?说的?|说道|所言|讲过|说过)[，,：:）”]")


def _resolve_speaker(span: str, allowed: set) -> str | None:
    """从"名字+修饰"前缀里解析白名单作者；不含任何允许作者 → None（编造）。"""
    for a in allowed:
        if a and a in span:
            return a
    return None


def _strip_fabricated_quotes(text: str, allowed: set) -> tuple[str, list]:
    """剥除旁白正文里白名单外的署名引语（含所在整句），返回 (清理后文本, 被剥句子)。

    glm-4-flash 的逐段扩写会在正文里自行编造"正如拿破仑所说…"式名言
    （quote 白名单只管 quote 字段，管不到正文），这是连续数稿被审查否决的
    根因。含编造署名的整句删除——名言是装饰，不值得为它丢整稿。
    """
    out_sents, stripped = [], []
    for sent in re.split(r"(?<=[。！？])", text):
        m = _ATTRIBUTION_RE.search(sent)
        if m and _resolve_speaker(m.group(1), allowed) is None:
            stripped.append(sent.strip())
            continue
        out_sents.append(sent)
    return "".join(out_sents), stripped


def generate(api_key: str, topic: str, model: str = "glm-4-flash",
             temperature: float = 0.7, n_min: int = 8, n_max: int = 11,
             c_lo: int = 70, c_hi: int = 110) -> dict:
    for _ in range(2):
        raw = _chat(api_key, OUTLINE_PROMPT.format(
            topic=topic, n_min=n_min, n_max=n_max), model, temperature)
        outline = _parse(raw)
        if outline and outline.get("sections"):
            break
    else:
        raise RuntimeError(f"outline generation failed: {topic}")

    sections = []
    for sec in outline["sections"]:
        text = _expand(api_key, sec["point"], model, temperature, c_lo, c_hi)
        allowed = set(QUOTE_AUTHOR_WHITELIST)
        if sec.get("quote", {}).get("author"):
            allowed.add(sec["quote"]["author"])
        text, stripped = _strip_fabricated_quotes(text, allowed)
        if stripped:
            print(f"[script] 剥除编造署名引语: {stripped}")
        sections.append({"text": text,
                         "image_query": sec.get("image_query") or topic,
                         "stickers": sec.get("stickers") or [],
                         "quote": sec.get("quote") or None})
    title = outline["title"]
    if not _has_hook(title):
        title = _refine_title(api_key, title, topic, model)
    return {"title": title, "sections": sections, "tags": outline["tags"]}
