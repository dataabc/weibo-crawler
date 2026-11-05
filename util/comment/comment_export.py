"""评论导出为 CSV。"""

from __future__ import annotations

import csv
import logging
import os
import sqlite3

from util.report import reporting

logger = logging.getLogger("weibo")


def export_comments_to_csv_for_current_user(wb) -> None:
    """将当前用户相关的评论从 SQLite 导出到该用户目录下的 CSV 文件"""
    # 仅在启用了 sqlite 写入且开启下载评论时导出
    if "sqlite" not in wb.write_mode or not wb.download_comment:
        return
    try:
        db_path = wb.get_sqlte_path()
        if not os.path.exists(db_path):
            logger.warning("导出评论失败，未找到SQLite数据库: %s", db_path)
            return

        # 当前用户的 ID，用于筛选属于该用户微博的评论
        user_id = str(wb.user_config.get("user_id", ""))
        if not user_id:
            logger.warning("导出评论失败，当前用户ID为空")
            return

        # 用户结果目录，与微博 CSV 同级，例如 weibo/示例用户2/ 或 weibo/1000000004/
        csv_path = wb.get_filepath("csv")
        user_dir = os.path.dirname(csv_path)
        if not os.path.isdir(user_dir):
            os.makedirs(user_dir)
        # 使用用户昵称作为文件名的一部分，避免再出现纯数字 user_id
        screen_name = wb.user.get("screen_name") or user_id
        safe_screen_name = reporting.safe_name(screen_name)
        out_path = os.path.join(user_dir, f"{safe_screen_name}_comments.csv")

        con = sqlite3.connect(db_path)
        cur = con.cursor()

        # 只导出当前用户微博下的评论
        sql = """
            SELECT
                c.id,
                c.weibo_id,
                c.created_at,
                c.user_screen_name,
                c.text,
                c.pic_url,
                c.like_count
            FROM comments c
            JOIN weibo w ON c.weibo_id = w.id
            WHERE w.user_id = ?
            ORDER BY c.weibo_id, c.id
        """
        rows = cur.execute(sql, (user_id,)).fetchall()
        con.close()

        if not rows:
            logger.info("用户 %s 没有可导出的评论记录，跳过生成评论 CSV", user_id)
            return

        header = [
            "id",
            "weibo_id",
            "created_at",
            "user_screen_name",
            "text",
            "pic_url",
            "like_count",
        ]

        # 1）导出当前用户的汇总评论文件：<用户昵称>_comments.csv
        with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(rows)

        # 2）按每条微博拆分导出：<用户昵称>_<weibo_id>_comments.csv
        #    满足“用户昵称 + weiboId + comments”的文件命名要求
        comments_by_weibo = {}
        for row in rows:
            weibo_id = row[1]
            comments_by_weibo.setdefault(weibo_id, []).append(row)

        for weibo_id, weibo_rows in comments_by_weibo.items():
            per_weibo_path = os.path.join(
                user_dir, f"{safe_screen_name}_{weibo_id}_comments.csv"
            )
            with open(per_weibo_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(header)
                writer.writerows(weibo_rows)

        logger.info(
            "共导出 %d 条评论到用户汇总 CSV: %s，并按每条微博拆分生成 %d 个评论 CSV",
            len(rows),
            out_path,
            len(comments_by_weibo),
        )
    except Exception as e:  # noqa: BLE001 兜底：单点失败不阻断主流程
        logger.exception("导出评论 CSV 失败: %s", e)
