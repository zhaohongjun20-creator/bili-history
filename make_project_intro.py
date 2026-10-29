"""一次性脚本：项目成长介绍视频（学生创作向）。

主题：介绍军事号 bili-history 与英文号 see-china 两个学生项目，以及它们
从 0.6.0 一路修到 0.7.5 的进步——不期待有多好，但期望逐渐进步。

复用产线设施：CosyVoice2 配音（低沉纪录片腔）+ Ken Burns + 转场 + BGM +
字幕烧录 + 质检。产物：data/project_intro/final.mp4
"""
import json
import subprocess
import sys
import time
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent))
from src.slideshow import build_section_clip, compose_final, make_srt
from src.narrator import _synth_cosy_batch, audio_duration
from src.qc import qc_video

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "project_intro"
W, H = 1920, 1080
FONT = "C:/Windows/Fonts/msyh.ttc"
INK = (242, 240, 235)          # 暖白
DIM = (150, 158, 170)          # 次级灰
ACCENT = (98, 160, 255)        # 学院蓝
WARM = (255, 176, 97)          # 暖橙
GREEN = (108, 196, 116)

SECTIONS = [
    # (旁白, 卡片)
    ("大家好，我是一名普通学生。课余时间，我做了两个自动做视频的小项目。"
     "它们不完美，但每一版都在进步。今天想给你看看，它们是怎么长大的。",
     "card_title"),
    ("第一个叫 bili-history，一条军事历史纪录片的自动产线：AI 写讲稿，"
     "本地大模型配音，维基百科的史料照片自动配图，剪成三分钟，投稿B站。"
     "从选题到发布，一条命令。",
     "card_history"),
    ("第二个叫 see-china，面向海外观众的英文频道：中国的园林、早餐、节日，"
     "配上英文旁白和真实镜头，让不了解中国的人，看见中国的日常。",
     "card_seechina"),
    ("说实话，它们一开始的问题很多：审查服务一挂，视频就带着问题发出去了；"
     "两个程序同时跑，会互相打架；渲染到第十段失败了，前面九段全部白做。",
     "card_problems"),
    ("所以我学着给它们补课：给每个任务一本成长手册，做到哪记到哪，失败了"
     "就从断点接着来；给审查装上闸门，AI 看不清的内容，宁可停下等人；"
     "给成片加自动质检，黑屏、静音、字幕漂移，全都在发布前拦下来。",
     "card_progress1"),
    ("测试从零写到了一百一十八项，每次提交代码，服务器会自动跑一遍全绿才放行。"
     "前几周，敦刻尔克那期视频真的从这条产线上走了下来，审查拦过四稿问题，"
     "最后安全发布。那是它们第一次，完整地长大了。",
     "card_progress2"),
    ("我知道，这只是学生的创作，比不上专业团队，也不期待它有多好。"
     "但我期望它逐渐进步——就像做视频的这两个项目，和我自己。",
     "card_end"),
]


