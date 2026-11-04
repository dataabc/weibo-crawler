"""评论块的 Markdown 渲染，以及在既有微博块内插入/替换评论块。

评论块格式::

    #### 评论

    <!-- comments: 5000000000000001 total=10344 saved=34 -->

    > **用户 A** · YYYY-MM-DD HH:MM:SS
    > 评论正文第一行
    > 第二行 [表情]
    >
    > > **用户 B** · YYYY-MM-DD HH:MM:SS · 回复 用户 A
    > > 回复正文。
    >
    > **用户 C** · YYYY-MM-DD HH:MM:SS
    > 另一条一级评论。

    <!-- /comments -->

一级评论用引用块（``>``），二级回复用嵌套引用（``> >``）；一级/二级均按
时间升序。除标准库外仅依赖 :mod:`util.core.atomic_io`（写回）；评论数据由
:mod:`util.comment.comment_fetch` 产出。
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime

from util.core import atomic_io

WEIBO_MARKER_RE = re.compile(r"<!--\s*weibo_id:\s*(\d+)\s*-->")
COMMENT_START_RE = re.compile(r"<!--\s*comments:\s*(\d+)(\s[^>]*?)?\s*-->")
COMMENT_END = "<!-- /comments -->"
COMMENT_TITLE = "#### 评论"
# 微博块标题只有两类：「### HH:MM:SS」（day/day_by_month）或
# 「### YYYY-MM-DD HH:MM:SS」（month/year/all）；要求整行匹配（行尾锚定），
# 避免正文中行首的 "### "（如 "### 12:00:00 备注"）被当成块边界。
_HEADING_RE = re.compile(
    r"^### (?:\d{2}:\d{2}:\d{2}|\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})\s*$",
    re.M,
)


@dataclass
class RenderOptions:
    """评论块渲染开关。"""

    show_like_count: bool = False
    show_reply_target: bool = True
    image_dir: str = "评论图片"


def _escape_name(name: str) -> str:
    text = str(name or "").strip()
    if not text:
        return "微博用户"
    for char in ("\\", "*", "_", "`", "[", "]"):
        text = text.replace(char, "\\" + char)
    return text.replace("<", "&lt;").replace(">", "&gt;")


def _escape_body_line(line: str) -> str:
    # 引用块内行首的 > 会形成意外嵌套，转义掉
    if line.startswith(">"):
        return "\\" + line
    return line


def _meta_line(comment, options: RenderOptions, is_reply: bool) -> str:
    name = _escape_name(getattr(comment, "user_name", ""))
    created_at = getattr(comment, "created_at", "") or "时间未知"
    parts = [f"**{name}** · {created_at}"]
    if options.show_like_count:
        like_count = int(getattr(comment, "like_count", 0) or 0)
        if like_count > 0:
            parts.append(f" · 赞 {like_count}")
    if is_reply and options.show_reply_target:
        target = str(getattr(comment, "reply_target", "") or "").strip()
        if target:
            parts.append(f" · 回复 {_escape_name(target)}")
    meta = "".join(parts)
    # 把评论在平台的唯一 ID 写成隐藏注释，用于刷新时按 cid 合并、防止旧评论丢失。
    cid = str(getattr(comment, "id", "") or "").strip()
    if cid:
        meta += f" <!-- cid: {cid} -->"
    return meta


def _body_lines(comment, prefix: str) -> list[str]:
    text = str(getattr(comment, "text", "") or "")
    if not text:
        return []
    lines = []
    for line in text.split("\n"):
        lines.append(prefix + _escape_body_line(line) if line else prefix.rstrip())
    return lines


def _pic_lines(comment, prefix: str, options: RenderOptions) -> list[str]:
    pic_file = str(getattr(comment, "pic_file", "") or "").strip()
    if not pic_file:
        return []
    image_dir = str(options.image_dir or "评论图片").strip().strip("/")
    # 与微博正文图片使用同一种展示格式：![img](目录/文件名)
    return [f"{prefix}![img]({image_dir}/{pic_file})"]


def render_comment_block(
    weibo_id,
    roots,
    total_number: int = 0,
    options: RenderOptions | None = None,
) -> str:
    """渲染一条微博的评论块；没有评论时返回空字符串（调用方跳过写入）。"""
    roots = list(roots or [])
    if not roots:
        return ""
    options = options or RenderOptions()
    saved = len(roots) + sum(len(getattr(root, "replies", []) or []) for root in roots)

    lines = [
        COMMENT_TITLE,
        "",
        f"<!-- comments: {weibo_id} total={int(total_number or 0)} saved={saved} -->",
        "",
    ]
    for index, root in enumerate(roots):
        if index:
            lines.append(">")
        lines.append("> " + _meta_line(root, options, is_reply=False))
        lines.extend(_body_lines(root, "> "))
        lines.extend(_pic_lines(root, "> ", options))
        for reply in getattr(root, "replies", []) or []:
            lines.append(">")
            lines.append("> > " + _meta_line(reply, options, is_reply=True))
            lines.extend(_body_lines(reply, "> > "))
            lines.extend(_pic_lines(reply, "> > ", options))
    lines.extend(["", COMMENT_END])
    return "\n".join(lines)


COMMENT_DATE_IN_LINE_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"
)


def _meta_time(line: str) -> str:
    match = COMMENT_DATE_IN_LINE_RE.search(line or "")
    return match.group(1) if match else ""


def sort_comment_block(block: str) -> str:
    """把评论块按时间升序排序（一级评论、二级回复各自排序）。

    只重排已渲染的文本，不改变内容/图片/cid；用于合并旧块后的最终写入。
    """
    text = str(block or "")
    marker_match = COMMENT_START_RE.search(text)
    if not marker_match:
        return text
    end = text.find(COMMENT_END, marker_match.end())
    if end == -1:
        return text
    prefix = text[: marker_match.start()].rstrip("\n")
    marker = text[marker_match.start(): marker_match.end()]
    body = text[marker_match.end(): end]
    lines = body.split("\n")

    # 按一级评论行切分
    top_chunks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.startswith("> **"):
            if current:
                top_chunks.append(current)
            current = [line]
        elif current:
            current.append(line)
    if current:
        top_chunks.append(current)

    parsed = []
    for chunk in top_chunks:
        # 去掉空行
        while chunk and not chunk[0].strip():
            chunk.pop(0)
        if not chunk:
            continue
        root_lines = [chunk[0]]
        rest = chunk[1:]
        reply_chunks: list[list[str]] = []
        reply: list[str] = []
        for line in rest:
            if line.startswith("> > **"):
                if reply:
                    reply_chunks.append(reply)
                reply = [line]
            elif reply:
                reply.append(line)
            elif line.strip() == ">":
                continue
            else:
                root_lines.append(line)
        if reply:
            reply_chunks.append(reply)
        # 去掉块尾多余的分隔符/空行
        while root_lines and root_lines[-1].strip() in ("", ">"):
            root_lines.pop()
        cleaned_replies = []
        for reply_lines in reply_chunks:
            while reply_lines and reply_lines[-1].strip() in ("", ">"):
                reply_lines.pop()
            if reply_lines:
                cleaned_replies.append(reply_lines)
        parsed.append(
            {
                "root": root_lines,
                "root_time": _meta_time(root_lines[0] if root_lines else ""),
                "replies": sorted(
                    cleaned_replies,
                    key=lambda item: _meta_time(item[0] if item else ""),
                ),
            }
        )
    parsed.sort(key=lambda item: item["root_time"])

    out = [prefix, marker, ""]
    for index, item in enumerate(parsed):
        out.extend(item["root"])
        for reply in item["replies"]:
            out.append(">")
            out.extend(reply)
        if index != len(parsed) - 1:
            out.append(">")
    out.extend(["", COMMENT_END])
    return "\n".join(out)


def find_weibo_span(text: str, weibo_id) -> tuple[int, int] | None:
    """定位单条微博块：从微博 ID 标记到下一个 ``### `` 标题（或文件末尾）。"""
    target = str(weibo_id)
    for match in WEIBO_MARKER_RE.finditer(text):
        if match.group(1) != target:
            continue
        next_heading = _HEADING_RE.search(text, match.end())
        end = next_heading.start() if next_heading else len(text)
        return match.start(), end
    return None


