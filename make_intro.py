"""一次性脚本：项目介绍视频（曼波风配音 + 标题卡 + GitHub 截图 + Ken Burns）。

曼波音色实现：edge-tts 活泼音色 → ffmpeg 升调 32%（asetrate 技巧）→ 魔性化。
"""
import os, random, subprocess, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import imageio_ffmpeg
import edge_tts

sys.path.insert(0, str(Path(__file__).parent))
from src.slideshow import build_section_clip, compose_final
from src.narrator import synth_section, audio_duration

W, H = 1920, 1080
FONT = "C:/Windows/Fonts/msyh.ttc"
BILI_PINK = (251, 114, 154)
GH_GREEN = (63, 185, 80)

SECTIONS = [
    # (讲稿文字, 画面)
    ("大家好，我是赛博翻车鱼。今天带你参观全自动视频流水线，曼波。", "card_title"),
    ("风景号：每天下午一点，自动去 Pexels 捞运镜素材，AI 写文案，剪成三十秒，发B站。", "img:intro_gh_sau.png"),
    ("军史号更狠：AI 编导写讲稿，微软云健配音，维基百科的二战老照片自动配图。", "img:intro_gh_history.png"),
    ("三分钟一集纪录片，从选题到投稿，全程零人工。人类只需要躺着，曼波。", "card_stats"),
    ("光流算法筛掉死镜头，两步法控制讲稿字数，限流了还会自己退避重试。", "img:intro_gh_profile.png"),
    ("全部代码已经开源，三个仓库链接放在简介里，去给我点个 star，曼波。", "card_github"),
    ("我是赛博翻车鱼，我们下期再见。曼波～曼波～", "card_end"),
]


def make_card(kind: str, path: str):
    img = Image.new("RGB", (W, H), (18, 21, 28))
    d = ImageDraw.Draw(img)
    big = ImageFont.truetype(FONT, 96)
    mid = ImageFont.truetype(FONT, 54)
    small = ImageFont.truetype(FONT, 40)

    def center(text, y, font, color=(240, 240, 245)):
        bb = d.textbbox((0, 0), text, font=font)
        d.text(((W - bb[2] + bb[0]) / 2, y), text, font=font, fill=color)

    if kind == "card_title":
        center("全 自 动 视 频 流 水 线", 330, big, BILI_PINK)
        center("一条命令，从选题到投稿", 500, mid)
        center("开源 · 免费 · 无人值守", 620, small, (150, 160, 175))
    elif kind == "card_stats":
        center("3 个开源仓库", 260, big, GH_GREEN)
        center("风景号 · 军史号 · 投稿内核", 430, mid)
        center("10+ 已发布视频 · 零人工干预", 560, mid)
        center("GLM 写稿 / TTS 配音 / 光流筛片 / biliup 投稿", 690, small, (150, 160, 175))
    elif kind == "card_github":
        center("github.com/zhaohongjun20-creator", 380, mid, BILI_PINK)
        center("bilibili-autopost · bili-autopost-sau · bili-history", 520, small)
        center("MIT 开源 · 欢迎白嫖", 640, small, (150, 160, 175))
    elif kind == "card_end":
        center("曼 波 ～", 400, big, BILI_PINK)
        center("点赞 · star · 下期见", 580, mid)
    img.save(path)


def mamboize(mp3: str, out: str, pitch: float = 1.32) -> str:
    """升调魔性化：asetrate 提高采样率=升调，aresample 还原，atempo 校正语速。"""
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run([
        ffmpeg, "-y", "-i", mp3,
        "-af", f"asetrate=24000*{pitch},aresample=24000,atempo=1/{pitch}",
        "-c:a", "libmp3lame", "-q:a", "4", out,
    ], check=True, capture_output=True, timeout=120)
    return out


def main():
    aud_dir, clip_dir = "audio_intro", "downloads"
    Path(aud_dir).mkdir(exist_ok=True)
    narrations, section_files = [], []

    for i, (text, visual) in enumerate(SECTIONS):
        raw = f"{aud_dir}/raw_{i:02d}.mp3"
        mp3 = f"{aud_dir}/sec_{i:02d}.mp3"
        for attempt in range(3):   # edge-tts 偶发 NoAudioReceived，退避重试
            try:
                synth_section(text, raw, "zh-CN-YunxiNeural", "+8%")
                if os.path.getsize(raw) > 2000:
                    break
            except Exception as e:
                print(f"段{i} TTS 第{attempt}次失败: {type(e).__name__}，重试")
                import time; time.sleep(5)
        mamboize(raw, mp3, pitch=1.32 if i % 2 else 1.26)   # 奇偶交替音调更魔性
        dur = audio_duration(mp3)
        narrations.append({"path": mp3, "duration": dur, "text": text})
        print(f"段{i}: {dur:.1f}s {text[:18]}")

        if visual.startswith("img:"):
            img = f"imgs/{visual[4:]}"
        else:
            img = f"imgs/{visual}.png"
            make_card(visual, img)
        clip = f"{clip_dir}/intro_sec_{i:02d}.mp4"
        build_section_clip(img, mp3, dur, clip, mode="in" if i % 2 == 0 else "out")
        section_files.append(clip)

    total = sum(n["duration"] for n in narrations)
    print(f"总时长 {total:.1f}s，合成中…")
    from src.slideshow import make_srt
    srt = make_srt(narrations, f"{clip_dir}/intro.srt")
    bgm = "bgm/Carefree.mp3" if Path("bgm/Carefree.mp3").exists() else str(random.choice(list(Path("bgm").glob("*.mp3"))))
    compose_final(section_files, bgm, srt, f"{clip_dir}/intro.mp4", bgm_volume=0.18)
    print(f"完成: {clip_dir}/intro.mp4 ({os.path.getsize(f'{clip_dir}/intro.mp4')/1e6:.1f} MB)")


if __name__ == "__main__":
    main()
