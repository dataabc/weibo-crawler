"""配置文件迁移工具。

以 ``config.json.example`` 为模板，为旧版 ``config.json`` 补齐新增配置项，
并统一处理历史键改名。模块只依赖标准库和项目已有的 ``json5``，不会触发
``weibo.py`` 的任何网络或日志副作用。

写回配置文件有两种方式：

- :func:`write_config_with_backup`：规范化 JSON 写回（会丢失原文件注释）；
- :func:`write_config_text_with_backup`：文本级最小编辑写回，尽量保留原文件的
  注释与排版；遇到无法安全处理的内容（如整份配置压在一行）时自动回退到
  规范化 JSON 写回，并通过返回值告知调用方是否保留了原格式。
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
    "merge_config_text",
    "write_config_with_backup",
    "write_config_text_with_backup",
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


_STRING_QUOTES = ("'", '"')
_LITERAL_STOP = " \t\r\n,}]\"/"


@dataclass
class _TextEntry:
    """配置文本中一个「键: 值」条目的位置信息。"""

    key: str
    key_start: int
    key_end: int
    quote: str
    value_start: int
    value_end: int
    line_start: int
    entry_end: int
    has_comma: bool


def _skip_ws_comments(text: str, index: int) -> int:
    """跳过空白、``//`` 行注释与 ``/* */`` 块注释。"""
    length = len(text)
    while index < length:
        char = text[index]
        if char in " \t\r\n":
            index += 1
        elif char == "/" and index + 1 < length and text[index + 1] == "/":
            index += 2
            while index < length and text[index] not in "\r\n":
                index += 1
        elif char == "/" and index + 1 < length and text[index + 1] == "*":
            index += 2
            while index + 1 < length and not (
                text[index] == "*" and text[index + 1] == "/"
            ):
                index += 1
            index = min(index + 2, length)
        else:
            break
    return index


def _scan_string_end(text: str, index: int) -> int:
    """从首个引号开始扫描字符串，返回结束引号之后的位置。"""
    quote = text[index]
    index += 1
    length = len(text)
    while index < length:
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == quote:
            return index + 1
        index += 1
    raise ValueError("配置文本中存在未闭合的字符串")


def _scan_value_end(text: str, index: int) -> int:
    """从值的第一个字符开始扫描，返回值的结束位置（不含）。"""
    length = len(text)
    char = text[index]
    if char in "{[":
        depth = 0
        while index < length:
            current = text[index]
            if current in _STRING_QUOTES:
                index = _scan_string_end(text, index)
                continue
            if current == "/" and index + 1 < length and text[index + 1] == "/":
                index += 2
                while index < length and text[index] not in "\r\n":
                    index += 1
                continue
            if current == "/" and index + 1 < length and text[index + 1] == "*":
                index += 2
                while index + 1 < length and not (
                    text[index] == "*" and text[index + 1] == "/"
                ):
                    index += 1
                index = min(index + 2, length)
                continue
            if current in "{[":
                depth += 1
                index += 1
                continue
            if current in "}]":
                depth -= 1
                index += 1
                if depth == 0:
                    return index
                continue
            index += 1
        raise ValueError("配置文本中存在未闭合的括号")
    if char in _STRING_QUOTES:
        return _scan_string_end(text, index)
    while index < length and text[index] not in _LITERAL_STOP:
        index += 1
    return index


def _decode_key_token(raw: str, quote: str) -> str:
    """把配置文本里的键 token 还原为键名。"""
    if quote == '"':
        try:
            return json.loads('"' + raw + '"')
        except ValueError:
            return raw
    return raw


