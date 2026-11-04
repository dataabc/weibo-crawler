#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""配置诊断：schema 校验 + example 差异，输出一份可读诊断报告。

用法（需要 json5 读取 config.json）：
    python scripts/weibo_config_diagnostics.py
    python scripts/weibo_config_diagnostics.py --config config.json --report report.txt

输出：
1. 配置校验问题（类型/取值范围，来自 util/config_schema）；
2. 配置键差异（代码读取 vs example，来自 scripts/weibo_config_audit）；
3. 目标 config 相对 example 的缺失键/多余键；
4. 结论（可上线 / 需修正）。
"""

from __future__ import annotations

import argparse
import os
import sys

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, os.path.join(PROJECT_DIR, "scripts"))

from util.config import config_paths, config_schema  # noqa: E402

from weibo_config_audit import (  # noqa: E402
    INTERNAL_KEYS,
    NESTED_KEYS,
    build_audit,
    collect_code_keys,
    collect_code_paths,
    collect_example_keys,
)


def _load_config(path: str) -> dict:
    """读取配置（json5，兼容纯 JSON；失败时抛出可读异常）。"""
    from util.config import config_migrate

    return config_migrate.load_config_file(path)


def build_diagnostics(config: dict, example_keys: list[str], code_audit: dict) -> str:
    validation = config_schema.validate_config(config)
    config_keys = {key for key in config if not str(key).startswith("_")}
    example_set = set(example_keys)
    code_keys = set(code_audit.get("code_keys", {}))
    # 目标键 = 代码读取 且 example 提供（真正的配置面）；旧键/内部键不制造噪音
    target_keys = code_keys & example_set
    missing_in_config = sorted(target_keys - config_keys)
    # 内部键/嵌套键/旧脚本变量读取键不算「多余」
    extra_in_config = sorted(
        config_keys - target_keys - NESTED_KEYS - INTERNAL_KEYS
    )
    lines = [
        "配置诊断报告",
        "=" * 60,
        f"配置校验问题: {len(validation)} 个",
    ]
    if validation:
        for issue in validation:
            lines.append(f"  - {issue}")
    else:
        lines.append("  （无，配置取值合法）")
    lines.append("")
    lines.append("配置键差异（代码读取 vs config.json.example）:")
    lines.append(
        f"  代码读取键: {code_audit.get('code_key_count', 0)} 个；"
        f"example 键: {code_audit.get('example_key_count', 0)} 个；"
        f"真正缺失: {len(code_audit.get('code_only_real', []))} 个"
    )
    for key in code_audit.get("code_only_real", []):
        lines.append(f"  - 代码读取但 example 缺: {key}")
    lines.append("")
    lines.append(
        f"现场 config 相对「代码读取键」的差异: 缺 {len(missing_in_config)} 个（将用代码默认值），"
        f"多 {len(extra_in_config)} 个（代码未读取，可能是旧键/自定义键）"
    )
    if missing_in_config:
        lines.append("  缺失键（将使用代码默认值；建议补进 config.json）:")
        lines.append("    " + ", ".join(missing_in_config[:20]))
    if extra_in_config:
        lines.append("  多余键（example 没有；可能是旧键或自定义键）:")
        lines.append("    " + ", ".join(extra_in_config[:20]))
    lines.append("")
    conclusion = []
    if validation:
        conclusion.append("存在配置取值问题，建议修正后再跑")
    if code_audit.get("code_only_real"):
        conclusion.append("example 缺少代码读取的键")
    lines.append("结论: " + ("；".join(conclusion) if conclusion else "配置健康，可正常运行"))
    lines.append("报告完毕。")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="配置诊断")
    default_config, default_example = config_paths.config_files(PROJECT_DIR)
    parser.add_argument(
        "--config",
        default=default_config,
        help="配置文件路径（默认 config/config.json 或 config.json）",
    )
    parser.add_argument(
        "--example",
        default=default_example,
        help="模板配置路径",
    )
    parser.add_argument("--report", default=None, help="诊断报告输出路径")
    args = parser.parse_args(argv)

    code_paths = collect_code_paths()
    example_keys = collect_example_keys(args.example)
    audit = build_audit(collect_code_keys(code_paths), example_keys)

    try:
        config = _load_config(args.config)
    except Exception as exc:  # noqa: BLE001
        print(f"读取配置失败: {exc}")
        return 1
    if not isinstance(config, dict):
        print("配置不是对象（json5 解析结果不是 dict）")
        return 1

    text = build_diagnostics(config, example_keys, audit)
    print(text, end="")
    if args.report:
        with open(args.report, "w", encoding="utf-8") as handle:
            handle.write(text)
    return 1 if config_schema.validate_config(config) else 0


if __name__ == "__main__":
    sys.exit(main())
