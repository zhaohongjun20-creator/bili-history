"""总字数不够时只补写偏短的段，不重出整篇大纲。"""
import json
import logging
from pathlib import Path

import autopost
from src import script as script_mod
from src.jobs import Job

# 配置 80–130，接受窗口是 65–160。低于 65 才算偏短。
C_LO, C_HI = 80, 130
TOPIC = "1942年阿拉曼"
TITLE = "为什么阿拉曼只打了12天"


def _cfg():
    return {
        "video": {"sections_min": 12, "sections_max": 16,
                  "chars_per_section": [C_LO, C_HI]},
        "copywriter": {"model": "glm-4-flash", "temperature": 0.7},
    }


def _run(tmp_path, monkeypatch, sections, replies):
    """replies: 要点 → 按调用顺序吐出的旁白。队列耗尽再打就是多付了一次。"""
    outline = {
        "title": TITLE,
        "tags": ["军事", "二战", "阿拉曼", "沙漠", "坦克", "北非"],
        "sections": sections,
    }
    prompts = []

    def fake_chat(api_key, prompt, model, temperature):
        prompts.append(prompt)
        if "你是B站军事历史区编导" in prompt:
            return json.dumps(outline, ensure_ascii=False)
        if prompt.startswith("原标题"):
            raise AssertionError("总字数不够不应重写标题")
        for point, queue in replies.items():
            if point in prompt:
                if not queue:
                    raise AssertionError(f"扩写次数超出预期: {point}")
                return queue.pop(0)
        raise AssertionError("未识别的提示词: " + prompt[:40])

    monkeypatch.setattr(script_mod, "_chat", fake_chat)
    job = Job.create(tmp_path, TOPIC)
    doc = autopost.stage_script(job, TOPIC, _cfg(), logging.getLogger("short-retry"))
    saved = json.loads(Path(job.path("script.json")).read_text(encoding="utf-8"))
    outline_calls = sum("你是B站军事历史区编导" in p for p in prompts)
    assert job.stage("script")["status"] == "done"
    assert all(not q for q in replies.values())
    return doc, saved, outline_calls


def test_short_total_reexpands_only_short_sections(tmp_path, monkeypatch):
    """偏短的段再扩写一次；窗口内和超上限的段留下；大纲只打一次。"""
    kept = "甲" * 80                 # 80，落在窗口内
    short_first = "乙" * 40          # 40，低于 65，首轮两次都不达标
    short_retry = "乙补" * 20        # 补写一轮的结果，仍然只有 40 字
    too_long = "丙" * 200            # 200，超上限，但不是偏短
    quote = {"text": "我们将战斗到底", "author": "丘吉尔", "context": "演说"}
    stickers = [{"emoji": "🔥", "at": 0.5, "why": "转折"}]
    sections = [
        {"point": "要点甲-沙漠防线", "image_query": "El Alamein 1942",
         "stickers": [], "quote": None},
        {"point": "要点乙-坦克对决", "image_query": "El Alamein tanks",
         "stickers": stickers, "quote": quote},
        {"point": "要点丙-补给线", "image_query": "El Alamein supply",
         "stickers": [], "quote": None},
    ]
    # 首轮 _expand 窗口外会再打一次；补写轮对偏短段只再打一次。
    replies = {
        "要点甲-沙漠防线": [kept],
        "要点乙-坦克对决": [short_first, short_first, short_retry],
        "要点丙-补给线": [too_long, too_long],
    }
    doc, saved, outline_calls = _run(tmp_path, monkeypatch, sections, replies)

    assert outline_calls == 1
    assert [s["text"] for s in saved["sections"]] == [kept, short_retry, too_long]
    assert saved["title"] == TITLE
    assert saved["sections"][0]["image_query"] == "El Alamein 1942"
    assert saved["sections"][1]["quote"] == quote
    assert saved["sections"][1]["stickers"] == stickers
    assert saved["sections"][2]["image_query"] == "El Alamein supply"
    assert all("point" not in s for s in saved["sections"])
    total = sum(len(s["text"]) for s in saved["sections"])
    assert total < 12 * C_LO          # 补写后仍短，稿也落盘
    assert doc["sections"][1]["text"] == short_retry


def test_in_window_sections_not_rewritten_when_total_short(tmp_path, monkeypatch):
    """每段都在窗口下沿、总字数仍不够：不重出大纲，也不再扩写。"""
    texts = ["丁" * 65, "戊" * 65, "己" * 70]
    points = ["要点丁-下沿", "要点戊-下沿", "要点己-窗口内"]
    sections = [
        {"point": p, "image_query": f"query {i}", "stickers": [], "quote": None}
        for i, p in enumerate(points)
    ]
    replies = {p: [t] for p, t in zip(points, texts)}
    doc, saved, outline_calls = _run(tmp_path, monkeypatch, sections, replies)

    assert outline_calls == 1
    assert [s["text"] for s in saved["sections"]] == texts
    assert sum(len(s["text"]) for s in doc["sections"]) < 12 * C_LO
