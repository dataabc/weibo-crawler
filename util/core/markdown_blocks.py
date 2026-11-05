"""Markdown 块级解析与重组。

把一份微博存档 markdown 解析为「文件头 + 微博块列表」，支持：
- 提取微博 ID（`<!-- weibo_id: N -->`，保持原格式不变）；
- 提取最后编辑时间（独立隐藏注释 `<!-- edited_at: … -->`）；
- 提取评论块（含 `cid` 注释）；
- 无 marker 的人工块原样保留（不参与排序，不丢弃）；
- 按时间重排、原位插入；
- 重排时逐字保留块原文（render 幂等）。

设计原则：
- **只读优先**：解析器本身不写文件；
- **原文优先**：块的 `raw` 逐字保留，重排只改顺序；只有明确要更新某个块时
  （正文/评论合并）才重新生成该块文本；
- **兼容旧格式**：旧评论块没有 cid、旧编辑块（`#### 编辑记录`）都原样保留。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from util.core import time_format
from util.core.time_format import DTFORMAT

# 微博块标题：### HH:MM:SS 或 ### YYYY-MM-DD HH:MM:SS
HEADING_RE = re.compile(
    r"^### (?:\d{2}:\d{2}:\d{2}|\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})\s*$",
    re.M,
)
HEADING_TIME_ONLY_RE = re.compile(r"^### (\d{2}):(\d{2}):(\d{2})\s*$")
HEADING_FULL_RE = re.compile(
    r"^### (\d{4}-\d{2}-\d{2})[ T](\d{2}):(\d{2}):(\d{2})\s*$"
)

# 微博 ID 标记（保持原格式；不带编辑时间）
WEIBO_ID_RE = re.compile(r"<!--\s*weibo_id:\s*(\d+)\s*-->")
# 最后编辑时间（独立隐藏注释）
# 值允许平台时间或「平台未提供（详情接口变动）」等说明文本
EDITED_AT_RE = re.compile(r"<!--\s*edited_at:\s*(.*?)\s*-->")
# 评论块
COMMENT_TITLE = "#### 评论"
COMMENT_END = "<!-- /comments -->"
CID_RE = re.compile(r"<!--\s*cid:\s*(\d+)\s*-->")
# 一级评论首行（`> **昵称** · 时间…`）；回复是 `> > **…`，不匹配
_PRIMARY_META_RE = re.compile(r"^> \*\*.+\*\* · ")
# 分隔线
# 评论块内的可见内容里，行首 > 的分组（一级评论与回复）
COMMENT_GROUP_SPLIT_RE = re.compile(r"\n\s*\n")


@dataclass
class CommentItem:
    """评论块中的一条一级评论（含其下的二级回复原文）。"""

    cid: str | None = None
    raw: str = ""  # 该组原文（不含首尾空行）
    has_replies: bool = False


@dataclass
class MdBlock:
    """一个微博块。"""

    heading: str = ""  # 标题行，如 "### 21:05:40"
    weibo_id: str | None = None
    edited_at: str | None = None  # 最后编辑时间（隐藏注释里的值）
    created_at: str | None = None  # 完整发布时间（YYYY-MM-DDTHH:MM:SS，能推出来时）
    body: str = ""  # 标题行之后、评论块之前的全部内容（含 marker/正文/媒体）
    comments_marker: str = ""  # 评论块标记行原文（含 <!-- comments: … -->）
    comments: list[CommentItem] = field(default_factory=list)
    comments_tail: str = ""  # 评论块结束标记之后的原文（通常是空行 + ---）
    raw: str = ""  # 整个块原文（含标题行），重排时逐字使用
    start: int = 0  # 在原文中的起止位置
    end: int = 0
    sort_dt: datetime | None = None
    sortable: bool = False  # 有 weibo_id 且能解析出日期才参与排序


@dataclass
class MdDocument:
    """一个 markdown 文件。"""

    title: str = ""  # 文件头（H2 标题 + 空行），逐字保留
    blocks: list[MdBlock] = field(default_factory=list)
    trailing: str = ""  # 最后一个块之后的尾部内容（一般只有空行）

    def find(self, weibo_id: str | int) -> MdBlock | None:
        key = str(weibo_id)
        for block in self.blocks:
            if block.weibo_id == key:
                return block
        return None

    def render(self) -> str:
        """按当前顺序渲染回文本；无改动时与原文逐字一致。"""
        parts = [self.title]
        for block in self.blocks:
            parts.append(block.raw)
        parts.append(self.trailing)
        return "".join(parts)


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _parse_heading(heading: str, fallback_date: str | None) -> tuple[str | None, datetime | None]:
    """从块标题解析完整发布时间。

    只接受**规范块标题**（``### HH:MM:SS`` / ``### YYYY-MM-DD HH:MM:SS``，
    日期与时间之间允许 ``T``）；其余格式返回 (None, None)。
    实际时间解析见 ``time_format.parse_heading_datetime``。
    """
    line = heading.strip()
    is_full = bool(HEADING_FULL_RE.match(line))
    if not (is_full or HEADING_TIME_ONLY_RE.match(line)):
        return None, None
    fallback = None
    if fallback_date:
        try:
            fallback = datetime.strptime(fallback_date, "%Y-%m-%d")
        except ValueError:
            fallback = None
    text = line[4:].strip()  # 去掉 "### "
    if is_full:
        text = text.replace("T", " ", 1)  # 统一成空格分隔供 strptime 使用
    dt = time_format.parse_heading_datetime(text, fallback)
    if dt is None:
        return None, None
    return dt.strftime(DTFORMAT), dt


def _is_primary_comment_start(line: str) -> bool:
    """判断一行是否「一级评论的开头」。

    ``comment_markdown`` 渲染的一级评论首行格式固定为
    ``> **昵称** · 时间[ · 赞 N][ <!-- cid: N -->]``；二级回复是 ``> > **…``，
    不会命中。带 cid 时按 cid 判定（精确），旧块无 cid 时按 meta 行格式判定
    （启发式，正文里的 ``>` 开头行已被转义，不会误判）。
    """
    if not line.startswith("> ") or line.startswith("> >"):
        return False
    if CID_RE.search(line):
        return True
    return bool(_PRIMARY_META_RE.match(line))


def _split_primary_groups(text: str) -> list[CommentItem] | None:
    """按「一级评论开头行」切分评论组；没有可识别的边界时返回 None。

    ``comment_markdown`` 渲染的多条一级评论之间是单独一行 ``>``（与评论内
    空行同形），只按空行切分会把多条一级评论并成一组；这里按一级评论首行切分。
    """
    lines = text.split("\n")
    chunks: list[list[str]] = []
    current: list[str] = []
    found_boundary = False
    for line in lines:
        if _is_primary_comment_start(line):
            found_boundary = True
            if current:
                chunks.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        chunks.append(current)
    if not found_boundary:
        # 一个一级评论首行都认不出（人工块/异常格式）：交给空行分组的兼容路径
        return None

    groups: list[CommentItem] = []
    for chunk_lines in chunks:
        # 去掉组尾的「单独一行 >」分隔符（一级评论之间的分隔）
        if len(chunk_lines) > 1 and chunk_lines[-1].strip() == ">":
            chunk_lines = chunk_lines[:-1]
        chunk = "\n".join(chunk_lines).strip("\n")
        if not chunk.strip():
            continue
        cid_match = CID_RE.search(chunk)
        groups.append(
            CommentItem(
                cid=cid_match.group(1) if cid_match else None,
                raw=chunk,
                has_replies=any(line.startswith("> >") for line in chunk.split("\n")),
            )
        )
    return groups or None


def _parse_comments(region: str) -> tuple[str, list[CommentItem]]:
    """解析评论块区域（`#### 评论` 之后到 `<!-- /comments -->` 之前）。

    返回 (评论标记行原文, 评论组列表)。评论组以「一级评论」为单位切分：
    优先按一级评论 meta 行（带 cid 时精确），无法识别时退回按空行切分
    （人工块/异常格式的兼容路径）。
    """
    region = region.strip("\n")
    marker = ""
    text = region
    first_line, sep, rest = region.partition("\n")
    if first_line.strip().startswith("<!--") and "comments:" in first_line:
        marker = first_line.strip()
        text = rest

    groups = _split_primary_groups(text)
    if groups is not None:
        return marker, groups

    groups = []
    for chunk in COMMENT_GROUP_SPLIT_RE.split(text):
        chunk = chunk.strip("\n")
        if not chunk.strip():
            continue
        cid_match = CID_RE.search(chunk)
        has_replies = any(line.startswith("> >") for line in chunk.split("\n"))
        groups.append(
            CommentItem(
                cid=cid_match.group(1) if cid_match else None,
                raw=chunk,
                has_replies=has_replies,
            )
        )
    return marker, groups


def _parse_block(raw: str, fallback_date: str | None) -> MdBlock:
    """解析单个块原文。raw 从标题行开始，到下一个标题行/文件尾之前。"""
    block = MdBlock(raw=raw)
    lines = raw.split("\n")
    block.heading = lines[0] if lines else ""
    block.created_at, block.sort_dt = _parse_heading(block.heading, fallback_date)

    remainder = "\n".join(lines[1:])

    # 评论块：#### 评论 … <!-- /comments -->
    comment_idx = remainder.find(COMMENT_TITLE)
    if comment_idx >= 0:
        before = remainder[:comment_idx]
        after_title = remainder[comment_idx + len(COMMENT_TITLE):]
        end_idx = after_title.find(COMMENT_END)
        if end_idx >= 0:
            comments_region = after_title[:end_idx]
            tail = after_title[end_idx + len(COMMENT_END):]
            marker, groups = _parse_comments(comments_region)
            block.comments_marker = marker
            block.comments = groups
            block.comments_tail = tail
            block.body = before
        else:
            # 只有标题没有结束标记：整段留在 body，避免误删
            block.body = remainder
    else:
        block.body = remainder

    # 提取 ID 与编辑时间（从 body 里找）
    id_match = WEIBO_ID_RE.search(block.body)
    if id_match:
        block.weibo_id = id_match.group(1)
    edited_match = EDITED_AT_RE.search(block.body)
    if edited_match:
        block.edited_at = edited_match.group(1)

    block.sortable = bool(block.weibo_id and block.sort_dt)
    return block


def parse_markdown(text: str, fallback_date: str | None = None) -> MdDocument:
    """解析 markdown 文本。

    ``fallback_date``：当块标题只有 ``HH:MM:SS`` 时，用它补全日期（YYYY-MM-DD，
    一般取自文件名）。
    """
    text = normalize_newlines(text)
    doc = MdDocument()
    matches = list(HEADING_RE.finditer(text))
    if not matches:
        doc.title = text
        return doc

    doc.title = text[: matches[0].start()]
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = _parse_block(text[start:end], fallback_date)
        block.start = start
        block.end = end
        doc.blocks.append(block)
    return doc


def sort_blocks(doc: MdDocument, order: str = "old_to_new") -> None:
    """按发布时间排序；不可排序的块（无 marker/无日期）保持相对位置。

    实现：把可排序块抽出排序，再按原位置回填；不可排序块留在原索引。
    """
    sortable_indices = [i for i, b in enumerate(doc.blocks) if b.sortable]
    sortable = [doc.blocks[i] for i in sortable_indices]
    sortable.sort(key=lambda b: b.sort_dt, reverse=(order != "old_to_new"))
    for idx, block in zip(sortable_indices, sortable):
        doc.blocks[idx] = block


def insert_block(doc: MdDocument, block: MdBlock, order: str = "old_to_new") -> None:
    """按发布时间把新块插入到正确位置（原位插入，不追加到末尾）。"""
    if not block.sortable:
        doc.blocks.append(block)
        return
    insert_at = len(doc.blocks)
    for i, existing in enumerate(doc.blocks):
        if not existing.sortable:
            continue
        if order == "old_to_new":
            if existing.sort_dt > block.sort_dt:
                insert_at = i
                break
        else:
            if existing.sort_dt < block.sort_dt:
                insert_at = i
                break
    doc.blocks.insert(insert_at, block)


def _comment_text_key(item: CommentItem) -> str:
    """评论内容键：去掉 cid 注释后的文本（用于旧块无 cid 的场景）。"""
    text = CID_RE.sub("", item.raw or "")
    return "\n".join(line.strip() for line in text.split("\n") if line.strip())


def merge_comment_groups(
    existing: list[CommentItem], incoming: list[CommentItem]
) -> list[CommentItem]:
    """合并评论组：保留旧评论，新增/更新新评论。

    匹配优先级：
    1. 新旧都有 cid → 按 cid 匹配（新内容覆盖旧内容）；
    2. 旧组无 cid、新组有 cid（旧块未升级）→ 按「去掉 cid 后的文本」匹配，
       用新组替换旧组（补上 cid，不产生重复）；
    3. 其它情况按文本兜底，匹配不上才追加。

    同时顺带清理 existing 里已有的重复组（「无 cid 旧组 + 带 cid 新组」），
    保证重复执行幂等。
    """
    # 1) 先清理已有重复：同 cid 或同文本只保留最后一条（优先带 cid 的）
    deduped: list[CommentItem] = []
    cid_seen: dict[str, int] = {}
    text_seen: dict[str, int] = {}
    for item in existing:
        text_key = _comment_text_key(item)
        if item.cid and item.cid in cid_seen:
            deduped[cid_seen[item.cid]] = item
            continue
        if text_key and text_key in text_seen:
            idx = text_seen[text_key]
            # 已有组无 cid、当前组带 cid → 用带 cid 的覆盖
            if item.cid and not deduped[idx].cid:
                deduped[idx] = item
                cid_seen[item.cid] = idx
                text_seen[text_key] = idx
            continue
        deduped.append(item)
        idx = len(deduped) - 1
        if item.cid:
            cid_seen[item.cid] = idx
        if text_key:
            text_seen[text_key] = idx
    result = deduped
    cid_index = {item.cid: i for i, item in enumerate(result) if item.cid}
    text_index = {
        _comment_text_key(item): i
        for i, item in enumerate(result)
        if not item.cid
    }

    for item in incoming:
        matched = None
        if item.cid and item.cid in cid_index:
            matched = cid_index[item.cid]
        else:
            text_key = _comment_text_key(item)
            if text_key and text_key in text_index:
                matched = text_index[text_key]
        if matched is not None:
            old = result[matched]
            result[matched] = item
            if old.cid and cid_index.get(old.cid) == matched:
                cid_index.pop(old.cid, None)
            if item.cid:
                cid_index[item.cid] = matched
            text_key = _comment_text_key(item)
            if text_key:
                text_index[text_key] = matched
        else:
            result.append(item)
            idx = len(result) - 1
            if item.cid:
                cid_index[item.cid] = idx
            text_key = _comment_text_key(item)
            if text_key:
                text_index[text_key] = idx
    return result


def render_comments_block(marker: str, groups: list[CommentItem]) -> str:
    """把评论组渲染回评论块文本（标题 + 标记 + 组 + 结束标记）。"""
    parts = [COMMENT_TITLE, ""]
    if marker:
        parts.append(marker)
        parts.append("")
    for i, group in enumerate(groups):
        if i:
            parts.append("")
        parts.append(group.raw)
    parts.append("")
    parts.append(COMMENT_END)
    return "\n".join(parts)


def rebuild_block_text(block: MdBlock) -> str:
    """根据当前字段重新生成块文本（用于正文/评论更新后）。

    注意：只在明确更新块内容时调用；普通重排请直接用 ``block.raw``。
    """
    parts = [block.heading, block.body]
    if block.comments:
        parts.append(render_comments_block(block.comments_marker, block.comments))
        parts.append(block.comments_tail)
    else:
        parts.append("")
    return "".join(parts)


# 公共别名：供主爬生成新块后解析使用
parse_block = _parse_block


def parse_comments_block(text: str) -> tuple[str, list[CommentItem]]:
    """解析一段「评论块文本」（``#### 评论`` 开头、含 ``<!-- /comments -->``）。

    返回 ``(评论标记行, 评论组列表)``；用于把新渲染的评论块与旧块合并。
    """
    body = normalize_newlines(text or "")
    index = body.find(COMMENT_TITLE)
    if index >= 0:
        body = body[index + len(COMMENT_TITLE):]
    end = body.find(COMMENT_END)
    if end >= 0:
        body = body[:end]
    return _parse_comments(body)
