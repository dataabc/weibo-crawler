"""配置文件迁移工具。

以 ``config.json.example`` 为模板，为旧版 ``config.json`` 补齐新增配置项，
并统一处理历史键改名。模块只依赖标准库和项目已有的 ``json5``，不会触发
``weibo.py`` 的任何网络或日志副作用。
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass, field
from datetime import datetime

try:
    import json5
except ImportError:  # 供未安装 json5 的精简环境降级使用
    json5 = None


__all__ = [
    "DEFAULT_RENAME_MAP",
    "MigrationResult",
    "load_config_file",
    "migrate_config",
    "format_summary",
    "write_config_with_backup",
]


# 旧配置键 -> 新配置键，供调用方显式传入 migrate_config。
DEFAULT_RENAME_MAP = {
    "filter": "only_crawl_original",
    "result_dir_name": "user_id_as_folder_name",
    # 2026-10-24 功能改名：旧配置键自动迁移到新键
    "missing_weibo_report": "weibo_archive_check",
    # 上一版功能名对应的配置键，同样迁移到新键
    "weibo_delete_save_check": "weibo_archive_check",
    # 已删除或隐藏目录改名后的旧键
    "deleted_weibo_dirs": "invisible_weibo_dirs",
    "missing_weibo_report_exclude_dirs": "invisible_weibo_dirs",
}


@dataclass
class MigrationResult:
    """一次配置迁移的结果。"""

    merged: dict
    added_keys: list[str] = field(default_factory=list)
    renamed_keys: list[tuple[str, str]] = field(default_factory=list)
    unknown_keys: list[str] = field(default_factory=list)


def _strip_json5_comments(text: str) -> str:
    """移除字符串之外的 // 与 /* */ 注释，供无 json5 时降级解析。"""
    result: list[str] = []
    index = 0
    in_string = False
    quote = ""
    escaped = False
    length = len(text)

    while index < length:
        char = text[index]

        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
            index += 1
            continue

        if char in ("'", '"'):
            in_string = True
            quote = char
            result.append(char)
            index += 1
            continue

        if char == "/" and index + 1 < length:
            next_char = text[index + 1]
            if next_char == "/":
                index += 2
                while index < length and text[index] not in "\r\n":
                    index += 1
                continue
            if next_char == "*":
                index += 2
                while index < length:
                    if (
                        text[index] == "*"
                        and index + 1 < length
                        and text[index + 1] == "/"
                    ):
                        index += 2
                        break
                    # 保留换行，方便解析错误信息定位
                    if text[index] in "\r\n":
                        result.append(text[index])
                    index += 1
                continue

        result.append(char)
        index += 1

    return "".join(result)


def _strip_trailing_commas(text: str) -> str:
    """移除对象或数组末尾的多余逗号，供无 json5 时降级解析。"""
    result: list[str] = []
    index = 0
    in_string = False
    quote = ""
    escaped = False
    length = len(text)

    while index < length:
        char = text[index]

        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
            index += 1
            continue

        if char in ("'", '"'):
            in_string = True
            quote = char
            result.append(char)
            index += 1
            continue

        if char == ",":
            lookahead = index + 1
            while lookahead < length and text[lookahead].isspace():
                lookahead += 1
            if lookahead < length and text[lookahead] in "}]":
                index += 1
                continue

        result.append(char)
        index += 1

    return "".join(result)


def _fallback_json5_loads(text: str) -> dict:
    """在缺少 json5 时解析常见 JSON5 写法（注释和尾随逗号）。"""
    cleaned = _strip_trailing_commas(_strip_json5_comments(text))
    return json.loads(cleaned)


def _loads_json5(text: str):
    """优先使用 json5；环境缺失时使用内置降级解析。"""
    if json5 is not None:
        return json5.loads(text)
    return _fallback_json5_loads(text)


def load_config_file(path: str) -> dict:
    """读取配置文件，支持 JSON5，解析失败时退回标准 JSON。

    文件不存在或无法解析时抛出 ``ValueError``，消息中会带上原始路径。
    """
    path_str = os.fspath(path)
    try:
        with open(path_str, "r", encoding="utf-8-sig") as config_file:
            content = config_file.read()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"无法读取配置文件 {path_str}: {exc}") from exc

    errors: list[str] = []
    for parser_name, parser in (("JSON5", _loads_json5), ("JSON", json.loads)):
        try:
            config = parser(content)
        except Exception as exc:  # json5 与 json 的异常类型并不统一
            errors.append(f"{parser_name}: {exc}")
            continue
        if not isinstance(config, dict):
            raise ValueError(f"配置文件 {path_str} 的根节点必须是对象")
        return config

    detail = "；".join(errors)
    raise ValueError(f"配置文件 {path_str} 解析失败（{detail}）")