def _parse_object_entries(
    text: str, open_index: int
) -> tuple[list[_TextEntry], int]:
    """解析从 ``open_index`` 处 ``{`` 开始的对象，返回（条目列表, 闭括号位置）。"""
    length = len(text)
    index = open_index + 1
    entries: list[_TextEntry] = []
    while True:
        index = _skip_ws_comments(text, index)
        if index >= length:
            raise ValueError("配置文本中存在未闭合的对象")
        if text[index] == "}":
            return entries, index
        line_start = text.rfind("\n", 0, index) + 1
        key_start = index
        if text[index] in _STRING_QUOTES:
            quote = text[index]
            key_end = _scan_string_end(text, index)
            key = _decode_key_token(text[key_start + 1 : key_end - 1], quote)
        else:
            quote = ""
            while index < length and (text[index].isalnum() or text[index] in "_$"):
                index += 1
            key_end = index
            key = text[key_start:key_end]
        if not key:
            raise ValueError("配置文本中存在无法识别的键")
        index = _skip_ws_comments(text, key_end)
        if index >= length or text[index] != ":":
            raise ValueError("配置文本中的键缺少冒号")
        index = _skip_ws_comments(text, index + 1)
        if index >= length:
            raise ValueError("配置文本中的键缺少值")
        value_start = index
        value_end = _scan_value_end(text, index)
        index = value_end
        comma_index = value_end
        while comma_index < length and text[comma_index] in " \t\r":
            comma_index += 1
        has_comma = comma_index < length and text[comma_index] == ","
        if has_comma:
            comma_index += 1
        entry_end = comma_index
        while entry_end < length:
            char = text[entry_end]
            if char in " \t\r":
                entry_end += 1
            elif char == "\n":
                entry_end += 1
                break
            elif (
                char == "/" and entry_end + 1 < length and text[entry_end + 1] == "/"
            ):
                entry_end += 2
                while entry_end < length and text[entry_end] not in "\r\n":
                    entry_end += 1
            elif (
                char == "/" and entry_end + 1 < length and text[entry_end + 1] == "*"
            ):
                entry_end += 2
                while entry_end + 1 < length and not (
                    text[entry_end] == "*" and text[entry_end + 1] == "/"
                ):
                    entry_end += 1
                entry_end = min(entry_end + 2, length)
            else:
                break
        entries.append(
            _TextEntry(
                key=key,
                key_start=key_start,
                key_end=key_end,
                quote=quote,
                value_start=value_start,
                value_end=value_end,
                line_start=line_start,
                entry_end=entry_end,
                has_comma=has_comma,
            )
        )
        index = entry_end


def _parse_config_text(text: str) -> tuple[list[_TextEntry], int, int]:
    """解析配置文本的根对象，返回（顶层条目, 开括号位置, 闭括号位置）。

    顶层键必须各自独占行首（允许前导缩进），否则视为无法安全保留原格式。
    """
    length = len(text)
    open_index = _skip_ws_comments(text, 0)
    if open_index >= length or text[open_index] != "{":
        raise ValueError("配置文本的根节点必须是对象")
    entries, close_index = _parse_object_entries(text, open_index)
    for entry in entries:
        if text[entry.line_start : entry.key_start].strip():
            raise ValueError("配置文本的顶层键不在行首，无法安全保留原格式")
    return entries, open_index, close_index


def _entry_comment_span(text: str, entry: _TextEntry) -> tuple[int, int]:
    """返回条目行尾注释的（起, 止）位置；没有注释时返回行尾位置两次。"""
    length = len(text)
    index = entry.value_end
    while index < length and text[index] in " \t\r":
        index += 1
    if index < length and text[index] == ",":
        index += 1
        while index < length and text[index] in " \t\r":
            index += 1
    if index + 1 < length and text[index] == "/" and text[index + 1] == "/":
        start = index
        while start > entry.value_end and text[start - 1] in " \t":
            start -= 1
        while index < length and text[index] not in "\r\n":
            index += 1
        return start, index
    if index + 1 < length and text[index] == "/" and text[index + 1] == "*":
        start = index
        while start > entry.value_end and text[start - 1] in " \t":
            start -= 1
        index += 2
        while index + 1 < length and not (
            text[index] == "*" and text[index + 1] == "/"
        ):
            index += 1
        return start, min(index + 2, length)
    return index, index


