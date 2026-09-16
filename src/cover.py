"""从视频抽帧生成B站封面（jpg, 16:9）。依赖 imageio-ffmpeg 自带的 ffmpeg，无需系统安装。"""
import subprocess, os
import imageio_ffmpeg

def extract_cover(video_path: str, cover_path: str, at_second: int = 3) -> str:
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    os.makedirs(os.path.dirname(os.path.abspath(cover_path)), exist_ok=True)
    cmd = [
        ffmpeg, "-y",
        "-ss", str(at_second), "-i", video_path,
        "-frames:v", "1",
        "-vf", "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720",
        "-q:v", "2",
        cover_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    return cover_path
