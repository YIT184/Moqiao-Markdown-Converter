"""Command-line interface for pdf2mdx."""

from __future__ import annotations

import argparse
import os
import sys
import pathlib
from typing import List, Optional

from .converters import convert_file, supported_extensions
from .markdown_utils import safe_stem

SUPPORTED_EXTENSIONS = set(supported_extensions())


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point for ``pdf2mdx`` CLI. Returns exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Validate inputs
    inputs: List[str] = []
    for pattern in args.INPUT:
        p = pathlib.Path(pattern)
        if p.is_file():
            inputs.append(str(p))
        elif p.is_dir():
            for ext in SUPPORTED_EXTENSIONS:
                for f in sorted(p.glob(f"*{ext}")):
                    inputs.append(str(f))
        elif "*" in pattern or "?" in pattern:
            # Simple glob — walk parent directory
            parent = p.parent
            if not parent.is_dir():
                parent = pathlib.Path(".")
            for f in sorted(parent.glob(p.name)):
                if f.suffix.lower() in SUPPORTED_EXTENSIONS:
                    inputs.append(str(f))
        else:
                print(f"[警告] 找不到输入文件 '{pattern}'", file=sys.stderr)

    if not inputs:
        print("错误: 没有找到可转换的文件 (PDF/XMind)。", file=sys.stderr)
        return 1

    output_dir = args.output_dir or "."
    os.makedirs(output_dir, exist_ok=True)

    success_count = 0
    fail_count = 0

    for filepath in inputs:
        ext = pathlib.Path(filepath).suffix.lower()
        stem = pathlib.Path(filepath).stem
        out_path = os.path.join(output_dir, f"{safe_stem(stem)}.md")

        try:
            print(f"转换 {ext[1:].upper()}: {filepath} → {out_path}")
            md_text = convert_file(
                filepath,
                output_dir,
                {
                    "images": not args.no_images,
                    "tables": not args.no_tables,
                    "vectors": not args.no_vectors,
                    "enhance_line_art": not args.no_enhance_line_art,
                    "password": args.password,
                },
            )

            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write(md_text)

            print(f"  [完成] {out_path}")
            success_count += 1

        except Exception as exc:
            print(f"  [失败] {exc}", file=sys.stderr)
            fail_count += 1

    print()
    print(f"转换完成: 成功 {success_count}, 失败 {fail_count}")

    return 0 if fail_count == 0 else 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf2mdx",
        description="将 PDF、XMind、Office、HTML 和文本数据文件转换为 Markdown",
    )
    parser.add_argument(
        "INPUT",
        nargs="+",
        help="输入文件或目录，支持通配符",
    )
    parser.add_argument(
        "-o", "--output-dir",
        default=".",
        help="输出目录（默认当前目录）",
    )
    parser.add_argument(
        "--no-images",
        action="store_true",
        help="不提取/保留图片",
    )
    parser.add_argument(
        "--no-tables",
        action="store_true",
        help="不检测 PDF 表格",
    )
    parser.add_argument(
        "--no-vectors",
        action="store_true",
        help="不提取 PDF 矢量图形",
    )
    parser.add_argument(
        "--no-enhance-line-art",
        action="store_true",
        help="不自动增强 PDF 中的单色线稿",
    )
    parser.add_argument(
        "--password",
        default=None,
        help="加密 PDF 的密码",
    )
    return parser


if __name__ == "__main__":
    sys.exit(main())
