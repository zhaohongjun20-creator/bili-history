"""质检模块测试：SRT 时间轴校验 + 真实小视频的规格/音量/黑帧检查。"""
import subprocess
from pathlib import Path

import imageio_ffmpeg
import pytest

from src import qc

FF = imageio_ffmpeg.get_ffmpeg_exe()


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    """模块级生成一次真实素材：正常片 / 全黑片 / 静音片 / 损坏片 / 封面。"""
    d = tmp_path_factory.mktemp("media")

    def make(name, vf, af):
        out = d / name
        subprocess.run(
            [FF, "-y", "-f", "lavfi", "-i", f"color=c=blue:s=320x240:r=25:d=2",
             "-f", "lavfi", "-i", af, "-vf", vf,
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
             str(out)], check=True, capture_output=True, timeout=120)
        return str(out)

    good = make("good.mp4", "null", "sine=frequency=440:duration=2")
    black = make("black.mp4", "null", "sine=frequency=440:duration=2")  # 全黑由下面造
    subprocess.run([FF, "-y", "-f", "lavfi", "-i", "color=c=black:s=320x240:r=25:d=2",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(d / "black.mp4")],
                   check=True, capture_output=True, timeout=120)
    silent = make("silent.mp4", "null", "anullsrc=r=44100:cl=mono:d=2")
    cover = d / "cover.jpg"
    subprocess.run([FF, "-y", "-f", "lavfi", "-i", "testsrc=s=1280x720:d=1",
                    "-frames:v", "1", str(cover)],
                   check=True, capture_output=True, timeout=60)
    broken = d / "broken.mp4"
    broken.write_bytes(b"\x00\x01garbage-not-video")
    return {"dir": d, "good": good, "black": str(d / "black.mp4"),
            "silent": silent, "cover": str(cover), "broken": str(broken)}


def _srt(path, body="1\n00:00:00,000 --> 00:00:01,500\n旁白内容\n"):
    Path(path).write_text(body, encoding="utf-8")
    return str(path)


CFG = {"width": 320, "height": 240, "fps": 25}


def test_good_video_passes(media, tmp_path):
    ok, problems = qc.qc_video(media["good"], _srt(tmp_path / "a.srt"),
                               media["cover"], CFG, narration_total=2.0)
    assert ok, problems


def test_broken_video_detected(media, tmp_path):
    ok, problems = qc.qc_video(media["broken"], _srt(tmp_path / "a.srt"),
                               media["cover"], CFG)
    assert not ok and any("不可读取" in p for p in problems)


def test_silence_detected(media, tmp_path):
    ok, problems = qc.qc_video(media["silent"], _srt(tmp_path / "a.srt"),
                               media["cover"], CFG)
    assert not ok and any("静音" in p for p in problems)


def test_black_frames_detected(media, tmp_path):
    ok, problems = qc.qc_video(media["black"], _srt(tmp_path / "a.srt"),
                               media["cover"], CFG,
                               thresholds={"max_black_seconds": 0.5})
    assert not ok and any("黑帧" in p for p in problems)


def test_resolution_mismatch_detected(media, tmp_path):
    ok, problems = qc.qc_video(media["good"], _srt(tmp_path / "a.srt"),
                               media["cover"], {"width": 1920, "height": 1080,
                                                "fps": 25})
    assert not ok and any("分辨率" in p for p in problems)


def test_missing_cover_detected(media, tmp_path):
    ok, problems = qc.qc_video(media["good"], _srt(tmp_path / "a.srt"),
                               str(tmp_path / "nope.jpg"), CFG)
    assert not ok and any("封面" in p for p in problems)


def test_srt_reversed_timestamps(media, tmp_path):
    srt = _srt(tmp_path / "r.srt",
               "1\n00:00:01,500 --> 00:00:01,000\n倒退\n")
    ok, problems = qc.qc_video(media["good"], srt, media["cover"], CFG)
    assert not ok and any("倒退" in p for p in problems)


def test_srt_overrun_detected(media, tmp_path):
    srt = _srt(tmp_path / "o.srt",
               "1\n00:00:00,000 --> 00:01:59,000\n超长\n")
    ok, problems = qc.qc_video(media["good"], srt, media["cover"], CFG)
    assert not ok and any("超出" in p for p in problems)


def test_srt_monotonic_ok(tmp_path):
    p = tmp_path / "m.srt"
    p.write_text("1\n00:00:00,000 --> 00:00:01,000\nA\n\n"
                 "2\n00:00:01,000 --> 00:00:02,000\nB\n", encoding="utf-8")
    cues = qc._parse_srt(str(p))
    assert len(cues) == 2
