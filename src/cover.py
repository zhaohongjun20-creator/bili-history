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


def make_cover(background_img: str, title: str, out_path: str,
               subtitle: str = "军事历史纪录片 · AI 辅助创作") -> str:
    """设计封面：本集史料图做暗化暗角背景，主标题大字 + 栏目副题（1280x720）。

    替代"从成片第 3 秒抽帧"的随手截图——B 站信息流里一眼能读到标题。
    PIL 处理背景，文字用微软雅黑；任何一步失败由调用方降级 extract_cover。
    """
    from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageEnhance

    W, H = 1280, 720
    FONT = "C:/Windows/Fonts/msyh.ttc"

    # 背景：史料图裁 16:9 → 高斯轻模糊 → 压暗
    img = Image.open(background_img).convert("RGB")
    ratio = max(W / img.width, H / img.height)
    img = img.resize((int(img.width * ratio) + 1, int(img.height * ratio) + 1))
    left = (img.width - W) // 2
    top = (img.height - H) // 2
    img = img.crop((left, top, left + W, top + H))
    img = img.filter(ImageFilter.GaussianBlur(3))
    img = ImageEnhance.Brightness(img).enhance(0.62)

    # 四边暗角（径向渐变遮罩）
    vignette = Image.new("L", (W, H), 0)
    vd = ImageDraw.Draw(vignette)
    for i in range(160):
        alpha = int(150 * (1 - i / 160))
        vd.rectangle([i * 2, i * 2, W - i * 2, H - i * 2], outline=alpha)
    vignette = vignette.filter(ImageFilter.GaussianBlur(60))
    black = Image.new("RGB", (W, H), (0, 0, 0))
    img = Image.composite(img, black, vignette.point(lambda a: 255 - a // 2))

    d = ImageDraw.Draw(img)
    title_font = ImageFont.truetype(FONT, 64)
    sub_font = ImageFont.truetype(FONT, 30)

    # 标题自动换行（最多 3 行，每行约 16 字）
    lines, buf = [], ""
    for ch in title:
        buf += ch
        if len(buf) >= 16:
            lines.append(buf)
            buf = ""
    if buf:
        lines.append(buf)
    lines = lines[:3]
    y = H - 110 - 74 * len(lines)
    for ln in lines:
        d.text((60, y), ln, font=title_font, fill=(255, 255, 255),
               stroke_width=3, stroke_fill=(0, 0, 0))
        y += 74
    # 副题 + 品牌色短线
    d.rectangle([60, y - 8, 220, y - 2], fill=(196, 60, 50))
    d.text((60, y + 8), subtitle, font=sub_font, fill=(235, 232, 226))

    img.save(out_path, "JPEG", quality=90)
    return out_path
