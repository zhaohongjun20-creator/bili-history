"""配音模块：可插拔双后端 —— edge-tts（免费）| 智谱 cogtts（大模型，更自然）。

zhipu 后端失败（余额不足/网络）时自动降级到 edge，保证产线不断。
"""
import asyncio, json, os, subprocess
from pathlib import Path
import edge_tts
import requests
import imageio_ffmpeg

ZHIPU_SPEECH_URL = "https://open.bigmodel.cn/api/paas/v4/audio/speech"

# 音色熔断池：指定音色合成失败（如云泽在免费端点不可用）时按序降级，保产线不断
EDGE_FALLBACK_VOICES = ["zh-CN-YunjianNeural", "zh-CN-YunyangNeural", "zh-CN-YunxiNeural"]

# 本地 SAPI 兜底语音（微软封堵 edge-tts 免费端点时启用；离线可用，音质略机械）
SAPI_VOICE = "Microsoft Huihui Desktop"

# Piper 本地神经音色（免费离线、质量介于云端神经与 SAPI 之间；模型在 ../tts_models）
PIPER_MODEL = str(Path(__file__).resolve().parent.parent.parent / "tts_models"
                  / "zh_CN-huayan-medium.onnx")
_piper_voice = None

# CosyVoice2 本地大模型（Apache-2.0，质量第一梯队；环境在 ../cosyvoice）
_COSY_ROOT = Path(__file__).resolve().parent.parent.parent / "cosyvoice"
_COSY_PY = _COSY_ROOT / ".venv" / "Scripts" / "python.exe"
_COSY_CLI = _COSY_ROOT / "synth_cli.py"


def _synth_cosy_batch(sections: list, out_dir: str, cosy: dict) -> list[str]:
    """CosyVoice2 批量合成（子进程一次加载模型，全部段落出片后返回 wav 路径）。

    任一段失败即抛错，由调用方降级到旧链路。两种配方（cosy 配置里选）：
    - mode=zero_shot + spk:      预注册音色 ID（register_voice.py 注册）
    - mode=instruct2 + prompt_wav + instruct: 参考音频 + 风格指令（实测音质最优，
      男声低沉纪录片腔 = repo/asset/cross_lingual_prompt.wav + 低沉严肃指令）
    """
    if not (_COSY_PY.exists() and _COSY_CLI.exists()):
        raise RuntimeError(f"cosyvoice env missing: {_COSY_PY}")
    from .cnnum import normalize as _cn  # 数字→汉字：旁路 cosy TN（"1944年"会被读成"十九四四年"）
    if cosy.get("mode", "zero_shot") == "instruct2":
        base = {"mode": "instruct2", "prompt_wav": str(_COSY_ROOT / cosy["prompt_wav"]),
                "instruct": cosy["instruct"]}
    else:
        base = {"spk_id": cosy["spk"]}
    jobs = [{**base, "text": _cn(s["text"]),
             "out": str((Path(out_dir) / f"sec_{i:02d}.wav").resolve())}
            for i, s in enumerate(sections)]  # 必须绝对路径：子进程 cwd 在 cosyvoice 目录
    manifest = _COSY_ROOT / "_jobs.json"
    manifest.write_text(json.dumps({"jobs": jobs}, ensure_ascii=False),
                        encoding="utf-8")
    r = subprocess.run([str(_COSY_PY), str(_COSY_CLI), str(manifest)],
                       capture_output=True, text=True, timeout=3600,
                       cwd=str(_COSY_ROOT))
    print(f"[narrator] cosy batch:\n{r.stdout.strip()}")
    outs = [j["out"] for j in jobs]
    missing = [o for o in outs if not Path(o).exists()]
    if missing or r.returncode != 0:
        raise RuntimeError(f"cosy batch incomplete: missing={missing} "
                           f"err={r.stderr.strip()[:200]}")
    return outs


def _synth_piper(text: str, out_path: str) -> str:
    """Piper 本地神经网络合成（wav）。模型进程内缓存，多次调用零加载开销。"""
    global _piper_voice
    import wave as _wave
    if _piper_voice is None:
        from piper import PiperVoice
        _piper_voice = PiperVoice.load(PIPER_MODEL)
    wav = str(Path(out_path).with_suffix(".wav"))
    with _wave.open(wav, "wb") as w:
        _piper_voice.synthesize_wav(text, w)
    if not Path(wav).exists() or Path(wav).stat().st_size < 5000:
        raise RuntimeError("piper produced empty audio")
    return wav


