"""幻灯片成片：史料图 Ken Burns 动效 + 旁白逐段对齐 + BGM 铺底 + 字幕烧录。"""
import subprocess
from pathlib import Path
import imageio_ffmpeg

W, H, FPS = 1920, 1080, 30
BIG = 2400  # 先放大再 zoom，减轻整数抖动


def _kenburns(mode: str) -> str:
    """交替推/拉：zoom in 从1.0缓推，zoom out 从1.12缓拉。"""
    if mode == "in":
        return "min(1.0+0.0006*on,1.12)"
    return "max(1.12-0.0006*on,1.0)"


def build_section_clip(img: str, voice_mp3: str, duration: float, out_mp4: str,
                       mode: str = "in", stickers: list = None) -> str:
    """史料图 Ken Burns + 旁白 +（可选）Twemoji 表情贴纸 overlay。"""
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    vf = (f"scale={BIG}:{int(BIG*H/W)}:force_original_aspect_ratio=increase,"
          f"crop={BIG}:{int(BIG*H/W)},"
          f"zoompan=z='{_kenburns(mode)}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
          f":d=1:s={W}x{H}:fps={FPS},setsar=1")

    extra_inputs, chains, label = [], [], "[base]"
    positions = [(W - 265, H - 400), (W - 450, H - 255)]   # 右侧偏下，避开字幕
    for i, st in enumerate(stickers or []):
        idx = 2 + i  # 输入：0=图 1=旁白 贴纸从2起
        extra_inputs += ["-loop", "1", "-t", f"{duration:.3f}", "-i", st["path"]]
        x, y = positions[i % len(positions)]
        nxt = f"[s{i}]"
        chains.append(f"{label}[{idx}:v]overlay=x={x}:y={y}:"
                      f"enable='between(t,{st['at']:.2f},{st['at'] + 2.6:.2f})'{nxt}")
        label = nxt

    cmd = [ffmpeg, "-y", "-loop", "1", "-framerate", str(FPS),
           "-t", f"{duration:.3f}", "-i", img,
           "-i", voice_mp3, *extra_inputs]
    if stickers:
        fc = f"[0:v]{vf}[base];" + ";".join(chains)
        cmd += ["-filter_complex", fc, "-map", label, "-map", "1:a"]
    else:
        fc = f"[0:v]{vf}[v]"
        cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "1:a"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-shortest", out_mp4]
    subprocess.run(cmd, check=True, capture_output=True, timeout=600)
    return out_mp4


def _fmt_ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def make_srt(narrations: list, path: str) -> str:
    """每段旁白一条字幕，时间轴按段起点对齐。"""
    lines, t0 = [], 0.0
    for i, n in enumerate(narrations, 1):
        lines += [str(i), f"{_fmt_ts(t0)} --> {_fmt_ts(t0 + n['duration'])}",
                  n["text"].replace("\n", " "), ""]
        t0 += n["duration"]
    Path(path).write_text("\n".join(lines), encoding="utf-8")
    return path


def compose_final(section_files: list, bgm_path: str, srt_path: str,
                  out_path: str, bgm_volume: float = 0.22) -> str:
    """concat 各段 → 混 BGM → 烧字幕。"""
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    list_file = Path(out_path + ".list.txt")
    # concat demuxer 的相对路径基于 list 文件目录解析，故用绝对路径最稳
    list_file.write_text(
        "\n".join(f"file '{Path(f).resolve().as_posix()}'" for f in section_files),
        encoding="utf-8")
    style = ("FontName=Microsoft YaHei,FontSize=15,PrimaryColour=&H00FFFFFF,"
             "OutlineColour=&H96000000,BorderStyle=1,Outline=1,Shadow=0,MarginV=26,"
             "Alignment=2")
    # 字体走系统 directwrite（无需 fontsdir）；srt 用相对路径避免盘符冒号转义
    fc = (f"[1:a]volume={bgm_volume}[bg];"
          f"[0:a][bg]amix=inputs=2:duration=first:normalize=0[aout];"
          f"[0:v]subtitles={Path(srt_path).as_posix()}"
          f":force_style='{style}'[vout]")
    cmd = [
        ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-stream_loop", "-1", "-i", bgm_path,
        "-filter_complex", fc,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-shortest", out_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=1800)
    finally:
        list_file.unlink(missing_ok=True)
    return out_path
