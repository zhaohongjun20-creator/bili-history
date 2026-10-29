"""配音预设 A/B 试听台：同一段文案 × 全部 CosyVoice2 预设 → 批量出样音供试听。

用法：
  python tts_studio.py                       # 用内置样音文案，跑 config 里全部预设
  python tts_studio.py --text-file t.txt     # 指定文案（军史风格段落效果最准）
  python tts_studio.py --presets a,b,c       # 只跑指定预设
  python tts_studio.py --apply jimu_calm     # 试听后切换产线预设（改 config.yaml）
  python tts_studio.py --list                # 列出预设

产物：data/tts_studio/<时间戳>/<预设名>.wav + INDEX.md（含每版配方与切换命令）。
质量是听出来的：选型后用一期真实文案 A/B，再 --apply 固化。
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from src import narrator

ROOT = Path(__file__).parent
OUT_ROOT = ROOT / "data" / "tts_studio"

SAMPLE_TEXT = ("1940年5月26日，敦刻尔克的海面上挤满了大大小小的船只。"
               "33.8万英法联军被困在海滩上，身后是步步逼近的德军装甲部队。"
               "就在这绝境之中，一场被称为奇迹的大撤退，即将开始。")


def _load_cosy() -> dict:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    return cfg.get("narrator", {}).get("cosy") or {}


def _presets(cosy: dict, only: list[str] | None) -> dict[str, dict]:
    presets = cosy.get("presets") or {}
    if only:
        missing = [n for n in only if n not in presets]
        if missing:
            raise SystemExit(f"预设不存在: {missing}（--list 查看）")
        return {n: presets[n] for n in only}
    if not presets:
        raise SystemExit("config.yaml narrator.cosy.presets 为空——先在配置里定义预设")
    return presets


def _apply_preset(name: str):
    """把 preset: <name> 写入 config.yaml 的 cosy 段（行级编辑，保留注释）。"""
    path = ROOT / "config.yaml"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    out, in_cosy, done = [], False, False
    for ln in lines:
        if re.match(r"^  cosy:", ln):
            in_cosy = True
        elif re.match(r"^\S", ln):
            in_cosy = False
        if in_cosy and re.match(r"^    preset:", ln):
            out.append(f"    preset: \"{name}\"\n")
            done = True
            continue
        out.append(ln)
        if in_cosy and not done and re.match(r"^    enable:", ln):
            out.append(f"    preset: \"{name}\"\n")
            done = True
    if not done:
        raise SystemExit("config.yaml 里找不到 cosy 段——手动加一行 '    preset: <名>'")
    path.write_text("".join(out), encoding="utf-8")
    print(f"已切换产线预设 → {name}（narrator.cosy.preset）")


def run(text: str, only: list[str] | None):
    cosy = _load_cosy()
    if not cosy.get("enable"):
        print("警告: narrator.cosy.enable=false（试听不受影响）")
    presets = _presets(cosy, only)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = OUT_ROOT / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    index = ["# 配音预设试听", f"", f"文案: {text[:40]}…", ""]
    for name, p in presets.items():
        print(f"合成 [{name}] …")
        try:
            paths = narrator._synth_cosy_batch(
                [{"text": text}], str(out_dir), {**cosy, "preset": name},
                indices=[0])
            wav = Path(paths[0])
            wav.rename(out_dir / f"{name}{wav.suffix}")
            size_kb = (out_dir / f"{name}{wav.suffix}").stat().st_size // 1024
            index += [f"## {name}", f"- 文件: {name}{wav.suffix} ({size_kb}KB)"]
        except Exception as e:
            index += [f"## {name}", f"- ❌ 失败: {str(e)[:150]}"]
        cfg_line = json.dumps(p, ensure_ascii=False)
        index += [f"- 配方: `{cfg_line}`",
                  f"- 切换: `python tts_studio.py --apply {name}`", ""]
    (out_dir / "INDEX.md").write_text("\n".join(index), encoding="utf-8")
    print(f"\n完成 → {out_dir}\n试听后切换: python tts_studio.py --apply <预设名>")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text-file", help="试听文案文件（默认内置军史样音段）")
    ap.add_argument("--presets", help="逗号分隔，只跑这些预设")
    ap.add_argument("--apply", help="切换产线预设到指定名字")
    ap.add_argument("--list", action="store_true", help="列出全部预设")
    a = ap.parse_args()

    cosy = _load_cosy()
    if a.list or (not a.apply and not a.text_file and a.presets is None and False):
        for n, p in (cosy.get("presets") or {}).items():
            cur = " ← 当前" if cosy.get("preset") == n else ""
            print(f"{n}{cur}\n   {json.dumps(p, ensure_ascii=False)}")
        return
    if a.apply:
        _apply_preset(a.apply)
        return
    text = Path(a.text_file).read_text(encoding="utf-8").strip() \
        if a.text_file else SAMPLE_TEXT
    run(text, a.presets.split(",") if a.presets else None)


if __name__ == "__main__":
    main()