def _merge_missing(
    user_config: dict,
    template: dict,
    prefix: str,
    added_keys: list[str],
) -> dict:
    """按模板递归补齐缺失键，同时保留用户的额外键。"""
    merged: dict = {}

    # 先按模板顺序写入，保证最终配置的键顺序稳定。
    for key, template_value in template.items():
        current_path = f"{prefix}.{key}" if prefix else key
        if key not in user_config:
            merged[key] = copy.deepcopy(template_value)
            # 整块字典新增时只记录父路径。
            added_keys.append(current_path)
            continue

        user_value = user_config[key]
        if isinstance(user_value, dict) and isinstance(template_value, dict):
            merged[key] = _merge_missing(
                user_value,
                template_value,
                current_path,
                added_keys,
            )
        else:
            # 用户已有的标量、列表、类型不一致的值一律优先。
            merged[key] = copy.deepcopy(user_value)

    # 模板中没有的嵌套键也保留，追加在模板键之后。
    for key, user_value in user_config.items():
        if key not in template:
            merged[key] = copy.deepcopy(user_value)

    return merged


def migrate_config(
    user_config: dict,
    template: dict,
    rename_map: dict[str, str] | None = None,
) -> MigrationResult:
    """按模板迁移用户配置，返回新对象，不修改任何入参。

    ``rename_map`` 只处理顶层键：旧键存在时，若新键已存在则保留新键并
    删除旧键，否则把旧键的值搬到新键。仅有实际搬迁时才会记入
    ``renamed_keys``。
    """
    if not isinstance(user_config, dict) or not isinstance(template, dict):
        raise TypeError("user_config 和 template 必须是字典")

    working = copy.deepcopy(user_config)
    renamed_keys: list[tuple[str, str]] = []

    for old_key, new_key in (rename_map or {}).items():
        if old_key == new_key or old_key not in working:
            continue
        if new_key in working:
            # 新键优先，旧键只做删除，不再重复写入。
            del working[old_key]
            continue
        working[new_key] = working.pop(old_key)
        renamed_keys.append((old_key, new_key))

    added_keys: list[str] = []
    merged = _merge_missing(working, template, "", added_keys)
    unknown_keys = [key for key in working if key not in template]

    return MigrationResult(
        merged=merged,
        added_keys=added_keys,
        renamed_keys=renamed_keys,
        unknown_keys=unknown_keys,
    )


def format_summary(result: MigrationResult) -> str:
    """把迁移结果格式化为中文多行摘要。"""
    if not result.added_keys and not result.renamed_keys and not result.unknown_keys:
        return "配置已是最新，无需迁移"

    lines: list[str] = []
    if result.added_keys:
        lines.append(
            f"新增配置 {len(result.added_keys)} 项："
            + ", ".join(result.added_keys)
        )
    if result.renamed_keys:
        renamed = ", ".join(
            f"{old_key} → {new_key}" for old_key, new_key in result.renamed_keys
        )
        lines.append(f"旧键改名 {len(result.renamed_keys)} 项：{renamed}")
    if result.unknown_keys:
        lines.append(
            f"未知键 {len(result.unknown_keys)} 项（已保留）："
            + ", ".join(result.unknown_keys)
        )

    return "\n".join(lines)


def _next_backup_path(target: str) -> str:
    """生成不冲突的备份路径，同一秒内重复调用时追加序号。"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base_path = f"{target}.bak.{stamp}"
    if not os.path.exists(base_path):
        return base_path

    index = 1
    while os.path.exists(f"{base_path}.{index}"):
        index += 1
    return f"{base_path}.{index}"


def write_config_with_backup(path: str, config: dict) -> str:
    """备份原配置后，以临时文件 + os.replace 原子写入规范化 JSON。

    返回新生成的备份路径。原文件不存在时抛出 ``FileNotFoundError``。
    """
    if not isinstance(config, dict):
        raise TypeError("config 必须是字典")

    path_str = os.fspath(path)
    target = os.path.abspath(path_str)
    if not os.path.isfile(target):
        raise FileNotFoundError(f"配置文件不存在，无法备份：{path_str}")

    try:
        original_mode = stat.S_IMODE(os.stat(target).st_mode)
    except OSError:
        original_mode = None

    backup_path = _next_backup_path(target)
    shutil.copy2(target, backup_path)

    payload = json.dumps(config, ensure_ascii=False, indent=4) + "\n"
    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{os.path.basename(target)}.",
            suffix=".tmp",
            dir=os.path.dirname(target),
            delete=False,
        ) as temp_file:
            temp_path = temp_file.name
            temp_file.write(payload)
            temp_file.flush()
            os.fsync(temp_file.fileno())

        os.replace(temp_path, target)
        temp_path = None

        if original_mode is not None:
            try:
                os.chmod(target, original_mode)
            except OSError:
                # 权限保留是尽力而为，失败不应影响配置已经写入的事实。
                pass
    finally:
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    return backup_path
