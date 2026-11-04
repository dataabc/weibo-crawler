"""markdown 文件写入编排。

``generate_markdown_file`` 负责单文件单日期的「读取已有块 → 去重 → 按发布时间
原位插入 → 备份 → 原子写」，依赖的 wb 方法由 Weibo 实例提供。
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from util.core.time_format import DTFORMAT
from util.core import errors
from util.core import markdown_blocks

logger = logging.getLogger("weibo")


def generate_markdown_file(wb, group_key, weibo_list) -> None:
    """生成单个markdown文件（增量模式）"""
    # 获取用户目录
    file_dir = wb.get_filepath("markdown")

    # 创建markdown文件路径
    if wb.markdown_split_by == "all":
         md_file_path = os.path.join(file_dir, f"{wb.user.get('screen_name', 'weibo')}.md")
         title_date = "全量"
    elif wb.markdown_split_by == "day_by_month":
         # 按天分割，但按月归档到子文件夹
         # group_key 格式为 YYYY-MM-DD
         month_folder = group_key[:7]  # 提取 YYYY-MM
         month_dir = os.path.join(file_dir, month_folder)
         if not os.path.isdir(month_dir):
             os.makedirs(month_dir)
         md_file_path = os.path.join(month_dir, f"{group_key}.md")
         title_date = group_key
    else:
         md_file_path = os.path.join(file_dir, f"{group_key}.md")
         title_date = group_key

    # 获取用户名
    username = wb.user.get("screen_name", "未知用户")

    # 读取已有文件并解析为块（去重 + 原位插入 + 保留人工块）
    existing_weibo_ids = set()
    existing_content = ""
    doc = None
    if os.path.exists(md_file_path):
        try:
            with open(md_file_path, "r", encoding="utf-8") as f:
                existing_content = f.read()
            # 块级解析：拿到已有块、评论块、人工块与文件头
            doc = markdown_blocks.parse_markdown(
                existing_content, fallback_date=group_key
            )
            existing_weibo_ids = {
                str(block.weibo_id) for block in doc.blocks if block.weibo_id
            }
            # 兜底：文件不是标准块结构时仍按正则提取，避免重复写入
            existing_weibo_ids.update(
                re.findall(markdown_blocks.WEIBO_ID_RE, existing_content)
            )
            logger.info(f"已读取现有MD文件，包含 {len(existing_weibo_ids)} 条微博记录")
        except Exception as e:  # noqa: BLE001 兜底：失败不阻断
            # 读失败绝不覆盖原文件：跳过该文件并记录到运行失败报告。
            logger.error(
                "读取现有 Markdown 文件失败，本轮跳过该文件（不覆盖原文件）: %s，原因: %s",
                md_file_path,
                e,
            )
            wb._record_write_failure(
                "Markdown 读取", md_file_path, f"读取失败: {e}"
            )
            return

    if doc is None:
        doc = markdown_blocks.MdDocument()

    # 过滤出新的微博（不在已有文件中的）
    new_weibo_list = []
    for w in weibo_list:
        weibo_id = str(w.get("id", ""))
        if weibo_id and weibo_id not in existing_weibo_ids:
            new_weibo_list.append(w)

    # 如果没有新微博，直接返回
    if not new_weibo_list:
        logger.info(f"分组 {group_key} 没有新微博需要写入")
        return

    # 按 markdown_sort_order 排序
    descending = wb.markdown_sort_order != "old_to_new"
    new_weibo_list.sort(key=lambda w: int(w.get("id", 0)), reverse=descending)

    # 构建新微博的markdown内容（每条微博一个块，后面按时间原位插入）
    new_block_texts = []
    for w in new_weibo_list:
        new_md_content = ""
        # 获取时间（HH:MM:SS格式）
        created_at = w.get("created_at", "")
        if not created_at:
            continue

        try:
            time_obj = datetime.strptime(created_at, DTFORMAT)
            time_str = time_obj.strftime("%H:%M:%S")
            date_str = time_obj.strftime("%Y-%m-%d")
            # 根据分组方式决定标题格式
            if wb.markdown_split_by in ["day", "day_by_month"]:
                # 按天分组时，日期已在文件名中，只显示时间
                heading_time = time_str
            else:
                # 按月/年/全量分组时，显示完整日期时间
                heading_time = f"{date_str} {time_str}"
        except ValueError:
            time_str = "00:00:00"
            date_str = created_at # fallback
            heading_time = created_at

        rendered_body = wb._render_weibo_block_body(w, created_at)
        new_md_content = (
            f"### {heading_time}\n\n<!-- weibo_id: {w.get('id')} -->\n\n"
            f"{rendered_body}---\n\n"
        )
        new_block_texts.append(new_md_content)

    # 写入文件（原位插入：按发送时间插到正确位置，而不是追加到末尾）
    try:
        if not doc.title:
            doc.title = f"## {title_date} [{username}] 微博存档\n\n"
        for block_text in new_block_texts:
            block = markdown_blocks.parse_block(block_text, group_key)
            markdown_blocks.insert_block(doc, block, wb.markdown_sort_order)
        final_content = doc.render()
        # 写前备份（同一文件每天最多一份），便于回滚
        wb._backup_markdown(md_file_path)
        # 原子写：主存档是核心资产，中断不会留下截断的文件
        wb._atomic_write_text(md_file_path, final_content)
        logger.info(f"Markdown文件已更新: {md_file_path}，新增 {len(new_weibo_list)} 条微博")
        # 记录本轮新写入的微博，供 get_pages 结束后抓评论并回填
        if getattr(wb, "new_written_weibo", None) is None:
            wb.new_written_weibo = {}
        for written_weibo in new_weibo_list:
            written_id = str(written_weibo.get("id") or "")
            if written_id:
                wb.new_written_weibo[written_id] = md_file_path
    except Exception as e:  # noqa: BLE001 兜底：失败不阻断
        # 主存档写盘失败必须进「本轮写入失败报告」（与读失败一致）。
        if errors.is_programming_error(e):
            logger.exception(
                "生成 Markdown 文件出现程序性错误（疑似代码缺陷）: %s", e
            )
        else:
            logger.exception("生成 Markdown 文件失败（主存档可能未更新）: %s", e)
        wb._record_write_failure(
            "Markdown 写入", md_file_path, errors.describe_exception(e)
        )