def _comment_after_value(text: str, entry: _TextEntry) -> str:
    """取出模板条目行尾注释的原文（不含前导逗号与空白）。"""
    start, end = _entry_comment_span(text, entry)
    return text[start:end].strip()


def _key_token(quote: str, key: str) -> str:
    """按原来的引号风格生成键 token。"""
    if not quote:
        return key
    escaped = key.replace("\\", "\\\\").replace(quote, "\\" + quote)
    return f"{quote}{escaped}{quote}"


def _adjust_block_indent(body: str, source_indent: str, target_indent: str) -> str:
    """把模板条目（可能多行）的缩进从模板缩进改为用户配置缩进。"""
    lines = body.split("\n")
    if not lines:
        return body
    adjusted = [lines[0]]
    for line in lines[1:]:
        stripped = line.lstrip(" \t")
        if not stripped:
            adjusted.append("")
            continue
        lead = line[: len(line) - len(stripped)]
        if source_indent and lead.startswith(source_indent):
            adjusted.append(target_indent + lead[len(source_indent) :] + stripped)
        elif not source_indent:
            adjusted.append(target_indent + lead + stripped)
        else:
            adjusted.append(target_indent + stripped)
    return "\n".join(adjusted)


def merge_config_text(
    original_text: str,
    template_text: str,
    result: MigrationResult,
) -> str:
    """在原文上做最小编辑，得到保留注释与排版的新配置文本。

    只处理顶层键的改名与新增；无法安全处理时抛出 ``ValueError``，由调用方
    回退到规范化 JSON 写回。返回值会重新解析并与 ``result.merged`` 严格比对。
    """
    if not isinstance(result, MigrationResult):
        raise TypeError("result 必须是 MigrationResult")
    template_entries, _, _ = _parse_config_text(template_text)
    user_entries, _, _ = _parse_config_text(original_text)
    if not user_entries:
        raise ValueError("用户配置为空对象，无法保留原格式")
    if not template_entries:
        raise ValueError("模板配置为空对象")

    user_indent = original_text[user_entries[0].line_start : user_entries[0].key_start]
    template_indent = template_text[
        template_entries[0].line_start : template_entries[0].key_start
    ]
    template_keys = [entry.key for entry in template_entries]
    user_by_key: dict[str, _TextEntry] = {entry.key: entry for entry in user_entries}
    last_entry = user_entries[-1]
    edits: list[tuple[int, int, str]] = []

    # 1) 顶层键改名：改键名；行尾注释同步为模板中新键的注释（若模板有）。
    for old_key, new_key in result.renamed_keys:
        entry = user_by_key.get(old_key)
        if entry is None or new_key in user_by_key:
            continue
        edits.append((entry.key_start, entry.key_end, _key_token(entry.quote, new_key)))
        if new_key in template_keys:
            template_entry = template_entries[template_keys.index(new_key)]
            template_comment = _comment_after_value(template_text, template_entry)
            if template_comment:
                comment_start, comment_end = _entry_comment_span(original_text, entry)
                edits.append((comment_start, comment_end, " " + template_comment))
        user_by_key[new_key] = entry

    # 2) 顶层新增键：按模板顺序插到「模板中前一个已存在的键」之后。
    inserted_at: dict[int, list[str]] = {}
    inserted_keys: dict[str, int] = {}
    for key in result.added_keys:
        if "." in key:
            continue
        if key not in template_keys:
            raise ValueError(f"模板中缺少新增键：{key}")
        template_entry = template_entries[template_keys.index(key)]
        body = _adjust_block_indent(
            template_text[template_entry.key_start : template_entry.value_end],
            template_indent,
            user_indent,
        )
        comment = _comment_after_value(template_text, template_entry)
        insert_offset = last_entry.entry_end
        template_index = template_keys.index(key)
        for previous_key in reversed(template_keys[:template_index]):
            if previous_key in inserted_keys:
                # 前一个键本身也是新增的：接在它后面，保持模板顺序。
                insert_offset = inserted_keys[previous_key]
                break
            previous_entry = user_by_key.get(previous_key)
            if previous_entry is not None:
                if previous_entry is not last_entry or previous_entry.has_comma:
                    insert_offset = previous_entry.entry_end
                break
        inserted_keys[key] = insert_offset
        if insert_offset == last_entry.entry_end and not last_entry.has_comma:
            edits.append((last_entry.value_end, last_entry.value_end, ","))
        inserted_at.setdefault(insert_offset, []).append(
            user_indent + body + "," + (" " + comment if comment else "") + "\n"
        )

    for offset, blocks in inserted_at.items():
        edits.append((offset, offset, "".join(blocks)))

    # 3) 嵌套新增（父键已存在、只缺子键）暂不处理，交给调用方回退。
    nested_added = [key for key in result.added_keys if "." in key]
    if nested_added:
        raise ValueError(
            "暂不支持在已有对象内部插入新增键：" + ", ".join(nested_added)
        )

    merged_text = original_text
    for start, end, replacement in sorted(
        edits, key=lambda item: (item[0], item[1]), reverse=True
    ):
        merged_text = merged_text[:start] + replacement + merged_text[end:]

    # 4) 校验：合并后的文本必须解析出与迁移结果完全一致的配置。
    try:
        reparsed = _loads_json5(merged_text)
    except Exception as exc:  # json5 与 json 的异常类型并不统一
        raise ValueError(f"合并后的配置文本无法解析：{exc}") from exc
    if reparsed != result.merged:
        raise ValueError("合并后的配置文本与迁移结果不一致")
    return merged_text


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


