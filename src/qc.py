"""成片自动质检（方案 P1-6）：发布前的最后一道确定性闸门。

检查项：文件可读、分辨率/帧率符合配置、音视频时长一致、
无意外静音/削波、无长黑帧、字幕时间轴合法、封面可打开。
全部用 ffmpeg/ffmpeg -vf 探测，不引入新依赖。阈值可在 config.yaml qc 段覆盖。
"""
import re
import subprocess
from pathlib import Path

import imageio_ffmpeg

DEFAULTS = {
    "max_black_seconds": 2.0,     # 累计黑帧上限（片头黑场转场会有一点）
    "max_volume_db": -0.5,        # 高于此值视为削波
    "min_mean_volume_db": -45.0,  # 低于此值视为整轨静音
    "av_duration_slack": 2.0,     # 音视频时长允许偏差（秒）
}


def _probe(path: str) -> dict:
    """ffmpeg -i 解析 Duration / 视频 / 音频流信息。读不了抛 RuntimeError。"""
    r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-i", path],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60)
    err = r.stderr
    if "Duration: N/A" in err or ("Duration" not in err and "Video" not in err):
        raise RuntimeError(f"ffmpeg 无法读取: {path}")
    dur = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if not dur:
        raise RuntimeError(f"无时长信息: {path}")
    h, m, s = dur.groups()
    info = {"duration": int(h) * 3600 + int(m) * 60 + float(s),
            "width": 0, "height": 0, "fps": 0.0, "has_audio": False}
    vm = re.search(r"Video:.*?,\s*(\d{3,5})x(\d{3,5})", err)
    if vm:
        info["width"], info["height"] = int(vm.group(1)), int(vm.group(2))
        fm = re.search(r"(\d+(?:\.\d+)?)\s*fps", err)
        info["fps"] = float(fm.group(1)) if fm else 0.0
    info["has_audio"] = "Audio:" in err
    return info


def _audio_levels(path: str) -> dict:
    """volumedetect 一遍解码取平均/峰值音量（dB）。"""
    r = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-i", path, "-vn", "-af", "volumedetect",
         "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600)
    mean = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", r.stderr)
    peak = re.search(r"max_volume:\s*(-?[\d.]+)\s*dB", r.stderr)
    return {"mean": float(mean.group(1)) if mean else 0.0,
            "peak": float(peak.group(1)) if peak else 0.0}


def _black_seconds(path: str) -> float:
    """blackdetect 统计累计黑帧时长（快速解码，不写文件）。"""
    r = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-i", path,
         "-vf", "blackdetect=d=0.5:pix_th=0.10", "-an", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=1200)
    total = 0.0
    for m in re.finditer(r"black_start:\S+\s+black_end:\S+\s+black_duration:([\d.]+)",
                         r.stderr):
        total += float(m.group(1))
    return total


def _parse_srt(path: str) -> list[tuple[float, float]]:
    """返回 [(start,end)] 秒；格式非法的行直接抛 ValueError。"""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    out, last_end = [], -1.0
    ts = re.compile(r"(\d+):(\d+):(\d+)[,.](\d{3})\s*-->\s*(\d+):(\d+):(\d+)[,.](\d{3})")
    for block in re.split(r"\n\s*\n", text.strip()):
        m = ts.search(block)
        if not m:
            if block.strip() and block.strip() != "0":
                raise ValueError(f"字幕块无法解析: {block[:60]!r}")
            continue
        g = [int(x) for x in m.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        if end <= start:
            raise ValueError(f"字幕时间倒退: {start} -> {end}")
        if start < last_end - 0.05:
            raise ValueError(f"字幕重叠/乱序 @ {start}")
        last_end = end
        out.append((start, end))
    return out


def qc_video(final_path: str, srt_path: str, cover_path: str,
             cfg_video: dict, thresholds: dict | None = None,
             narration_total: float | None = None) -> tuple[bool, list[str]]:
    """全部通过返回 (True, [])；否则 (False, 问题列表)。"""
    th = {**DEFAULTS, **(thresholds or {})}
    problems: list[str] = []

    # 1 文件存在、非空、可读
    f = Path(final_path)
    if not f.exists() or f.stat().st_size == 0:
        return False, [f"成片缺失或为空: {final_path}"]
    try:
        info = _probe(final_path)
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        return False, [f"成片不可读取: {e}"]

    # 2 分辨率/帧率
    w, h, fps = cfg_video.get("width", 1920), cfg_video.get("height", 1080), \
        cfg_video.get("fps", 30)
    if (info["width"], info["height"]) != (w, h):
        problems.append(f"分辨率 {info['width']}x{info['height']} != 配置 {w}x{h}")
    if info["fps"] and abs(info["fps"] - fps) > 2:
        problems.append(f"帧率 {info['fps']} != 配置 {fps}")

    # 3 音视频时长
    if not info["has_audio"]:
        problems.append("成片没有音频轨")
    elif narration_total and not (narration_total * 0.8 <= info["duration"]
                                  <= narration_total * 1.6 + 15):
        problems.append(
            f"成片时长 {info['duration']:.0f}s 与旁白总长 "
            f"{narration_total:.0f}s 明显错位")

    # 4 音频静音/削波
    try:
        lv = _audio_levels(final_path)
        if lv["peak"] >= th["max_volume_db"]:
            problems.append(f"音频削波风险: max {lv['peak']}dB ≥ {th['max_volume_db']}dB")
        if lv["mean"] <= th["min_mean_volume_db"]:
            problems.append(f"音频近乎静音: mean {lv['mean']}dB ≤ {th['min_mean_volume_db']}dB")
    except subprocess.TimeoutExpired:
        problems.append("音量探测超时（人工复查）")

    # 5 黑帧
    try:
        black = _black_seconds(final_path)
        if black > th["max_black_seconds"]:
            problems.append(f"黑帧过多: {black:.1f}s > {th['max_black_seconds']}s")
    except subprocess.TimeoutExpired:
        problems.append("黑帧探测超时（人工复查）")

    # 6 字幕
    if not Path(srt_path).exists():
        problems.append(f"字幕文件缺失: {srt_path}")
    else:
        try:
            cues = _parse_srt(srt_path)
            if not cues:
                problems.append("字幕为空")
            elif cues[-1][1] > info["duration"] + 5:
                problems.append(f"字幕超出视频时长: {cues[-1][1]:.0f}s > "
                                f"{info['duration']:.0f}s")
        except ValueError as e:
            problems.append(f"字幕时间轴非法: {e}")

    # 7 封面
    cf = Path(cover_path)
    if not cf.exists() or cf.stat().st_size < 10240:
        problems.append(f"封面缺失或过小: {cover_path}")
    else:
        try:
            _probe(cover_path)
        except (RuntimeError, subprocess.TimeoutExpired):
            problems.append(f"封面无法解码: {cover_path}")

    return (not problems), problems
