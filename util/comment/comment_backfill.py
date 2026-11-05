"""评论块合并与回填。

把新渲染的评论块与 markdown 里已有评论块合并（按 cid 优先、文本兜底），
避免「整块替换」在刷新返回变少（分页上限/风控/白名单变化）时丢失已存档评论。
"""

from __future__ import annotations

import logging
import os
import re

from util.core import errors
from util.core import markdown_blocks

logger = logging.getLogger("weibo")


def merge_comment_block(
    md_path: str, weibo_id: str, new_block_text: str, *, original_text: str | None = None
) -> str:
    """合并新旧评论块并返回渲染结果；任何异常都回退为新块（不丢数据）。

    ``original_text``：调用方已读过的原文（避免同一文件读两次）；
    为 None 时按原行为自行读取。
    """
    if original_text is not None:
        original = original_text
    else:
        try:
            with open(md_path, "r", encoding="utf-8") as handle:
                original = handle.read()
        except OSError:
            return new_block_text
    fallback = os.path.splitext(os.path.basename(md_path))[0]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", fallback):
        fallback = None
    try:
        doc = markdown_blocks.parse_markdown(original, fallback_date=fallback)
        old_block = doc.find(weibo_id)
    except Exception as e:  # noqa: BLE001 兜底：解析失败回退新块
        # 静默回退成「新块」会被整块替换，旧评论有丢失风险。
        logger.warning(
            "合并评论块失败，本文件回退为新块（可能丢旧评论）：%s（%s）",
            md_path,
            errors.describe_exception(e),
        )
        return new_block_text
    if old_block is None or not old_block.comments:
        return new_block_text
    try:
        new_marker, new_groups = markdown_blocks.parse_comments_block(new_block_text)
    except Exception as e:  # noqa: BLE001 兜底：解析失败回退新块
        logger.warning(
            "解析新评论块失败，本文件回退为新块（可能丢旧评论）：%s（%s）",
            md_path,
            errors.describe_exception(e),
        )
        return new_block_text
    merged = markdown_blocks.merge_comment_groups(old_block.comments, new_groups)
    return markdown_blocks.render_comments_block(
        new_marker or old_block.comments_marker, merged
    )
