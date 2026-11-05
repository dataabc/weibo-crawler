#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""清理 markdown 评论块中的重复评论组（一次性修复工具）。

- 同一评论按 cid 优先、无 cid 按「去掉 cid 注释后的文本」去重；
- 优先保留带 cid 的那一条；
- 只重写评论块，正文、媒体链接、块顺序都不变；
- dry-run（默认）只输出报告；--fix 先备份 ``.bak.<YYYYMMDD_HHMMSS>`` 再原子写。

示例：
    python scripts/weibo_dedup_comment_groups.py
    python scripts/weibo_dedup_comment_groups.py --fix"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
from datetime import datetime

PROJECT_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from util.core import file_lock, markdown_blocks  # noqa: E402

logger = logging.getLogger("weibo")
DEFAULT_EXCLUDE = {"report", "已不可见", "已删除", "编辑历史"}


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


def _backup(path: str) -> str | None:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{path}.bak.{stamp}"
    try:
        import shutil

        shutil.copy2(path, backup_path)
        return backup_path
    except OSError as exc:
        logger.warning("备份失败（跳过）: %s（%s）", path, exc)
        return None


def _md_files_in_dir(directory: str, exclude_names: set[str]) -> list[str]:
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
            try:
                for sub in sorted(os.listdir(path)):
                    if sub.endswith(".md") and not sub.startswith("._"):
                        result.append(os.path.join(path, sub))
            except OSError:
                continue
        elif name.endswith(".md") and not name.startswith("._"):
            result.append(path)
    return result


def dedup_file(path: str, *, dry_run: bool, backup: bool) -> dict:
    """处理单个 md；返回 {path,status,groups_removed,reason}。"""
    item = {"path": path, "status": "unchanged", "groups_removed": 0, "reason": ""}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            original = handle.read()
    except OSError as exc:
        item.update(status="error", reason=f"读取失败: {exc}")
        return item
    fallback = os.path.splitext(os.path.basename(path))[0]
    if not fallback[:4].isdigit():
        fallback = None
    doc = markdown_blocks.parse_markdown(original, fallback_date=fallback)
    changed = False
    removed = 0
    for block in doc.blocks:
        if not block.comments:
            continue
        before = len(block.comments)
        deduped = markdown_blocks.merge_comment_groups(block.comments, [])
        if len(deduped) < before:
            block.comments = deduped
            block.raw = markdown_blocks.rebuild_block_text(block)
            changed = True
            removed += before - len(deduped)
    if not changed:
        return item
    item.update(
        status="would_fix" if dry_run else "fixed",
        groups_removed=removed,
        reason=f"删除 {removed} 个重复评论组",
    )
    if dry_run:
        return item
    if backup and not _backup(path):
        item.update(status="error", reason="备份失败，未写入")
        return item
    try:
        _write_text_atomic(path, doc.render())
    except OSError as exc:
        item.update(status="error", reason=f"写入失败: {exc}")
        return item
    item["status"] = "fixed"
    return item


def dedup_output_dir(
    output_dir: str,
    *,
    dry_run: bool = True,
    backup: bool = True,
    users: list[str] | None = None,
) -> dict:
    summary = {
        "mode": "dry-run" if dry_run else "fix",
        "files": [],
        "total": 0,
        "would_fix": 0,
        "fixed": 0,
        "unchanged": 0,
        "errors": 0,
        "groups_removed": 0,
    }
    if not os.path.isdir(output_dir):
        summary["errors"] = 1
        summary["files"].append(
            {"path": output_dir, "status": "error", "groups_removed": 0, "reason": "目录不存在"}
        )
        return summary
    wanted = {str(u) for u in users} if users else None
    for name in sorted(os.listdir(output_dir)):
        if name.startswith(".") or name in DEFAULT_EXCLUDE:
            continue
        user_dir = os.path.join(output_dir, name)
        if not os.path.isdir(user_dir):
            continue
        if wanted is not None and name not in wanted:
            continue
        for md_path in _md_files_in_dir(user_dir, DEFAULT_EXCLUDE):
            item = dedup_file(md_path, dry_run=dry_run, backup=backup)
            summary["files"].append(item)
            summary["total"] += 1
            if item["status"] in ("would_fix", "fixed"):
                summary["groups_removed"] += item["groups_removed"]
                if item["status"] == "would_fix":
                    summary["would_fix"] += 1
                else:
                    summary["fixed"] += 1
            elif item["status"] == "error":
                summary["errors"] += 1
            else:
                summary["unchanged"] += 1
    return summary


def render_summary(summary: dict) -> str:
    lines = [
        "评论块重复清理报告",
        f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"模式: {summary['mode']}",
        f"文件总数: {summary['total']}",
        f"需要清理: {summary['would_fix']}",
        f"已清理: {summary['fixed']}",
        f"无需处理: {summary['unchanged']}",
        f"删除重复组数: {summary['groups_removed']}",
        f"失败: {summary['errors']}",
        "=" * 60,
    ]
    for item in summary["files"]:
        if item["status"] in ("would_fix", "fixed", "error"):
            lines.append(
                f"[{item['status']}] {item['path']}（{item['groups_removed']} 组）"
            )
            if item.get("reason"):
                lines.append(f"    {item['reason']}")
    lines.append("报告完毕。")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="清理 markdown 评论块重复组")
    parser.add_argument("--dry-run", action="store_true", help="只输出报告（默认）")
    parser.add_argument("--fix", action="store_true", help="实际清理（先备份）")
    parser.add_argument("--users", nargs="*", default=None, help="只处理指定目录名")
    parser.add_argument("--output-dir", default=None, help="数据目录（默认读配置）")
    parser.add_argument("--report", default=None, help="报告输出路径")
    args = parser.parse_args(argv)
    try:
        import weibo

        config = weibo.get_config()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("读取配置失败: %s", exc)
        return 1
    output_dir = args.output_dir or file_lock.resolve_output_dir(
        PROJECT_DIR, config.get("output_directory")
    )
    summary = dedup_output_dir(
        output_dir, dry_run=not args.fix, users=args.users
    )
    text = render_summary(summary)
    print(text)
    if args.report:
        _write_text_atomic(args.report, text)
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
