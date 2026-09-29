"""配音模块：可插拔双后端 —— edge-tts（免费）| 智谱 cogtts（大模型，更自然）。

zhipu 后端失败（余额不足/网络）时自动降级到 edge，保证产线不断。
"""
import asyncio, os, subprocess
from pathlib import Path
import edge_tts
import requests
import imageio_ffmpeg

ZHIPU_SPEECH_URL = "https://open.bigmodel.cn/api/paas/v4/audio/speech"


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
                  backend: str = "edge", zhipu: dict | None = None) -> str:
    """按后端合成；zhipu 失败自动降级 edge。"""
    if backend == "zhipu" and zhipu and zhipu.get("api_key"):
        try:
            return _synth_zhipu(text, out_path, zhipu["api_key"],
                                zhipu.get("model", "cogtts"),
                                zhipu.get("voice", "tongtong"))
        except Exception as e:
            print(f"[narrator] zhipu TTS 失败，降级 edge: {e}")
    return _synth_edge(text, out_path, voice, rate)


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
            backend: str = "edge", zhipu: dict | None = None) -> list:
    """逐段配音；返回 [{path, duration, text}]。"""
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    results = []
    for i, sec in enumerate(sections):
        mp3 = f"{out_dir}/sec_{i:02d}.mp3"
        synth_section(sec["text"], mp3, voice, rate, backend=backend, zhipu=zhipu)
        results.append({"path": mp3, "duration": audio_duration(mp3),
                        "text": sec["text"]})
    return results
