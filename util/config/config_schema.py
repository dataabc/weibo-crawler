"""配置模型与校验。

在保持默认行为（缺键用代码默认值、不抛异常）的前提下，给关键配置键建立
「类型 + 取值范围」校验，配置漂移（手改错值、旧键遗留）在启动日志里可见，
并作为后续 dataclass 模型化的基础。

只校验已知键；未知键忽略（兼容旧配置）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ConfigRule:
    """一条配置约束：类型与取值范围。"""

    types: tuple
    default: object
    choices: tuple | None = None
    minimum: int | float | None = None


# 关键配置键（覆盖爬取/写入/报告/评论/隔离区；不是全部 100 个键）
RULES: dict[str, ConfigRule] = {
    "timestamp_prefix_format": ConfigRule(
        (str,), "%Y-%m-%d_%H-%M-%S",
        choices=("%Y-%m-%d_%H-%M-%S", "%Y%m%d_%H%M%S"),
    ),
    "timestamp_prefix_include_id": ConfigRule((int, bool), 0, choices=(0, 1, False, True)),
    "since_date": ConfigRule((str, int), "1970-01-01"),
    "write_mode": ConfigRule((list,), []),
    "only_crawl_original": ConfigRule((int, bool), 0, choices=(0, 1, False, True)),
    "remove_html_tag": ConfigRule((int, bool), 1, choices=(0, 1, False, True)),
    "markdown_split_by": ConfigRule(
        (str,), "day", choices=("day", "day_by_month", "month", "year", "all")
    ),
    "markdown_sort_order": ConfigRule(
        (str,), "new_to_old", choices=("old_to_new", "new_to_old")
    ),
    "markdown_image_order": ConfigRule(
        (str,),
        "live_photo_first",
        choices=("live_photo_first", "image_first_interleaved"),
    ),
    "markdown_preview": ConfigRule((dict,), {}),
    "markdown_file_dir": ConfigRule((dict,), {}),
    "markdown_write_backup": ConfigRule((int, bool), 1, choices=(0, 1, False, True)),
    "markdown_show_location_link": ConfigRule(
        (int, bool), 0, choices=(0, 1, False, True)
    ),
    "comment_refresh_scope": ConfigRule(
        (str,), "all", choices=("all", "recent", "off")
    ),
    "comment_refresh_limit": ConfigRule((int,), 300, minimum=0),
    "comment_markdown_backup": ConfigRule(
        (int, bool), 0, choices=(0, 1, False, True)
    ),
    "weibo_emoji_dir": ConfigRule((str,), "weibo_data/emoji"),
    "comment_emoji_inline_size": ConfigRule((int,), 0, minimum=0),
    "not_downloaded_record": ConfigRule((int, bool), 0, choices=(0, 1, False, True)),
    "invisible_confirm_rounds": ConfigRule((int,), 2, minimum=1),
    "invisible_archive_enabled": ConfigRule((int, bool), 1, choices=(0, 1, False, True)),
    "invisible_archive_dir": ConfigRule((str,), "已不可见"),
    "weibo_archive_check": ConfigRule((int, bool), 0, choices=(0, 1, False, True)),
    "weibo_deleted_report": ConfigRule((int, bool), 1, choices=(0, 1, False, True)),
    "weibo_crawl_summary": ConfigRule((int, bool), 0, choices=(0, 1, False, True)),
    "weibo_media_check": ConfigRule((int, bool), 0, choices=(0, 1, False, True)),
    "weibo_comment_summary": ConfigRule((int, bool), 0, choices=(0, 1, False, True)),
    "weibo_change_report": ConfigRule((int, bool), 1, choices=(0, 1, False, True)),
    "user_id_list": ConfigRule((list, str), []),  # str = 用户列表文件名
    "since_date_source": ConfigRule((str,), ""),
}


def validate_config(config: dict) -> list[str]:
    """校验已知配置键；返回警告列表（空 = 通过）。不抛异常。"""
    issues: list[str] = []
    if not isinstance(config, dict):
        return ["配置不是字典"]
    for key, rule in RULES.items():
        if key not in config:
            continue
        value = config.get(key)
        if rule.types and not isinstance(value, rule.types):
            issues.append(
                f"{key} 类型错误（期望 {rule.types}，实际 {type(value).__name__}）"
            )
            continue
        if rule.choices is not None and value not in rule.choices:
            issues.append(f"{key} 取值 {value!r} 不在允许范围 {rule.choices}")
        if rule.minimum is not None:
            try:
                if float(value) < float(rule.minimum):
                    issues.append(f"{key} 取值 {value!r} 小于最小值 {rule.minimum}")
            except (TypeError, ValueError):
                issues.append(f"{key} 不是数字")
    write_mode = config.get("write_mode")
    if isinstance(write_mode, list):
        # mysql/mongo/mongodb/post 是合法模式（未验证），不算废弃模式
        supported = {
            "csv", "json", "sqlite", "markdown", "mysql", "mongo", "mongodb", "post",
        }
        unknown = [mode for mode in write_mode if mode not in supported]
        if unknown:
            issues.append(
                f"write_mode 含未知模式: {unknown}（允许 {sorted(supported)}）"
            )
    return issues


def apply_defaults(config: dict) -> dict:
    """返回补全已知键默认值的副本（不修改入参；供展示/文档与测试使用）。

    生产启动路径不调用它（apply() 逐键写默认值），保留是因为
    tests/config/test_config_schema.py 用它验证 RULES.default 与运行默认值一致。
    """
    result = dict(config or {})
    for key, rule in RULES.items():
        result.setdefault(key, rule.default)
    return result
