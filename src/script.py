"""GLM 军事史讲稿生成（两步法）：大纲（标题/要点/配图词/标签）→ 逐段展开控字数。

单段展开对字数指令的服从性远好于整篇生成。
"""
import json, re, requests

ZHIPU_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"

OUTLINE_PROMPT = """你是B站军事历史区编导，策划一期战争史科普视频（只讲历史，不涉当代时政）。
主题：{topic}

输出 JSON，不要其他内容：
{{"title": "40字内中文标题，克制有张力，不用感叹号",
  "tags": ["6个中文标签"],
  "sections": [{{"point": "该段要讲的史实要点（一句话，含具体细节如时间地点数字）",
                 "image_query": "2-4个英文单词的Wikimedia搜索词，如 'Midway aircraft 1942'"}}]}}
共 {n_min}-{n_max} 段，构成起承转合。"""

EXPAND_PROMPT = """把以下史实要点扩写成纪录片旁白段落，要求：
- 中文 {c_lo}-{c_hi} 字（硬性要求，不足或超出都算失败）
- 口语化叙述，有细节有节奏，克制不煽情
- 只输出旁白正文，不要任何前缀、标题、引号

要点：{point}"""


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
        sections.append({"text": text, "image_query": sec["image_query"]})
    return {"title": outline["title"], "sections": sections, "tags": outline["tags"]}
