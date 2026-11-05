#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""部署自检。

用途：升级代码后、跑全量抓取前，检查「现场运行目录」是否同步完整：
1. 必需模块是否齐全；
2. 关键模块能否导入（语法/依赖问题）；
3. 配置里新增键是否缺失（缺失会用默认值，仅提示）；未知的新键不阻塞。

用法：
    python scripts/weibo_selfcheck.py
    python scripts/weibo_selfcheck.py --strict   # 配置缺失也算失败

退出码：0 = 通过；1 = 有必需文件缺失或导入失败。"""

from __future__ import annotations

import argparse
import os
import sys

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

REQUIRED_FILES = [
    "weibo.py",
    "__main__.py",
    "const.py",
    "requirements.txt",
    "util/core/archive_state.py",
    "util/core/archive_index.py",
    "util/core/markdown_blocks.py",
    "util/core/file_lock.py",
    "util/core/time_format.py",
    "util/core/csvutil.py",
    "util/core/dateutil.py",
    "util/report/archive_check_core.py",
    "util/report/change_report.py",
    "util/report/reporting.py",
    "util/report/crawl_summary.py",
    "util/media/media_check.py",
    "util/media/media_report.py",
    "util/media/media_download.py",
    "util/media/media_time.py",
    "util/media/not_downloaded.py",
    "util/comment/comment_fetch.py",
    "util/comment/comment_markdown.py",
    "util/comment/comment_refresh.py",
    "util/comment/comment_retry.py",
    "util/comment/comment_backfill.py",
    "util/comment/comment_report.py",
    "util/comment/comment_images.py",
    "util/config/config_migrate.py",
    "util/config/config_paths.py",
    "util/config/config_schema.py",
    "util/http/http_client.py",
    "util/misc/notify.py",
    "util/misc/llm_analyzer.py",
    "scripts/weibo_markdown_reorder.py",
    "scripts/weibo_archive_check_full.py",
    "scripts/weibo_comment_backfill.py",
]

REQUIRED_IMPORTS = [
    "util.core.markdown_blocks",
    "util.core.archive_state",
    "util.report.change_report",
    "util.comment.comment_fetch",
    "util.comment.comment_markdown",
    "util.core.file_lock",
    "util.core.time_format",
]

NEW_CONFIG_KEYS = [
    "markdown_write_backup",
    "edit_snapshot_dir",
    "archive_state_path",
    "comment_refresh_scope",
    "comment_refresh_recent_days",
    "comment_refresh_limit",
    "weibo_change_report",
    "weibo_change_report_dir",
    "invisible_confirm_rounds",
]


def _iter_required_files():
    """要检查的文件清单：显式清单 + 动态扫描 util/ 与 scripts/。"""
    seen = set()
    for rel in REQUIRED_FILES:
        if rel not in seen:
            seen.add(rel)
            yield rel
    for folder in ("util", "scripts"):
        root = os.path.join(PROJECT_DIR, folder)
        if not os.path.isdir(root):
            continue
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = sorted(name for name in dirs if name != "__pycache__")
            for name in sorted(files):
                if name.startswith("._") or not name.endswith(".py"):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, name), PROJECT_DIR)
                if rel not in seen:
                    seen.add(rel)
                    yield rel


def check_files() -> list[str]:
    errors = []
    for rel in _iter_required_files():
        path = os.path.join(PROJECT_DIR, rel)
        if not os.path.isfile(path):
            errors.append(f"缺少文件: {rel}")
    return errors


def check_imports() -> list[str]:
    errors = []
    for name in REQUIRED_IMPORTS:
        try:
            __import__(name)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"导入失败: {name}（{type(exc).__name__}: {exc}）")
    return errors


def check_runtime_consistency(runtime_dir: str) -> tuple[list[str], list[str]]:
    """对比工作区与现场运行目录的 weibo.py / util / scripts 是否一致（md5）。"""
    import hashlib

    notes = []
    errors = []
    runtime_dir = os.path.abspath(runtime_dir)
    if not os.path.isdir(runtime_dir):
        return notes, [f"现场目录不存在: {runtime_dir}"]

    def md5(path: str) -> str:
        h = hashlib.md5()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    checked = 0
    for root_name in ("util", "scripts"):
        src_dir = os.path.join(PROJECT_DIR, root_name)
        for dirpath, _dirs, files in os.walk(src_dir):
            if "__pycache__" in dirpath:
                continue
            for name in sorted(files):
                if not name.endswith(".py") or name.startswith("."):
                    continue
                src = os.path.join(dirpath, name)
                rel = os.path.relpath(src, PROJECT_DIR)
                dst = os.path.join(runtime_dir, rel)
                checked += 1
                if not os.path.isfile(dst):
                    errors.append(f"现场缺少: {rel}")
                elif md5(src) != md5(dst):
                    errors.append(f"版本不一致: {rel}")
    src_main = os.path.join(PROJECT_DIR, "weibo.py")
    dst_main = os.path.join(runtime_dir, "weibo.py")
    checked += 1
    if not os.path.isfile(dst_main):
        errors.append("现场缺少: weibo.py")
    elif md5(src_main) != md5(dst_main):
        errors.append("版本不一致: weibo.py")
    if not errors:
        notes.append(f"工作区与现场一致（对比 {checked} 个文件）")
    return notes, errors


def check_config() -> tuple[list[str], list[str]]:
    """返回 (提示, 错误)。配置不存在时只算提示。"""
    notes = []
    errors = []
    try:
        from util.config import config_paths

        config_path, _example = config_paths.config_files(PROJECT_DIR)
    except Exception:  # noqa: BLE001 兜底：导入失败时退回旧路径
        config_path = os.path.join(PROJECT_DIR, "config", "config.json")
    if not os.path.isfile(config_path):
        notes.append(
            "未找到配置文件（%s）；首次部署需从 config/config.json.example "
            "复制为 config/config.json 并填写" % config_path
        )
        return notes, errors
    try:
        import json5

        with open(config_path, "r", encoding="utf-8") as handle:
            config = json5.load(handle)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"config.json 解析失败: {exc}")
        return notes, errors
    missing = [key for key in NEW_CONFIG_KEYS if key not in config]
    if missing:
        notes.append(
            "以下新增配置键未配置（将使用默认值）: " + "、".join(missing)
        )
    else:
        notes.append("新增配置键齐全")
    return notes, errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="weibo-crawler 部署自检")
    parser.add_argument(
        "--strict", action="store_true", help="配置缺少新增键也算失败"
    )
    parser.add_argument(
        "--compare",
        default=None,
        metavar="RUNTIME_DIR",
        help="对比工作区与现场运行目录的 weibo.py/util/scripts 版本（md5）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    errors = []
    errors.extend(check_files())
    errors.extend(check_imports())
    notes, config_errors = check_config()
    errors.extend(config_errors)
    if args.compare:
        cmp_notes, cmp_errors = check_runtime_consistency(args.compare)
        notes.extend(cmp_notes)
        errors.extend(cmp_errors)
    if args.strict:
        errors.extend(note for note in notes if "未配置" in note)
    print("部署自检")
    print("=" * 60)
    print(f"项目目录: {PROJECT_DIR}")
    if notes:
        for note in notes:
            print(f"[提示] {note}")
    if errors:
        for error in errors:
            print(f"[错误] {error}")
        print("-" * 60)
        print(f"自检失败：{len(errors)} 项")
        return 1
    print("-" * 60)
    print("自检通过：模块齐全、关键模块可导入。")
    print("下一步：python3 weibo.py --check-config && python3 -m unittest discover -s tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
