"""写入流程编排。

``write_data`` 负责按 write_mode 依次调用各写入后端（csv/json/sqlite/markdown
等）并分派媒体下载；每个后端失败互不影响（由 wb._run_write_step 隔离）。
"""

from __future__ import annotations

import logging

logger = logging.getLogger("weibo")


def write_data(wb, wrote_count) -> None:
    """将爬到的信息写入文件或数据库（各后端互相隔离）"""
    if wb.got_count <= wrote_count:
        return
    if "csv" in wb.write_mode:
        wb._run_write_step("CSV 写入", wb.write_csv, wrote_count)
    if "json" in wb.write_mode:
        wb._run_write_step("JSON 写入", wb.write_json, wrote_count)
    # MySQL / MongoDB / POST 后端（实现见 util/legacy/backends.py）；未在真实服务上验证，
    # 依赖缺失或连接失败只记失败报告，不阻断其他后端。
    if "mysql" in wb.write_mode:
        wb._run_write_step("MySQL 写入", wb.weibo_to_mysql, wrote_count)
    if "mongo" in wb.write_mode or "mongodb" in wb.write_mode:
        wb._run_write_step("MongoDB 写入", wb.weibo_to_mongodb, wrote_count)
    if "post" in wb.write_mode:
        wb._run_write_step("POST 推送", wb.write_post, wrote_count)
    if "sqlite" in wb.write_mode:
        wb._run_write_step("SQLite 写入", wb.weibo_to_sqlite, wrote_count)
    if "markdown" in wb.write_mode:
        wb._run_write_step("Markdown 写入", wb.write_markdown, wrote_count)

    # 图片下载逻辑：如果使用markdown模式，图片已在write_markdown中下载
    # 否则按默认逻辑下载
    if wb.original_pic_download and "markdown" not in wb.write_mode:
        wb._run_write_step(
            "原创图片下载", wb.download_files, "img", "original", wrote_count
        )
    if wb.original_video_download:
        wb._run_write_step(
            "原创视频下载", wb.download_files, "video", "original", wrote_count
        )
    if wb.original_live_photo_download:
        wb._run_write_step(
            "原创 Live Photo 下载",
            wb.download_files,
            "live_photo",
            "original",
            wrote_count,
        )
    # 下载转发微博文件（如果不禁爬转发）
    if not wb.only_crawl_original:
        if wb.retweet_pic_download and "markdown" not in wb.write_mode:
            wb._run_write_step(
                "转发图片下载", wb.download_files, "img", "retweet", wrote_count
            )
        if wb.retweet_video_download:
            wb._run_write_step(
                "转发视频下载", wb.download_files, "video", "retweet", wrote_count
            )
        if wb.retweet_live_photo_download:
            wb._run_write_step(
                "转发 Live Photo 下载",
                wb.download_files,
                "live_photo",
                "retweet",
                wrote_count,
            )

    # 编辑内容更新：保存旧版快照 + 把 md 正文更新为最新版 + 更新状态索引
    wb._run_write_step("编辑内容更新", wb._apply_edit_updates, wrote_count)