def card(kind: str, path: str):
    img = Image.new("RGB", (W, H), (20, 22, 28))
    d = ImageDraw.Draw(img)
    big = ImageFont.truetype(FONT, 88)
    mid = ImageFont.truetype(FONT, 52)
    small = ImageFont.truetype(FONT, 36)
    tiny = ImageFont.truetype(FONT, 30)

    def center(text, y, font, color=INK):
        bb = d.textbbox((0, 0), text, font=font)
        d.text(((W - bb[2] + bb[0]) / 2, y), text, font=font, fill=color)

    if kind == "card_title":
        center("两个会自己进步的项目", 300, big, ACCENT)
        center("一个学生的课余创作", 480, mid)
        center("bili-history · see-china", 600, small, DIM)
        center("不期待有多好，但期望逐渐进步", 700, small, WARM)
    elif kind == "card_history":
        center("军事号 bili-history", 280, big, ACCENT)
        center("AI 写稿 → 大模型配音 → 史料配图 → 自动投稿", 470, mid)
        center("一条命令，从选题到B站发布", 590, mid)
        center("已发布 25 期军史纪录片", 690, small, DIM)
    elif kind == "card_seechina":
        center("英文号 see-china", 280, big, WARM)
        center("面向海外观众的中国故事", 470, mid)
        center("园林 · 早餐 · 节日 · 城市日常", 590, mid)
        center("英文旁白 + 真实镜头", 690, small, DIM)
    elif kind == "card_problems":
        center("它们曾经的问题", 300, big, (232, 120, 120))
        center("审查挂了会放行问题视频", 460, mid)
        center("双开互相打架 · 断在半路全部重来", 570, mid)
        center("—— 每一个都被真实踩过 ——", 700, small, DIM)
    elif kind == "card_progress1":
        center("第一课：可靠性", 280, big, GREEN)
        center("任务手册 · 断点续跑", 450, mid)
        center("审查闸门 · 宁停不错", 565, mid)
        center("自动质检 · 黑屏静音字幕全拦", 680, mid)
    elif kind == "card_progress2":
        center("第二课：证明自己", 300, big, GREEN)
        center("测试 0 → 118 项 · CI 六连绿", 460, mid)
        center("审查拦下四稿问题 · 名言防线", 570, mid)
        center("敦刻尔克一期，真实发布", 680, mid, WARM)
    elif kind == "card_end":
        center("这只是学生的创作", 320, big, ACCENT)
        center("不期待有多好", 500, mid)
        center("但期望逐渐进步", 620, big, WARM)
        center("—— 项目，和我自己 ——", 780, small, DIM)
    center("github.com/zhaohongjun20-creator", 950, tiny, (110, 118, 130))
    img.save(path)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "clips").mkdir(exist_ok=True)
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    cosy = cfg["narrator"]["cosy"]

    # ① 卡片
    imgs = []
    for i, (_, kind) in enumerate(SECTIONS):
        p = OUT / f"card_{i:02d}.png"
        card(kind, str(p))
        imgs.append(str(p))
    print("卡片就绪")

    # ② CosyVoice 批量配音（纪录片腔；cnnum 由 narrator 内部处理）
    wavs = [str(OUT / f"sec_{i:02d}.wav") for i in range(len(SECTIONS))]
    if not all(Path(w).exists() and Path(w).stat().st_size > 1000 for w in wavs):
        secs = [{"text": t} for t, _ in SECTIONS]
        t0 = time.time()
        wavs = _synth_cosy_batch(secs, str(OUT), cosy)
        print(f"配音完成 {time.time()-t0:.0f}s")
    else:
        print("配音复用已有 wav")

    # ③ 段落渲染（Ken Burns 轮换）
    modes = ["in", "panright", "out", "panleft"]
    fade = 0.6
    voices = []
    for i, (text, _) in enumerate(SECTIONS):
        dur = audio_duration(wavs[i])
        tail = 0.0 if i == len(SECTIONS) - 1 else fade
        voices.append({"path": wavs[i], "duration": dur, "text": text,
                       "tail": tail})
        print(f"渲染段落{i} {dur:.1f}s …")
        build_section_clip(imgs[i], wavs[i], dur + tail,
                           str(OUT / "clips" / f"sec_{i:02d}.mp4"),
                           mode=modes[i % 4], stickers=[])
    srt = make_srt(voices, str(OUT / "final.srt"))  # 相对 cwd 也行，slideshow 已做盘符转义

    # ④ 合成（BGM + 转场 + 字幕）
    import random
    bgm = sorted((ROOT / cfg["bgm_dir"]).glob("*.mp3"))
    clips = [str(OUT / "clips" / f"sec_{i:02d}.mp4") for i in range(len(SECTIONS))]
    final = str(OUT / "final.mp4")
    compose_final(clips, str(random.choice(bgm)), srt, final,
                  bgm_volume=0.18, fade=fade,
                  transitions=["fade", "dissolve", "wipeleft", "circleopen",
                               "fadeblack"])
    print(f"成片: {final}")

    ok, problems = qc_video(final, srt, str(OUT / "cover.jpg"), cfg["video"],
                            narration_total=sum(v["duration"] for v in voices))
    print("质检:", "通过" if ok else problems)
    # 封面用第一张卡（真 JPEG——PNG 字节改名 jpg 会过不了 ffmpeg 探测）
    Image.open(imgs[0]).convert("RGB").save(OUT / "cover.jpg", "JPEG", quality=92)


if __name__ == "__main__":
    main()
