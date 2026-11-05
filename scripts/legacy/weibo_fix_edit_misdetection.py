#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""清理「编辑误判」产物。

本脚本用「平台语义内容」= 正文纯文本 + 媒体文件名（去扩展名）+ 链接 URL 重新判定：
- 语义相同 → 误判：删除该微博的 edited_at 行、把快照移到 `编辑历史/_误判备份/`、
  从状态索引移除版本记录；
- 语义不同 → 真实编辑：保留。

dry-run（默认）只输出报告；--fix 先备份 md（.bak.<YYYYMMDD_HHMMSS>）再修改。

示例：
    python scripts/weibo_fix_edit_misdetection.py
    python scripts/weibo_fix_edit_misdetection.py --fix"""

from __future__ import annotations

import argparse
import json
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

from util.core import file_lock  # noqa: E402

EXCLUDE_DIRS = {"report", "已不可见", "已删除", "编辑历史"}


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


def _signature(text: str):
    """语义签名：正文纯文本 + 媒体文件名（去扩展名）+ 链接 URL。"""
    prose, media, links = [], [], []
    for line in (text or "").split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("<!--") or stripped == "---":
            continue
        match = re.match(
            r"^!?\[(?:网页链接|视频|Live Photo[^\]]*)\]\((https?://[^)]+)\)$",
            stripped,
        )
        if match:
            links.append(match.group(1))
            continue
        match = re.match(r"^!?\[[^\]]*\]\(([^)]+)\)$", stripped)
        if match:
            media.append(os.path.splitext(match.group(1))[0])
            continue
        prose.append(stripped)
    return ("\n".join(prose), tuple(sorted(media)), tuple(sorted(links)))


def _snapshot_body(text: str) -> str:
    match = re.search(r"## 正文\n\n(.*?)(?:\n## 媒体|\Z)", text, re.S)
    body = match.group(1) if match else ""
    media_match = re.search(r"## 媒体\n\n(.*)", text, re.S)
    return (body.strip() + "\n" + (media_match.group(1).strip() if media_match else "")).strip()


def _find_block_body(user_dir: str, weibo_id: str) -> str:
    """在用户目录 md 中定位微博块正文（去掉 edited_at 与评论块）。"""
    pattern = re.compile(r"weibo_id:\s*" + weibo_id + r"\b")
    for name in sorted(os.listdir(user_dir)):
        if name in EXCLUDE_DIRS or name.startswith("."):
            continue
        path = os.path.join(user_dir, name)
        if os.path.isdir(path):
            candidates = [
                os.path.join(path, sub)
                for sub in os.listdir(path)
                if sub.endswith(".md") and not sub.startswith("._")
            ]
        elif name.endswith(".md") and not name.startswith("._"):
            candidates = [path]
        else:
            continue
        for md_path in candidates:
            try:
                text = open(md_path, encoding="utf-8").read()
            except OSError:
                continue
            if not pattern.search(text):
                continue
            match = re.search(
                r"weibo_id:\s*" + weibo_id + r"[^\n]*\n(.*?)(?=\n---\n|\Z)",
                text,
                re.S,
            )
            if not match:
                continue
            body = re.sub(r"<!-- edited_at: [^>]+-->\n?", "", match.group(1))
            comment_idx = body.find("#### 评论")
            if comment_idx >= 0:
                end_idx = body.find("<!-- /comments -->", comment_idx)
                body = body[:comment_idx] + (
                    body[end_idx + len("<!-- /comments -->"):] if end_idx >= 0 else ""
                )
            return body
    return ""


def _remove_edited_at(md_path: str, weibo_id: str, backup: bool) -> bool:
    try:
        text = open(md_path, encoding="utf-8").read()
    except OSError:
        return False
    pattern = re.compile(
        r"(weibo_id:\s*" + weibo_id + r"\s*-->)\n<!-- edited_at: [^>]+-->\n"
    )
    if not pattern.search(text):
        return False
    if backup:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            shutil.copy2(md_path, f"{md_path}.bak.{stamp}")
        except OSError:
            return False
    _write_text_atomic(md_path, pattern.sub(r"\1\n", text, count=1))
    return True


def _load_state(user_dir: str) -> tuple[str, dict | None]:
    for name in os.listdir(user_dir):
        if name.startswith("archive_state_") and name.endswith(".json"):
            path = os.path.join(user_dir, name)
            try:
                with open(path, encoding="utf-8") as handle:
                    return path, json.load(handle)
            except (OSError, ValueError):
                return path, None
    return "", None


def fix_user(user_dir: str, *, dry_run: bool, backup: bool) -> dict:
    result = {
        "user_dir": user_dir,
        "snapshots": 0,
        "misdetected": 0,
        "real_edits": 0,
        "removed_marks": 0,
        "moved_snapshots": 0,
        "errors": 0,
        "details": [],
    }
    snapshots_root = os.path.join(user_dir, "编辑历史")
    if not os.path.isdir(snapshots_root):
        return result
    state_path, state = _load_state(user_dir)
    versions_removed = set()
    for month in sorted(os.listdir(snapshots_root)):
        month_dir = os.path.join(snapshots_root, month)
        if not os.path.isdir(month_dir):
            continue
        for name in sorted(os.listdir(month_dir)):
            if not name.endswith(".md") or name.startswith("._"):
                continue
            match = re.search(r"_(\d+)\.md$", name)
            if not match:
                continue
            weibo_id = match.group(1)
            snap_path = os.path.join(month_dir, name)
            rel_snap = os.path.relpath(snap_path, user_dir)
            try:
                snap_text = open(snap_path, encoding="utf-8").read()
            except OSError:
                result["errors"] += 1
                continue
            result["snapshots"] += 1
            old_text = _snapshot_body(snap_text)
            current_text = _find_block_body(user_dir, weibo_id)
            if _signature(old_text) != _signature(current_text):
                result["real_edits"] += 1
                continue
            result["misdetected"] += 1
            result["details"].append((weibo_id, rel_snap))
            if dry_run:
                continue
            # 删除 edited_at 行
            block_file = _find_md_path(user_dir, weibo_id)
            if block_file:
                if _remove_edited_at(block_file, weibo_id, backup):
                    result["removed_marks"] += 1
            # 快照移到 _误判备份
            backup_dir = os.path.join(
                snapshots_root, "_误判备份", datetime.now().strftime("%Y%m%d")
            )
            try:
                os.makedirs(backup_dir, exist_ok=True)
                shutil.move(snap_path, os.path.join(backup_dir, name))
                result["moved_snapshots"] += 1
            except OSError:
                result["errors"] += 1
            if state and versions_removed is not None:
                for item in state.get("weibos", {}).values():
                    versions = item.get("versions") or []
                    keep = [
                        version
                        for version in versions
                        if version.get("path") != rel_snap
                    ]
                    if len(keep) != len(versions):
                        item["versions"] = keep
                        item["last_edit_at"] = ""
                        versions_removed.add(rel_snap)
    if not dry_run and state_path and state:
        try:
            _write_text_atomic(
                state_path,
                json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            )
        except OSError:
            result["errors"] += 1
    return result


def _find_md_path(user_dir: str, weibo_id: str) -> str:
    """定位包含某微博块的 md 文件路径。"""
    pattern = re.compile(r"weibo_id:\s*" + weibo_id + r"\b")
    for name in sorted(os.listdir(user_dir)):
        if name in EXCLUDE_DIRS or name.startswith("."):
            continue
        path = os.path.join(user_dir, name)
        if os.path.isdir(path):
            for sub in sorted(os.listdir(path)):
                if sub.endswith(".md") and not sub.startswith("._"):
                    full = os.path.join(path, sub)
                    try:
                        if pattern.search(open(full, encoding="utf-8").read()):
                            return full
                    except OSError:
                        continue
        elif name.endswith(".md") and not name.startswith("._"):
            try:
                if pattern.search(open(path, encoding="utf-8").read()):
                    return path
            except OSError:
                continue
    return ""


def scan(output_dir: str, *, dry_run: bool, backup: bool) -> list[dict]:
    results = []
    for name in sorted(os.listdir(output_dir)):
        if name.startswith(".") or name in EXCLUDE_DIRS:
            continue
        user_dir = os.path.join(output_dir, name)
        if not os.path.isdir(user_dir):
            continue
        results.append(fix_user(user_dir, dry_run=dry_run, backup=backup))
    return results


def render_report(results: list[dict], dry_run: bool) -> str:
    lines = [
        "编辑误判清理报告",
        f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"模式: {'dry-run' if dry_run else 'fix'}",
    ]
    total = {"snapshots": 0, "misdetected": 0, "real_edits": 0, "errors": 0,
             "removed_marks": 0, "moved_snapshots": 0}
    for result in results:
        if not result["snapshots"]:
            continue
        lines.append(
            f"{os.path.basename(result['user_dir'])}: 快照 {result['snapshots']}，"
            f"误判 {result['misdetected']}，真实编辑 {result['real_edits']}"
        )
        for key in total:
            total[key] += result.get(key, 0)
    lines.append("=" * 60)
    lines.append(f"快照总数: {total['snapshots']}")
    lines.append(f"误判（将清理）: {total['misdetected']}")
    lines.append(f"真实编辑（保留）: {total['real_edits']}")
    if not dry_run:
        lines.append(f"已移除 edited_at 标记: {total['removed_marks']}")
        lines.append(f"已移动误判快照: {total['moved_snapshots']}")
    lines.append(f"失败: {total['errors']}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="清理编辑误判产物")
    parser.add_argument("--dry-run", action="store_true", help="只报告（默认）")
    parser.add_argument("--fix", action="store_true", help="实际清理（先备份 md）")
    parser.add_argument("--output-dir", default=None, help="数据目录（默认读配置）")
    parser.add_argument("--report", default=None, help="报告输出路径")
    args = parser.parse_args(argv)
    try:
        import weibo

        config = weibo.get_config()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"读取配置失败: {exc}")
        return 1
    output_dir = args.output_dir or file_lock.resolve_output_dir(
        PROJECT_DIR, config.get("output_directory")
    )
    results = scan(output_dir, dry_run=not args.fix, backup=True)
    text = render_report(results, not args.fix)
    print(text)
    if args.report:
        _write_text_atomic(args.report, text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
