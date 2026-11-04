#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""配置键核对：对比代码读取的配置键与 config.json.example。

用途：
1. 找出「代码读取但 example 未提供」的键（新用户配置缺失、默认值依赖代码）；
2. 找出「example 提供但代码未读取」的废弃/冗余键；
3. 生成配置键清单，供配置校验使用。

用法：
    python scripts/weibo_config_audit.py
    python scripts/weibo_config_audit.py --json"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from util.config import config_paths  # noqa: E402

CONFIG_GET_RE = re.compile(r'(?<![\w.])config\.get\(\s*["\']([A-Za-z0-9_]+)["\']')
CONFIG_ATTR_RE = re.compile(r'(?<![\w.])config\[\s*["\']([A-Za-z0-9_]+)["\']\s*\]')

# 内部/嵌套键：不要求出现在 example 顶层，或由代码按嵌套结构读取
INTERNAL_KEYS = {
    "_readonly_mode", "since_date_source", "user_id", "db", "llm_config",
    "api_base", "api_key", "model", "max_tokens", "temperature", "timeout",
    "enable_anomaly_detection", "enable_sentiment_analysis", "enable_summary",
}
NESTED_KEYS = {
    "original_image_dir", "retweet_image_dir", "original_video_dir",
    "retweet_video_dir", "original_live_photo_dir", "retweet_live_photo_dir",
    "comment_image_dir", "original_image_folder", "retweet_image_folder",
    "original_video_folder", "retweet_video_folder", "original_live_photo_folder",
    "retweet_live_photo_folder", "comment_image_folder", "inline_image_preview",
    "link_preview", "original_image_preview", "retweet_image_preview",
    "original_video_preview", "retweet_video_preview", "original_live_photo_preview",
    "retweet_live_photo_preview", "host", "port", "password", "charset",
    # 旧脚本（weibo_archive_check_full / weibo_comment_backfill）以变量方式读取的配置键
    "weibo_archive_check_full_dir", "weibo_comment_backfill_dir",
    # anti_ban_config 嵌套对象内的键（example 中位于 anti_ban_config 下）
    "batch_delay", "batch_size", "max_api_errors", "max_session_time",
    "max_weibo_per_session", "random_rest_probability",
    "request_delay_max", "request_delay_min", "rest_time_min",
    # anti_ban_config 的随机请求头列表（util/http/http_client.py 读取）
    "user_agents", "accept_languages", "referer_list",
}


def collect_code_paths(project_dir: str = PROJECT_DIR) -> list[str]:
    """收集需扫描的代码路径：weibo.py + 递归 util/ 与 scripts/。"""
    paths = [os.path.join(project_dir, "weibo.py")]
    for folder in ("util", "scripts"):
        folder_path = os.path.join(project_dir, folder)
        if not os.path.isdir(folder_path):
            continue
        for dirpath, dirs, files in os.walk(folder_path):
            dirs[:] = sorted(name for name in dirs if name != "__pycache__")
            for name in sorted(files):
                if name.startswith("._") or not name.endswith(".py"):
                    continue
                paths.append(os.path.join(dirpath, name))
    return paths


def collect_code_keys(paths: list[str]) -> dict[str, list[str]]:
    """扫描代码，返回 {配置键: [出现位置]}。"""
    result: dict[str, list[str]] = {}
    for path in paths:
        if not os.path.isfile(path):
            continue
        if os.path.basename(path).startswith("."):
            continue
        try:
            text = open(path, "r", encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        rel = os.path.relpath(path, PROJECT_DIR)
        for match in CONFIG_GET_RE.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            result.setdefault(match.group(1), []).append(f"{rel}:{line}")
        for match in CONFIG_ATTR_RE.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            result.setdefault(match.group(1), []).append(f"{rel}:{line}")
    return result


def collect_example_keys(path: str) -> list[str]:
    """读取 config.json.example 的**顶层**键（忽略注释与嵌套对象内的键）。"""
    keys: list[str] = []
    if not os.path.isfile(path):
        return keys
    try:
        text = open(path, "r", encoding="utf-8", errors="replace").read()
    except OSError:
        return keys
    depth = 0
    for raw_line in text.split("\n"):
        line = raw_line
        comment_idx = line.find("//")
        if comment_idx >= 0:
            line = line[:comment_idx]
        stripped = line.strip()
        if depth == 1:
            match = re.match(r'^"([A-Za-z0-9_]+)"\s*:', stripped)
            if match:
                keys.append(match.group(1))
        depth += line.count("{") - line.count("}")
        if depth < 0:
            depth = 0
    return keys


def build_audit(code_keys: dict[str, list[str]], example_keys: list[str]) -> dict:
    example_set = set(example_keys)
    code_set = set(code_keys)
    only = code_set - example_set
    return {
        "code_only": sorted(only),
        "code_only_real": sorted(only - INTERNAL_KEYS - NESTED_KEYS),
        "code_only_internal": sorted(only & INTERNAL_KEYS),
        "code_only_nested": sorted(only & NESTED_KEYS),
        "example_only": sorted(example_set - code_set - NESTED_KEYS - INTERNAL_KEYS),
        "common": sorted(code_set & example_set),
        "code_key_count": len(code_set),
        "example_key_count": len(example_keys),
        "total_locations": sum(len(v) for v in code_keys.values()),
        "code_keys": {key: code_keys[key] for key in sorted(code_keys)},
    }


def render_text(audit: dict) -> str:
    lines = [
        "配置键审计（代码读取 vs config.json.example）",
        f"代码读取键数: {audit['code_key_count']}（出现 {audit['total_locations']} 处）",
        f"example 键数: {audit['example_key_count']}",
        "=" * 60,
        f"仅代码读取（example 缺失）: {len(audit['code_only'])} 个"
        f"（真正需补: {len(audit['code_only_real'])} 个；内部键 {len(audit['code_only_internal'])} 个；嵌套键 {len(audit['code_only_nested'])} 个）",
    ]
    for key in audit["code_only_real"]:
        locations = audit["code_keys"].get(key, [])
        lines.append(f"  [需补] {key}（{', '.join(locations[:3])}）")
    for key in audit["code_only_internal"]:
        lines.append(f"  [内部键，无需 example] {key}")
    for key in audit["code_only_nested"]:
        lines.append(f"  [嵌套读取，example 已有] {key}")
    lines.append("")
    lines.append(f"仅 example 有（代码未读取，废弃/文档键候选）: {len(audit['example_only'])} 个")
    for key in audit["example_only"]:
        lines.append(f"  - {key}")
    lines.append("")
    lines.append("报告完毕。")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="配置键审计")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--report", default=None, help="报告输出路径")
    args = parser.parse_args(argv)

    code_paths = collect_code_paths()
    _config_path, example = config_paths.config_files(PROJECT_DIR)
    audit = build_audit(collect_code_keys(code_paths), collect_example_keys(example))

    if args.json:
        payload = {k: v for k, v in audit.items() if k != "code_keys"}
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    else:
        text = render_text(audit)
    print(text, end="")
    if args.report:
        with open(args.report, "w", encoding="utf-8") as handle:
            handle.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