def _write_text_with_backup(target: str, payload: str) -> str:
    """备份原文件后，用临时文件 + os.replace 原子写入 ``payload``。

    返回新生成的备份路径；原文件不存在时抛出 ``FileNotFoundError``。
    """
    if not os.path.isfile(target):
        raise FileNotFoundError(f"配置文件不存在，无法备份：{target}")

    try:
        original_mode = stat.S_IMODE(os.stat(target).st_mode)
    except OSError:
        original_mode = None

    backup_path = _next_backup_path(target)
    shutil.copy2(target, backup_path)

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


def write_config_with_backup(path: str, config: dict) -> str:
    """备份原配置后，以临时文件 + os.replace 原子写入规范化 JSON。

    返回新生成的备份路径。原文件不存在时抛出 ``FileNotFoundError``。
    该函数会丢失原文件里的注释；需要保留注释时改用
    :func:`write_config_text_with_backup`。
    """
    if not isinstance(config, dict):
        raise TypeError("config 必须是字典")

    target = os.path.abspath(os.fspath(path))
    payload = json.dumps(config, ensure_ascii=False, indent=4) + "\n"
    return _write_text_with_backup(target, payload)


def write_config_text_with_backup(
    path: str,
    result: MigrationResult,
    *,
    original_text: str,
    template_text: str,
) -> tuple[str, bool]:
    """备份原配置后，优先按原格式写回（保留注释与排版）。

    文本级合并无法安全处理时（例如整份配置压在一行、或需要在已有对象
    内部插入新增键），自动回退为规范化 JSON 写回。``original_text`` 与
    ``template_text`` 由调用方读取后传入，避免重复读盘。

    返回（备份路径, 是否保留了原格式）。
    """
    if not isinstance(result, MigrationResult):
        raise TypeError("result 必须是 MigrationResult")

    target = os.path.abspath(os.fspath(path))
    try:
        payload = merge_config_text(original_text, template_text, result)
        preserved = True
    except ValueError:
        payload = json.dumps(result.merged, ensure_ascii=False, indent=4) + "\n"
        preserved = False

    backup_path = _write_text_with_backup(target, payload)
    return backup_path, preserved
