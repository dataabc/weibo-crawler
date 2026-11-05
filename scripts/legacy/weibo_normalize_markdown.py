#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""统一存量 markdown 的微博块格式（一次性格式化工具）。

新代码写入的块已是「块头后空行、marker 后空行、edited_at 后空行」；但升级前
写入的存量块仍是旧格式（块头紧贴 marker、marker 紧贴正文）。本脚本统一补齐
空行（只加空行、不改任何内容），dry-run 默认，--fix 先备份再写。

示例：
    python scripts/weibo_normalize_markdown.py
    python scripts/weibo_normalize_markdown.py --fix
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime

PROJECT_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from util.core import archive_index  # noqa: E402

logger = logging.getLogger("weibo")
WEIBO_ID_RE = re.compile(r"^<!--\s*weibo_id:\s*\d+\s*-->\s*$")
HEADING_RE = re.compile(
    r"^### (?:\d{2}:\d{2}:\d{2}|\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})\s*$"
)
EDITED_AT_PREFIX = "<!-- edited_at:"


def _write_text_atomic(path: str, text: str) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=os.path.basename(path) + ".", suffix=".tmp", dir=directory
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def normalize_text(text: str) -> tuple[str, int]:
    """返回 (格式化后文本, 插入的空行数)。幂等。"""
    lines = text.split("\n")
    out: list[str] = []
    inserted = 0

    def next_line(index: int):
        return lines[index + 1] if index + 1 < len(lines) else None

    for index, line in enumerate(lines):
        out.append(line)
        stripped = line.strip()
        nxt = next_line(index)
        if nxt is None or not nxt.strip():
            continue
        if HEADING_RE.match(stripped) and not nxt.lstrip().startswith(("#", "---")):
            out.append("")
            inserted += 1
        elif WEIBO_ID_RE.match(stripped) and not nxt.lstrip().startswith(
            (EDITED_AT_PREFIX, "---", "### ")
        ):
            out.append("")
            inserted += 1
        elif stripped.startswith(EDITED_AT_PREFIX) and not nxt.lstrip().startswith(
            ("---", "### ")
        ):
            out.append("")
            inserted += 1
    return "\n".join(out), inserted


def normalize_file(path: str, *, dry_run: bool = True) -> dict:
    item = {"path": path, "inserted": 0, "status": "unchanged", "reason": ""}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            original = handle.read()
    except OSError as exc:
        item.update(status="error", reason=f"读取失败: {exc}")
        return item
    updated, inserted = normalize_text(original)
    if inserted == 0 or updated == original:
        return item
    item["inserted"] = inserted
    if dry_run:
        item.update(status="would_fix", reason=f"需补 {inserted} 个空行")
        return item
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        shutil.copy2(path, f"{path}.bak.{stamp}")
    except OSError:
        item.update(status="error", reason="备份失败，未写入")
        return item
    try:
        _write_text_atomic(path, updated)
    except OSError as exc:
        item.update(status="error", reason=f"写入失败: {exc}")
        return item
    item.update(status="fixed", reason=f"已补 {inserted} 个空行")
    return item


def normalize_output_dir(output_dir: str, *, dry_run: bool = True) -> dict:
    summary = {
        "mode": "dry-run" if dry_run else "fix",
        "files": [],
        "total": 0,
        "would_fix": 0,
        "fixed": 0,
        "unchanged": 0,
        "errors": 0,
        "inserted": 0,
    }
    if not os.path.isdir(output_dir):
        summary["errors"] = 1
        return summary
    for name in sorted(os.listdir(output_dir)):
        user_dir = os.path.join(output_dir, name)
        if not os.path.isdir(user_dir) or name.startswith("."):
            continue
        if name in archive_index.DEFAULT_EXCLUDE_DIRS or name in {"report"}:
            continue
        for md_path in archive_index.iter_active_md_paths(user_dir):
            item = normalize_file(md_path, dry_run=dry_run)
            summary["files"].append(item)
            summary["total"] += 1
            status = item["status"]
            if status == "would_fix":
                summary["would_fix"] += 1
                summary["inserted"] += item["inserted"]
            elif status == "fixed":
                summary["fixed"] += 1
                summary["inserted"] += item["inserted"]
            elif status == "error":
                summary["errors"] += 1
            else:
                summary["unchanged"] += 1
    return summary


def render_summary(summary: dict) -> str:
    lines = [
        "Markdown 块格式统一报告",
        f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"模式: {summary['mode']}",
        f"文件总数: {summary['total']}",
        f"需要格式化: {summary['would_fix']}",
        f"已格式化: {summary['fixed']}",
        f"无需处理: {summary['unchanged']}",
        f"共补空行: {summary['inserted']}",
        f"失败: {summary['errors']}",
        "=" * 60,
    ]
    for item in summary["files"]:
        if item["status"] in ("would_fix", "fixed", "error"):
            lines.append(f"[{item['status']}] {item['path']}（{item['reason']}）")
    lines.append("报告完毕。")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="统一存量 markdown 微博块格式")
    parser.add_argument("--dry-run", action="store_true", help="只报告（默认）")
    parser.add_argument("--fix", action="store_true", help="实际格式化（先备份）")
    parser.add_argument("--output-dir", default=None, help="数据目录（默认读配置）")
    parser.add_argument("--report", default=None, help="报告输出路径")
    args = parser.parse_args(argv)
    if args.output_dir:
        output_dir = args.output_dir
    else:
        try:
            import weibo
            from util.core import file_lock

            config = weibo.get_config()
            output_dir = file_lock.resolve_output_dir(
                PROJECT_DIR, config.get("output_directory")
            )
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"读取配置失败: {exc}")
            return 1
    summary = normalize_output_dir(output_dir, dry_run=not args.fix)
    text = render_summary(summary)
    print(text)
    if args.report:
        _write_text_atomic(args.report, text)
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