def _comment_title_start(text: str, marker_start: int) -> int:
    """若评论 marker 上方紧邻 ``#### 评论`` 标题（中间只有空行），返回标题行起点。"""
    prefix = text[:marker_start]
    trimmed = prefix.rstrip()
    line_start = trimmed.rfind("\n") + 1
    last_line = trimmed[line_start:].strip()
    if last_line == COMMENT_TITLE:
        return line_start
    return marker_start


def find_comment_span(text: str, weibo_id) -> tuple[int, int] | None:
    """在给定文本（通常是微博块）中定位评论块区间（含 ``#### 评论`` 标题）。"""
    target = str(weibo_id)
    for match in COMMENT_START_RE.finditer(text):
        if match.group(1) != target:
            continue
        end = text.find(COMMENT_END, match.end())
        if end == -1:
            return None
        return _comment_title_start(text, match.start()), end + len(COMMENT_END)
    return None


def upsert_comment_block(text: str, weibo_id, block: str) -> tuple[str, bool]:
    """在微博块内插入或替换评论块；返回 ``(新全文, 是否改动)``。"""
    if not block:
        return text, False
    span = find_weibo_span(text, weibo_id)
    if span is None:
        return text, False
    start, end = span
    body = text[start:end]

    comment_span = find_comment_span(body, weibo_id)
    if comment_span is not None:
        c_start, c_end = comment_span
        new_body = (
            body[:c_start].rstrip("\n")
            + "\n\n"
            + block.strip("\n")
            + "\n\n"
            + body[c_end:].lstrip("\n")
        )
    else:
        separator = body.rfind("\n---\n")
        if separator == -1:
            new_body = body.rstrip("\n") + "\n\n" + block.strip("\n") + "\n\n"
        else:
            new_body = (
                body[:separator].rstrip("\n")
                + "\n\n"
                + block.strip("\n")
                + "\n"
                + body[separator:]
            )
    new_text = text[:start] + new_body + text[end:]
    return new_text, new_text != text


