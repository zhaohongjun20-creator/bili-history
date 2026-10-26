"""发布前审查闸门：无记忆审查官。

两层：
1. 确定性检查（零成本）：标题标签词/长度/钩子、规范化后旁白残留数字。
2. GLM 全新对话审查（无生成上下文，只看成品）：史实硬伤、配图匹配、标题质量。
   返回 pass / fix(带title_fix) / reject，reject 则产线不发布（宁缺毋滥）。

审查点在渲染之前：图选错、标题烂都在烧 10 分钟渲染前拦下。

可靠性（方案 P1-4）：服务超时/非法 JSON 时重试，仍失败抛 ReviewUnavailable——
调用方必须把选题置为 review_failed 等待重跑，**禁止把审查异常当 pass 放行**。
模型返回的问题带段落号与严重度（problems 为可读字符串列表，details 保留结构）。
"""
import json, re, time, requests

from .script import _has_hook, _LABEL_RE, ZHIPU_URL
from .cnnum import normalize as cn_normalize

REVIEW_PROMPT = """你是B站军事历史频道的独立内容审查官，审查一期即将发布的视频。
你与内容制作者无任何关联，只用挑剔的眼光找问题。

【标题】{title}
【简介】{desc}
【分段内容与配图】（第N段：该段讲什么 → 配的Wikimedia图片文件名）
{sections}

逐项审查：
1. 史实硬伤：年代穿越（如1941年出现1943年后的武器）、人物/战役/武器张冠李戴、数字明显离谱
2. 配图匹配：图片文件名与该段内容是否相关（人名地名撞车、纪念碑/现代照片混入也算问题）
3. 标题质量：是否把"反差错位""具体数字"这类结构标签词写进了标题；是否平铺直叙无钩子；是否有语病
4. 内容风险：涉及当代政治敏感、过度血腥描述；关键伤亡数字/名言是否需要来源核实
5. 旁白质量：明显语病、重复啰嗦、数字表达怪异

输出 JSON（不要其他内容）：
{{"verdict": "pass|fix|reject",
  "severity": "low|medium|high",
  "problems": [{{"issue": "每个问题一句话", "section": 段落号(整数,标题/整体填-1),
                 "severity": "low|medium|high",
                 "suggest": "修改建议或需要核实的来源类型"}}],
  "title_fix": "仅当标题有问题时给出修正标题（30字内，带钩子，否则空字符串）"}}
判定标准：史实硬伤≥2处或配图大面积跑偏 → reject；小问题 → fix；没问题 → pass。
AI 审查通过不代表史实绝对正确：凡涉及具体伤亡数字、争议性结论，请在 problems
里给出 severity=medium 并注明需人工核实，由产线转人工审核。"""


class ReviewUnavailable(RuntimeError):
    """审查服务持续不可用（超时/非法返回）——不得自动放行。"""


def _deterministic(title: str, secs: list) -> list:
    issues = []
    if _LABEL_RE.search(title):
        issues.append(f"标题含结构标签词面: {title[:20]}")
    if len(title) > 30:
        issues.append(f"标题超30字({len(title)}): {title[:20]}")
    if not _has_hook(title):
        issues.append(f"标题无钩子: {title}")
    for i, s in enumerate(secs):
        # cnnum 有意保留装备编号（B-17/T-34，字母邻接的数字），只查独立残留数字
        if re.search(r"(?<![A-Za-z0-9.\-])\d", cn_normalize(s["text"])):
            issues.append(f"第{i}段规范化后仍残留独立数字（TTS会乱读）")
            break
    return issues


_VALID_SEVERITIES = ("low", "medium", "high")


def _validate_problems(problems) -> None:
    """逐元素 schema 校验，任一非法即整体无效（fail-closed，不静默过滤）。

    规则：issue 非空字符串；section 为 ≥-1 的整数（bool 不算）；severity
    三值枚举；suggest 缺省空、出现则须为字符串。
    """
    if not isinstance(problems, list):
        raise ValueError(f"problems 不是列表: {problems!r}")
    for p in problems:
        if not isinstance(p, dict):
            raise ValueError(f"problems 元素不是对象: {p!r}")
        issue = p.get("issue")
        if not isinstance(issue, str) or not issue.strip():
            raise ValueError(f"problems 元素 issue 缺失或为空: {p!r}")
        sec = p.get("section", -1)
        if isinstance(sec, bool) or not isinstance(sec, int) or sec < -1:
            raise ValueError(f"problems 元素 section 非法: {p!r}")
        if p.get("severity") not in _VALID_SEVERITIES:
            raise ValueError(f"problems 元素 severity 非法: {p!r}")
        sug = p.get("suggest", "")
        if sug is not None and not isinstance(sug, str):
            raise ValueError(f"problems 元素 suggest 非字符串: {p!r}")


