"""edge-tts 纪录片旁白：逐段合成 mp3，返回各段音频路径与时长。"""
import asyncio, json, subprocess
from pathlib import Path
import edge_tts
import imageio_ffmpeg


def synth_section(text: str, out_mp3: str, voice: str, rate: str) -> str:
    async def run():
        await edge_tts.Communicate(text, voice, rate=rate).save(out_mp3)
    asyncio.run(run())
    return out_mp3


def audio_duration(path: str) -> float:
    r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-i", path],
                       capture_output=True, text=True, timeout=60)
    m = next((l for l in r.stderr.splitlines() if "Duration" in l), "")
    try:
        hh, mm, ss = m.split("Duration:")[1].split(",")[0].strip().split(":")
        return int(hh) * 3600 + int(mm) * 60 + float(ss)
    except (ValueError, IndexError):
        return 0.0


def narrate(sections: list, out_dir: str, voice: str, rate: str) -> list:
    """逐段配音；返回 [{path, duration, text}]。"""
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    results = []
    for i, sec in enumerate(sections):
        mp3 = f"{out_dir}/sec_{i:02d}.mp3"
        synth_section(sec["text"], mp3, voice, rate)
        results.append({"path": mp3, "duration": audio_duration(mp3),
                        "text": sec["text"]})
    return results
