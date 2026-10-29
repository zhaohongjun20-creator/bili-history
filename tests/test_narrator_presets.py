"""配音预设解析测试：命名预设 / legacy 回退 / 内置缺省 / 校验。"""
import pytest

from src.narrator import DEFAULT_COSY_PRESET, resolve_cosy_preset


def test_named_preset_resolved():
    cosy = {"enable": True, "preset": "b",
            "presets": {"a": {"mode": "instruct2", "prompt_wav": "x.wav",
                              "instruct": "指令A"},
                        "b": {"mode": "instruct2", "prompt_wav": "y.wav",
                              "instruct": "指令B"}}}
    r = resolve_cosy_preset(cosy)
    assert r == {"mode": "instruct2", "prompt_wav": "y.wav", "instruct": "指令B"}


def test_legacy_fields_fallback():
    cosy = {"mode": "instruct2", "prompt_wav": "legacy.wav",
            "instruct": "legacy 指令"}
    r = resolve_cosy_preset(cosy)
    assert r["prompt_wav"] == "legacy.wav" and r["instruct"] == "legacy 指令"


def test_preset_missing_falls_back_to_legacy():
    cosy = {"preset": "不存在", "mode": "instruct2", "prompt_wav": "l.wav",
            "instruct": "L", "presets": {"a": {"mode": "instruct2",
                                               "prompt_wav": "x.wav"}}}
    r = resolve_cosy_preset(cosy)
    assert r["prompt_wav"] == "l.wav"


def test_builtin_default_when_nothing_set():
    r = resolve_cosy_preset({})
    assert r == DEFAULT_COSY_PRESET


def test_zero_shot_spk_normalized():
    cosy = {"preset": "s", "presets": {"s": {"mode": "zero_shot",
                                             "spk_id": "doc_male"}}}
    assert resolve_cosy_preset(cosy) == {"mode": "zero_shot", "spk_id": "doc_male"}


def test_instruct2_without_prompt_wav_inherits_default():
    """部分预设只写差异项：prompt_wav 缺省继承内置参考音频（配方=差异项）。"""
    cosy = {"preset": "x",
            "presets": {"x": {"mode": "instruct2", "instruct": "只有指令"}}}
    r = resolve_cosy_preset(cosy)
    assert r["prompt_wav"] == DEFAULT_COSY_PRESET["prompt_wav"]
    assert r["instruct"] == "只有指令"


def test_instruct_missing_uses_default_instruction():
    cosy = {"preset": "x",
            "presets": {"x": {"mode": "instruct2", "prompt_wav": "p.wav"}}}
    assert resolve_cosy_preset(cosy)["instruct"] == DEFAULT_COSY_PRESET["instruct"]