def _ask_reviewer(api_key: str, title: str, desc: str, secs: list,
                  image_titles: list, model: str) -> dict:
    """单次审查请求，返回解析且结构合法的 dict；任何结构问题抛 ValueError。"""
    sec_lines = "\n".join(
        f"第{i}段：{s['text'][:80]}…… → 配图: {image_titles[i] if i < len(image_titles) else '（无）'}"
        for i, s in enumerate(secs))
    r = requests.post(
        ZHIPU_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": model, "temperature": 0.2,
              "messages": [{"role": "user", "content": REVIEW_PROMPT.format(
                  title=title, desc=desc[:300], sections=sec_lines)}]},
        timeout=120,
    )
    r.raise_for_status()
    raw = r.json()["choices"][0]["message"]["content"]
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S) or \
        re.search(r"\{.*\}", raw, re.S)
    # 裸 JSON（无围栏）走第二个正则、无捕获组——group(1) 会 IndexError（旧版放行事故源）
    out = json.loads(m.group(1) if m.groups() else m.group(0)) if m else None
    if not isinstance(out, dict) or out.get("verdict") not in ("pass", "fix", "reject"):
        raise ValueError(f"审查返回无法解析: {str(raw)[:120]}")
    # problems 必填且必须是列表——不用 `or []` 兜底（假值 ""/null/0/false/{} 会被
    # 洗成空列表当"无问题"放行，fail-open 漏洞）
    if "problems" not in out:
        raise ValueError(f"审查返回缺 problems 字段: {str(raw)[:120]}")
    _validate_problems(out["problems"])
    # 顶层字段类型/枚举：不合规响应不得被部分采纳
    if "severity" in out and out["severity"] not in _VALID_SEVERITIES:
        raise ValueError(f"severity 非法: {out['severity']!r}")
    if "title_fix" in out and not isinstance(out["title_fix"], str):
        raise ValueError(f"title_fix 非字符串: {out['title_fix']!r}")
    # pass 携带非空问题 = 结论自相矛盾：不允许"默默保留 pass、丢弃问题"
    if out["verdict"] == "pass" and out["problems"]:
        raise ValueError(f"pass 携带非空 problems，结论矛盾: {str(raw)[:120]}")
    return out


def review(api_key: str, title: str, desc: str, secs: list,
           image_titles: list, model: str = "glm-4-flash",
           retries: int = 2) -> dict:
    """返回 {verdict, problems, title_fix, severity, details}。

    确定性失败直接 reject，不花 GLM。服务异常重试 retries 次后抛
    ReviewUnavailable（调用方应置 failed，等待重试，不放行）。
    确定性问题（标签词/残留数字/超长/无钩子）**一律 reject**——它们本应
    在生成层修正，流到审查层说明修正失败，不允许"记录问题后继续"。
    """
    problems = _deterministic(title, secs)
    if problems:
        return {"verdict": "reject", "problems": problems,
                "title_fix": "", "severity": "high", "details": []}

    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            out = _ask_reviewer(api_key, title, desc, secs, image_titles, model)
            break
        except (requests.RequestException, KeyError, ValueError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(3 * (attempt + 1))
    else:
        raise ReviewUnavailable(f"审查服务不可用（重试{retries}次）: {last_err}")

    details, flat = [], []
    for p in out["problems"]:                 # _ask_reviewer 已保证为合法列表
        if isinstance(p, dict):
            details.append(p)
            flat.append(f"第{p.get('section', '?')}段"
                        f"[{p.get('severity', '?')}] {p.get('issue', '')}")
        elif p:
            flat.append(str(p))
    flat.extend(problems)                      # 确定性问题（如有）并入

    out["problems"] = flat
    out["details"] = details
    out.setdefault("severity", "medium" if flat else "low")
    if out["verdict"] == "fix" and out.get("title_fix"):
        fix = str(out["title_fix"]).strip().strip('"「」')
        if _LABEL_RE.search(fix) or not _has_hook(fix):
            out["title_fix"] = ""  # 审查官自己给的标题不达标就不采纳
    return out
