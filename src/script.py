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
                 "image_query": "2-4个英文单词的Wikimedia搜索词，如 'Midway aircraft 1942'",
                 "stickers": [{{"emoji": "一个emoji字符", "at": 0.5, "why": "情绪点说明"}}],
                 "quote": {{"text": "名言原文", "author": "出自谁", "context": "何时何地说/写"}}}}]}}
共 {n_min}-{n_max} 段，构成起承转合。

标题规则（**硬性要求，平铺直叙即失败**）：不许用教科书式命名（如"XX战役：XX的兴衰/解析"）。
必须至少命中一种钩子结构，优先多个叠加：
1. 反差错位——小人物×大武器 / 强者翻车（参考爆款："日本小伙把F35当回天鱼雷用"）
2. 悬念留白——"到底/为什么/凭什么/竟"或故意省略关键信息逼人点开
3. 具体数字——"7天""300人""0胜"，数字自带点开欲
4. 错误类比——"把X当Y用""XX的末路始于一次XX"

stickers 规则：**自行判断**哪些段落值得加表情贴纸——只在情绪高点/反差/玩梗处加（全篇0-4处，可以没有），at=贴纸出现时机（段内相对时间0-1），选与该句情绪匹配的 emoji。

quote 规则：**自行判断**哪些段落适合引用文章/回忆录/名人名言（全篇1-3处）——优先克劳塞维茨《战争论》、丘吉尔演讲/回忆录、朱可夫回忆录、曼施坦因《失去的胜利》、戴高乐、艾森豪威尔等当事人的著作或演说。**硬性要求：只用真实存在且广为流传的名言，给出处；拿不准的一律不用**（宁缺毋滥，编造名言是纪录片大忌）。该段旁白应自然织入这句名言（引用后紧跟一句编导视角的回应或解读）。"""

EXPAND_PROMPT = """把以下史实要点扩写成纪录片旁白段落，要求：
- 中文 {c_lo}-{c_hi} 字（硬性要求，不足或超出都算失败）
- 口语化叙述，有细节有节奏，克制不煽情
- 若要点包含名言引用：将名言原文自然织入旁白（用引号），引用后接一句你的解读
- 只输出旁白正文，不要任何前缀、标题、引号包裹全文

要点：{point}"""

TITLE_FIX_PROMPT = """原标题「{title}」是平铺直叙的教科书式命名，没人想点。
按钩子规则重写军事史视频标题（30字内、不用感叹号、必须含具体人/武器/数字等冲突元素）：
1 反差错位 2 悬念留白 3 具体数字 4 错误类比。
只输出新标题，不要引号和其他内容。

视频主题：{topic}"""

_HOOK_RE = re.compile(
    r"到底|为什么|凭什么|为何|竟然|居然|究竟|谁|背后|真相|秘密|无人|没人"
    r"|把.{1,8}当|却|反被|败给|骗过|逼疯|终结|末路|翻车|救了|毁掉|\d|[?？]")


def _has_hook(title: str) -> bool:
    """标题钩子检测：命中任一钩子结构且非教科书式命名。"""
    return bool(title) and len(title) <= 40 and bool(_HOOK_RE.search(title))


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
        sections.append({"text": text,
                         "image_query": sec.get("image_query") or topic,
                         "stickers": sec.get("stickers") or [],
                         "quote": sec.get("quote") or None})
    title = outline["title"]
    if not _has_hook(title):
        title = _refine_title(api_key, title, topic, model)
    return {"title": title, "sections": sections, "tags": outline["tags"]}