def _synth_sapi(text: str, out_path: str) -> str:
    """Windows 本地语音合成（System.Speech，wav 格式），edge 全线失败时的终极兜底。"""
    wav = str(Path(out_path).with_suffix(".wav"))
    ps = ("Add-Type -AssemblyName System.Speech;"
          f"$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
          f"$s.SelectVoice('{SAPI_VOICE}'); $s.Rate = 0;"
          f"$s.SetOutputToWaveFile('{wav}');"
          "$s.Speak([Console]::In.ReadToEnd()); $s.Dispose()")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       input=text, text=True, capture_output=True, timeout=300)
    if not Path(wav).exists() or Path(wav).stat().st_size < 5000:
        raise RuntimeError(f"SAPI failed: {r.stderr[:150]}")
    return wav


def _synth_edge(text: str, out_path: str, voice: str, rate: str) -> str:
    async def run():
        await edge_tts.Communicate(text, voice, rate=rate).save(out_path)
    asyncio.run(run())
    return out_path


def _synth_zhipu(text: str, out_path: str, api_key: str, model: str, voice: str) -> str:
    r = requests.post(
        ZHIPU_SPEECH_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": model, "input": text, "voice": voice},
        timeout=120,
    )
    if r.status_code != 200 or len(r.content) < 2000:
        raise RuntimeError(f"zhipu tts failed: {r.status_code} {r.text[:120]}")
    Path(out_path).write_bytes(r.content)
    return out_path


def synth_section(text: str, out_path: str, voice: str, rate: str,
                  backend: str = "edge", zhipu: dict | None = None,
                  allow_sapi: bool = False) -> str:
    """按后端合成；zhipu 失败降级 edge；edge 指定音色失败自动换熔断池音色。

    质量闸门：神经音色全灭时默认直接抛错（今日不发，等恢复），
    绝不放 SAPI 机械音上线——"产线可以断，烂片不能发"。
    allow_sapi=True 时才允许本地 SAPI 兜底（仅供手动救急）。
    """
    if backend == "zhipu" and zhipu and zhipu.get("api_key"):
        try:
            return _synth_zhipu(text, out_path, zhipu["api_key"],
                                zhipu.get("model", "cogtts"),
                                zhipu.get("voice", "tongtong"))
        except Exception as e:
            print(f"[narrator] zhipu TTS 失败，降级 edge: {e}")
    try:
        return _synth_edge(text, out_path, voice, rate)
    except Exception as e:
        for fb in EDGE_FALLBACK_VOICES:
            if fb == voice:
                continue
            try:
                _synth_edge(text, out_path, fb, rate)
                print(f"[narrator] 音色 {voice} 失败({type(e).__name__})，已熔断降级 → {fb}")
                return out_path
            except Exception:
                continue
        # edge 全线失败 → Piper 本地神经音色（免费离线，质量可上线）
        try:
            wav = _synth_piper(text, out_path)
            print(f"[narrator] edge-tts 不可用，Piper 本地神经音色接管 → {wav}")
            return wav
        except Exception as ep:
            if allow_sapi:
                try:
                    wav = _synth_sapi(text, out_path)
                    print(f"[narrator] 质量闸门放行(手动模式)：本地 SAPI 兜底 → {wav}")
                    return wav
                except Exception as e2:
                    raise RuntimeError(f"edge/piper/SAPI 均失败: {e2}")
            raise RuntimeError(
                f"edge 与 Piper 均不可用（{type(ep).__name__}）。质量闸门：今日不发布。")


def audio_duration(path: str) -> float:
    r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-i", path],
                       capture_output=True, text=True, timeout=60)
    m = next((l for l in r.stderr.splitlines() if "Duration" in l), "")
    try:
        hh, mm, ss = m.split("Duration:")[1].split(",")[0].strip().split(":")
        return int(hh) * 3600 + int(mm) * 60 + float(ss)
    except (ValueError, IndexError):
        return 0.0


def narrate(sections: list, out_dir: str, voice: str, rate: str,
            backend: str = "edge", zhipu: dict | None = None,
            allow_sapi: bool = False, cosy: dict | None = None) -> list:
    """逐段配音；返回 [{path, duration, text}]。

    cosy 配置存在且 enable 时优先走 CosyVoice2 本地大模型批量合成（质量最优），
    失败自动降级 edge→熔断池→Piper 旧链路，产线不断。
    """
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    if cosy and cosy.get("enable"):
        try:
            paths = _synth_cosy_batch(sections, out_dir, cosy)
            print(f"[narrator] CosyVoice2 大模型音色接管（{len(paths)}段）")
            return [{"path": p, "duration": audio_duration(p), "text": s["text"]}
                    for p, s in zip(paths, sections)]
        except Exception as e:
            print(f"[narrator] CosyVoice2 失败，降级旧链路: {e}")
    results = []
    for i, sec in enumerate(sections):
        mp3 = f"{out_dir}/sec_{i:02d}.mp3"
        actual = synth_section(sec["text"], mp3, voice, rate, backend=backend,
                               zhipu=zhipu, allow_sapi=allow_sapi)
        results.append({"path": actual, "duration": audio_duration(actual),
                        "text": sec["text"]})
    return results
