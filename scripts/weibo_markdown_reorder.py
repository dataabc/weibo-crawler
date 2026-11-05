#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""微博 markdown 统一重排。

用途：把现有 markdown 文件按「微博发布时间」重新排序，让时间线严格有序；
日常增量写入已由主爬自动原位插入，本脚本只用于升级时对存量文件做一次重排。

安全设计：
- ``--dry-run``（默认）：只输出报告，不写任何文件；
- ``--fix``：先备份 ``.bak.<YYYYMMDD_HHMMSS>``，再原子写回；
- **含无 marker 人工块的文件整个跳过**（不重排、不丢内容），报告列出；
- 只处理用户目录下的 markdown（跳过报告目录、媒体目录、不可见目录）；
- 幂等：重复执行第二次应全部「无变化」。

示例：
    python scripts/weibo_markdown_reorder.py                 # dry-run 全部用户
    python scripts/weibo_markdown_reorder.py --users 1234567890
    python scripts/weibo_markdown_reorder.py --fix --users 1234567890"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from datetime import datetime

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from util.core import atomic_io  # noqa: E402
from util.core import file_lock, markdown_blocks  # noqa: E402

logger = logging.getLogger("weibo")
_DATE_IN_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")
DEFAULT_EXCLUDE = {"report", "已删除", "编辑历史"}

def _backup(path: str) -> str | None:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{path}.bak.{stamp}"
    try:
        import shutil

        shutil.copy2(path, backup_path)
        return backup_path
    except OSError as exc:
        logger.warning("备份失败（跳过该文件）: %s（%s）", path, exc)
        return None


def _md_files_in_dir(directory: str, exclude_names: set[str]) -> list[str]:
    """列出用户目录下（含一层月目录）的 md 文件，跳过排除目录。"""
    result: list[str] = []
    try:
        entries = sorted(os.listdir(directory))
    except OSError:
        return result
    for name in entries:
        if name.startswith(".") or name in exclude_names:
            continue
        path = os.path.join(directory, name)
        if os.path.isdir(path):
            # 月目录（YYYY-MM）或其它子目录：只取其中的 md，不递归
            try:
                for sub in sorted(os.listdir(path)):
                    if sub.endswith(".md") and not sub.startswith("._"):
                        result.append(os.path.join(path, sub))
            except OSError:
                continue
        elif name.endswith(".md") and not name.startswith("._"):
            result.append(path)
    return result


def reorder_file(path: str, order: str, *, dry_run: bool, backup: bool) -> dict:
    """重排单个文件，返回结果字典（不抛异常）。"""
    item = {
        "path": path,
        "status": "unchanged",
        "blocks": 0,
        "reason": "",
    }
    try:
        with open(path, "r", encoding="utf-8") as handle:
            original = handle.read()
    except OSError as exc:
        item.update(status="error", reason=f"读取失败: {exc}")
        return item
    name = os.path.basename(path)
    match = _DATE_IN_NAME_RE.match(name)
    fallback_date = match.group(1) if match else None
    doc = markdown_blocks.parse_markdown(original, fallback_date=fallback_date)
    item["blocks"] = len(doc.blocks)
    unparsed = [b for b in doc.blocks if not b.sortable]
    if unparsed:
        item.update(
            status="skipped",
            reason=f"含 {len(unparsed)} 个无微博 ID 的块，按安全策略跳过",
        )
        return item
    if len(doc.blocks) < 2:
        item["reason"] = "块数不足 2，无需重排"
        return item
    markdown_blocks.sort_blocks(doc, order)
    rendered = doc.render()
    if rendered == original:
        item["reason"] = "已有序"
        return item
    if dry_run:
        item.update(status="would_reorder", reason="顺序需要调整")
        return item
    if backup:
        backup_path = _backup(path)
        if not backup_path:
            item.update(status="error", reason="备份失败，未写入")
            return item
    try:
        atomic_io.atomic_write_text(path, rendered)
    except OSError as exc:
        item.update(status="error", reason=f"写入失败: {exc}")
        return item
    item.update(status="reordered", reason="已重排")
    return item


