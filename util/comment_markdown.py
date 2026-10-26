"""评论块的 Markdown 渲染，以及在既有微博块内插入/替换评论块。

评论块格式（2026-10-24 用户最终确认）::

    #### 评论

    <!-- comments: 5000000000000001 total=10344 saved=34 -->

    > **用户 A** · 2024-05-03 00:15:39
    > 评论正文第一行
    > 第二行 [表情]
    >
    > > **用户 B** · 2024-05-03 05:31:04 · 回复 用户 A
    > > 回复正文。
    >
    > **用户 C** · 2024-05-03 00:21:12
    > 另一条一级评论。

    <!-- /comments -->

一级评论用引用块（``>``），二级回复用嵌套引用（``> >``）；一级/二级均按
时间升序。本模块只依赖标准库；评论数据由 :mod:`util.comment_fetch` 产出。
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime

WEIBO_MARKER_RE = re.compile(r"<!--\s*weibo_id:\s*(\d+)\s*-->")
COMMENT_START_RE = re.compile(r"<!--\s*comments:\s*(\d+)(\s[^>]*?)?\s*-->")
COMMENT_END = "<!-- /comments -->"
COMMENT_TITLE = "#### 评论"
_HEADING_RE = re.compile(r"^### ", re.M)


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
    return "".join(parts)


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
    return [f"{prefix}![评论图片]({image_dir}/{pic_file})"]


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


def _next_backup_path(path: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = f"{path}.bak.{stamp}"
    index = 1
    while os.path.exists(candidate):
        candidate = f"{path}.bak.{stamp}_{index}"
        index += 1
    return candidate


def update_markdown_file(
    path: str,
    weibo_id,
    block: str,
    backup: bool = True,
    dry_run: bool = False,
    backup_state: set | None = None,
) -> bool:
    """读取 md、插入/替换评论块并原子写回；返回是否有改动。

    ``backup_state``：同一次运行内共享的集合。文件已在集合里时不再重复备份，
    避免同一个 md 文件里多条微博各生成一份 ``.bak``。
    """
    with open(path, "r", encoding="utf-8") as handle:
        original = handle.read()
    updated, changed = upsert_comment_block(original, weibo_id, block)
    if not changed:
        return False
    if dry_run:
        return True
    if backup and (backup_state is None or path not in backup_state):
        shutil.copy2(path, _next_backup_path(path))
        if backup_state is not None:
            backup_state.add(path)

    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp_path = tempfile.mkstemp(
        prefix=os.path.basename(path) + ".", suffix=".tmp", dir=directory
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as writer:
            writer.write(updated)
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    return True