UPDATE_REMOVED = "removed"        # 旧评论块已移除（无评论/过滤后为空）


def remove_comment_block(text: str, weibo_id) -> tuple[str, bool]:
    """移除微博块内的评论块；返回 ``(新全文, 是否改动)``。"""
    span = find_weibo_span(text, weibo_id)
    if span is None:
        return text, False
    start, end = span
    body = text[start:end]
    comment_span = find_comment_span(body, weibo_id)
    if comment_span is None:
        return text, False
    c_start, c_end = comment_span
    before = body[:c_start].rstrip("\n")
    after = body[c_end:].lstrip("\n")
    new_body = before
    if after:
        new_body += "\n\n" + after
    else:
        new_body += "\n"
    return text[:start] + new_body + text[end:], True


def remove_comment_block_file(
    path: str,
    weibo_id,
    backup: bool = True,
    dry_run: bool = False,
    backup_state: set | None = None,
) -> tuple[bool, str]:
    """从 md 文件移除指定微博的评论块；返回 ``(是否改动, 状态)``。"""
    with open(path, "r", encoding="utf-8") as handle:
        original = handle.read()
    if find_weibo_span(original, weibo_id) is None:
        return False, UPDATE_MISSING
    updated, changed = remove_comment_block(original, weibo_id)
    if not changed:
        return False, UPDATE_UNCHANGED
    if dry_run:
        return True, UPDATE_REMOVED
    if backup and (backup_state is None or path not in backup_state):
        shutil.copy2(path, _next_backup_path(path))
        if backup_state is not None:
            backup_state.add(path)
    atomic_io.atomic_write_text(path, updated, preserve_mode=True)
    return True, UPDATE_REMOVED


def _next_backup_path(path: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = f"{path}.bak.{stamp}"
    index = 1
    while os.path.exists(candidate):
        candidate = f"{path}.bak.{stamp}_{index}"
        index += 1
    return candidate


UPDATE_WRITTEN = "written"       # 评论块已写入/替换
UPDATE_UNCHANGED = "unchanged"   # 评论块内容未变化
UPDATE_MISSING = "missing"       # 文件里找不到该微博块（可能被人工修改）


def update_markdown_file_status(
    path: str,
    weibo_id,
    block: str,
    backup: bool = True,
    dry_run: bool = False,
    backup_state: set | None = None,
    original_text: str | None = None,
    emoji_store=None,
) -> tuple[bool, str]:
    """读取 md、插入/替换评论块并原子写回；返回 ``(是否有改动, 状态)``。

    状态三态：``UPDATE_WRITTEN`` / ``UPDATE_UNCHANGED`` / ``UPDATE_MISSING``。
    三态可以区分「微博块未找到」与「评论已最新」，避免把未写入误记为已完成。

    ``backup_state``：同一次运行内共享的集合。文件已在集合里时不再重复备份，
    避免同一个 md 文件里多条微博各生成一份 ``.bak``。

    ``emoji_store``：可选的评论表情本地化存储（``util.comment.comment_emoji``）。
    传入时，落盘前会先把评论块里的表情外链下载到本地，并改写为相对 md 的内链。
    """
    if original_text is not None:
        original = original_text
    else:
        with open(path, "r", encoding="utf-8") as handle:
            original = handle.read()
    if find_weibo_span(original, weibo_id) is None:
        return False, UPDATE_MISSING
    if emoji_store is not None and block:
        block = emoji_store.localize_block(block, path)
    updated, changed = upsert_comment_block(original, weibo_id, block)
    if not changed:
        return False, UPDATE_UNCHANGED
    if dry_run:
        return True, UPDATE_WRITTEN
    if backup and (backup_state is None or path not in backup_state):
        shutil.copy2(path, _next_backup_path(path))
        if backup_state is not None:
            backup_state.add(path)

    # 原子写并保留原权限（实现见 util/core/atomic_io.py）。
    atomic_io.atomic_write_text(path, updated, preserve_mode=True)
    return True, UPDATE_WRITTEN