def reorder_output_dir(
    output_dir: str,
    *,
    order: str = "old_to_new",
    dry_run: bool = True,
    backup: bool = True,
    users: list[str] | None = None,
    exclude_names: set[str] | None = None,
) -> dict:
    """遍历 output_dir 下所有用户目录并重排，返回汇总结果。"""
    exclude = set(DEFAULT_EXCLUDE)
    exclude.update(exclude_names or set())
    wanted = {str(u) for u in users} if users else None
    summary = {
        "mode": "dry-run" if dry_run else "fix",
        "order": order,
        "files": [],
        "total": 0,
        "would_reorder": 0,
        "reordered": 0,
        "unchanged": 0,
        "skipped": 0,
        "errors": 0,
    }
    if not os.path.isdir(output_dir):
        summary["errors"] = 1
        summary["files"].append(
            {"path": output_dir, "status": "error", "blocks": 0, "reason": "输出目录不存在"}
        )
        return summary
    user_dirs = []
    for name in sorted(os.listdir(output_dir)):
        if name.startswith(".") or name in exclude:
            continue
        path = os.path.join(output_dir, name)
        if os.path.isdir(path):
            if wanted is not None and name not in wanted:
                # 允许用用户 ID 匹配目录名之外的场景由调用方自行过滤
                continue
            user_dirs.append(path)
    for user_dir in user_dirs:
        for md_path in _md_files_in_dir(user_dir, exclude):
            item = reorder_file(md_path, order, dry_run=dry_run, backup=backup)
            summary["files"].append(item)
            summary["total"] += 1
            status = item["status"]
            if status == "would_reorder":
                summary["would_reorder"] += 1
            elif status == "reordered":
                summary["reordered"] += 1
            elif status == "skipped":
                summary["skipped"] += 1
            elif status == "error":
                summary["errors"] += 1
            else:
                summary["unchanged"] += 1
    return summary


def render_summary(summary: dict, limit_details: int = 200) -> str:
    lines = [
        "Markdown 统一重排报告",
        f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"模式: {summary['mode']}（排序方向: {summary['order']}）",
        f"文件总数: {summary['total']}",
        f"需要重排: {summary['would_reorder']}",
        f"已重排: {summary['reordered']}",
        f"已有序/无变化: {summary['unchanged']}",
        f"跳过（含无 marker 块）: {summary['skipped']}",
        f"失败: {summary['errors']}",
        "=" * 60,
    ]
    interesting = [
        item
        for item in summary["files"]
        if item["status"] in ("would_reorder", "reordered", "skipped", "error")
    ]
    if not interesting:
        lines.append("（全部无需处理）")
    for item in interesting[:limit_details]:
        rel = item["path"]
        lines.append(f"[{item['status']}] {rel}（{item['blocks']} 块）")
        if item.get("reason"):
            lines.append(f"    原因: {item['reason']}")
    if len(interesting) > limit_details:
        lines.append(f"……其余 {len(interesting) - limit_details} 条省略")
    lines.append("报告完毕。")
    return "\n".join(lines) + "\n"


def _resolve_output_dir(config: dict) -> str:
    return file_lock.resolve_output_dir(PROJECT_DIR, config.get("output_directory"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="微博 markdown 统一重排（升级迁移工具）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true", help="只输出报告，不写文件（默认）")
    parser.add_argument("--fix", action="store_true", help="实际重排（先备份）")
    parser.add_argument("--users", nargs="*", default=None, help="只处理指定目录名的用户")
    parser.add_argument(
        "--order", choices=["old_to_new", "new_to_old"], default=None,
        help="排序方向（默认读配置 markdown_sort_order）",
    )
    parser.add_argument("--no-backup", action="store_true", help="不备份（不推荐）")
    parser.add_argument("--output-dir", default=None, help="数据输出目录（默认读配置）")
    parser.add_argument("--report", default=None, help="报告输出路径（默认打印到 stdout）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    dry_run = not args.fix
    if args.dry_run and args.fix:
        logger.error("--dry-run 与 --fix 只能选一个")
        return 1
    try:
        import weibo

        config = weibo.get_config()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("读取配置失败: %s", exc)
        return 1
    output_dir = args.output_dir or _resolve_output_dir(config)
    order = args.order or config.get("markdown_sort_order", "old_to_new")
    exclude = set(config.get("invisible_weibo_dirs") or ())
    summary = reorder_output_dir(
        output_dir,
        order=order,
        dry_run=dry_run,
        backup=not args.no_backup,
        users=args.users,
        exclude_names=exclude,
    )
    report_text = render_summary(summary)
    print(report_text)
    if args.report:
        atomic_io.atomic_write_text(args.report, report_text)
    if summary["errors"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
