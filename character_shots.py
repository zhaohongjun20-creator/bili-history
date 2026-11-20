"""角色向测试片：三五句 brief → 编号分镜提示词。

不接入 autopost，不生成视频，不改史料 Ken Burns。只调用本仓库已有的
GLM（环境变量 ZHIPU_API_KEY，默认模型 glm-4-flash）。

用法：
  python character_shots.py path/to/brief.md
  python character_shots.py path/to/brief.md -o path/to/out.md
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.character_shots import DEFAULT_MODEL, generate_from_brief  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="把三五句角色片 brief 写成编号分镜提示词（GLM / ZHIPU_API_KEY）")
    parser.add_argument("brief", help="brief 的 markdown 或文本路径")
    parser.add_argument("-o", "--output", help="输出路径，默认与 brief 同目录、文件名加「-分镜提示词」")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"智谱模型 id，默认 {DEFAULT_MODEL}")
    args = parser.parse_args(argv)

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")

    brief_path = Path(args.brief)
    if not brief_path.is_file():
        raise SystemExit(f"找不到 brief: {brief_path}")
    dest = Path(args.output) if args.output else brief_path.with_name(
        brief_path.stem + "-分镜提示词.md")
    if dest.resolve() == brief_path.resolve():
        raise SystemExit("输出路径和 brief 相同，请换一个 -o")

    try:
        text = generate_from_brief(brief_path.read_text(encoding="utf-8"),
                                   model=args.model)
    except (RuntimeError, ValueError) as e:
        raise SystemExit(str(e)) from e

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    print(dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
