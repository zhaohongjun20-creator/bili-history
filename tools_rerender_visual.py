"""一次性脚本：军史线观感升级 A/B 验证——复用法莱斯任务缓存，纯视觉重渲。

旧：data/jobs/20261105_113540_b8347e41a5/final.mp4（0.7.7 抽帧封面/整段字幕/线性运镜/5 转场）
新：data/jobs/20261105_113540_b8347e41a5/final_v2.mp4（0.8.0 设计封面/分句字幕/缓动/3 转场）
复用 narration/*.wav 与 materials/img_*，不碰 topics.db、不投稿、不生成讲稿。
"""
import json
import random
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from src.slideshow import (build_section_clip, compose_final, make_srt,
                           _fmt_ts)
from src.cover import make_cover
from src.qc import qc_video

ROOT = Path(__file__).parent
JOB = ROOT / "data" / "jobs" / "20261105_113540_b8347e41a5"
cfg = yaml.safe_load(open(ROOT / "config.yaml", encoding="utf-8"))

t_all = time.time()
out_clips = JOB / "clips_v2"
out_clips.mkdir(exist_ok=True)

idx = json.loads((JOB / "narration" / "index.json").read_text(encoding="utf-8"))
assets = json.loads((JOB / "materials" / "assets.json").read_text(encoding="utf-8"))
fade = float(cfg.get("transition", {}).get("fade_seconds", 0.6))
styles = cfg["transition"]["styles"]
n = len(idx)

# ① 按新缓动/新字幕重渲 15 段（音轨原样复用，不重新 TTS）
t0 = time.time()
voices = []
modes = ["in", "panright", "out", "panleft"]
for i, v in enumerate(idx):
    dur = v["duration"]
    tail = 0.0 if i == n - 1 else fade
    img = JOB / "materials" / assets[i]["file"].split("/")[-1]
    outc = out_clips / f"sec_{i:02d}.mp4"
    if outc.exists() and outc.stat().st_size > 0:
        print(f"段落{i} 复用已渲 clip")
    else:
        build_section_clip(str(img), v["path"], dur + tail,
                           str(outc), mode=modes[i % 4], stickers=[])
    voices.append({"path": v["path"], "duration": dur, "text": v["text"],
                   "tail": tail})
    print(f"段落{i} {dur:.1f}s 渲染完成")
print(f"渲染 {n} 段耗时 {time.time()-t0:.0f}s")

# ② 新版分句字幕
srt = make_srt(voices, str(JOB / "final_v2.srt"))
cues = open(srt, encoding="utf-8").read().count("\n\n")
print(f"字幕条数: {cues}（旧版为 {n} 条整段）")

# ③ 合成（新转场池 + ducking）
t1 = time.time()
bgms = sorted((ROOT / cfg["bgm_dir"]).glob("*.mp3"))
clips = [str(out_clips / f"sec_{i:02d}.mp4") for i in range(n)]
final_v2 = str(JOB / "final_v2.mp4")
compose_final(clips, str(random.choice(bgms)), srt, final_v2,
              bgm_volume=float(cfg.get("bgm_volume", 0.22)), fade=fade,
              transitions=styles)
print(f"合成耗时 {time.time()-t1:.0f}s")

# ④ 设计封面 A/B
make_cover(str(JOB / "materials" / assets[0]["file"].split("/")[-1]),
           json.loads((JOB / "script.json").read_text(encoding="utf-8"))["title"],
           str(JOB / "cover_v2.jpg"))

# ⑤ QC
ok, problems = qc_video(final_v2, srt, str(JOB / "cover_v2.jpg"),
                        cfg["video"], narration_total=None)
print("QC:", "通过" if ok else problems)

# ⑥ A/B 报告
old_sz = (JOB / "final.mp4").stat().st_size
new_sz = (JOB / "final_v2.mp4").stat().st_size
report = f"""观感升级 A/B（法莱斯口袋同素材重渲）
- 旧版 final.mp4: {old_sz/1e6:.1f} MB（整段字幕 {n} 条 / 线性运镜 / 5 转场 / 抽帧封面）
- 新版 final_v2.mp4: {new_sz/1e6:.1f} MB（分句字幕 {cues} 条 / 余弦缓动 / 3 转场 / 设计封面）
- 重渲耗时: 渲染 {time.time()-t0:.0f}s + 合成 {time.time()-t1:.0f}s
- QC: {'通过' if ok else problems}
"""
(JOB / "ab_report.txt").write_text(report, encoding="utf-8")
print(report)
