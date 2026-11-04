"""评论回填主流程。

等价原 Weibo.write_markdown_comments_for_new_weibo：把本轮新写入/待重试/待刷新的
微博评论抓取并合并写回 md，失败记入持久化重试清单。依赖由 Weibo 实例提供。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from util.comment import comment_fetch
from util.comment import comment_markdown
from util.comment import comment_refresh
from util.core import archive_state
from util.core.time_format import DTFORMAT

logger = logging.getLogger("weibo")


def run(wb) -> None:
    """对本轮新写入 Markdown 的微博（以及上次失败项）抓评论并写回。

    - 只在 download_comment=1 且 write_mode 含 markdown 时执行；
    - 抓取失败（网络/HTTP/风控）不当作「无评论」：记录到持久化失败清单
      （report/comment_retry_<账号>.json），下一轮自动重试，避免评论永久
      缺失；
    - 单条微博失败只记日志与报告，不阻断主流程。
    """
    if not getattr(wb, "download_comment", 0):
        return
    if "markdown" not in wb.write_mode:
        return
    written = getattr(wb, "new_written_weibo", None) or {}
    retry_map = wb._load_comment_retry()
    targets = {}
    for weibo_id, info in retry_map.items():
        md_path = str(info.get("md_path") or "")
        if md_path and os.path.isfile(md_path):
            targets[weibo_id] = md_path
    retried_ids = set(targets)
    for weibo_id, md_path in written.items():
        targets[weibo_id] = md_path

    # 把「本轮抓到的已存档微博」也纳入评论刷新（默认 all，轮转刷新 + 单轮预算）
    # 目标选择逻辑见 util/comment_refresh.py
    scope = str(getattr(wb, "comment_refresh_scope", "all") or "all").lower()
    # refresh_state 在分支外初始化：scope=off 时函数尾部仍会读取这两个变量。
    refresh_state = None
    refresh_state_path = ""
    if scope in ("all", "recent"):
        try:
            refresh_state = wb._ensure_archive_state()
            refresh_state_path = getattr(wb, "_state_path", "")
        except Exception as e:  # noqa: BLE001
            logger.warning("状态索引不可用，跳过已存档微博评论刷新: %s", e)
            refresh_state = None
            refresh_state_path = ""
        if refresh_state is not None:
            for weibo_id, md_file in comment_refresh.select_archive_refresh_candidates(
                getattr(wb, "weibo", []) or [],
                refresh_state,
                wb._locate_block_file,
                scope=scope,
                recent_days=int(
                    getattr(wb, "comment_refresh_recent_days", 30) or 30
                ),
                limit=int(getattr(wb, "comment_refresh_limit", 300) or 0),
                skip_ids=set(targets),
            ):
                targets[weibo_id] = md_file
    if not targets:
        logger.info("本轮没有新写入 Markdown 的微博，也没有待刷新/待重试的评论，跳过评论抓取")
        return

    allowed_users = wb._comment_allowed_users()
    if allowed_users:
        logger.info("评论抓取已启用评论者白名单：%d 个评论者", len(allowed_users))
    logger.info(
        "开始抓取评论并写入 Markdown：新微博 %d 条，待重试 %d 条",
        len(written),
        len(retried_ids),
    )
    options = comment_fetch.FetchOptions(
        max_root=int(getattr(wb, "comment_max_download_count", 0) or 0),
        max_reply=int(getattr(wb, "comment_reply_max_download_count", 0) or 0),
        delay_min=float(getattr(wb, "comment_request_delay_min", 1.0) or 0),
        delay_max=float(getattr(wb, "comment_request_delay_max", 2.0) or 0),
        include_latest=bool(getattr(wb, "comment_include_latest_stream", 0)),
        emoji_replace=bool(getattr(wb, "comment_emoji_replace", 1)),
        link_preview=bool(
            (getattr(wb, "markdown_preview", None) or {}).get("link_preview", 0)
        ),
        max_requests=int(getattr(wb, "comment_max_requests", 300) or 300),
    )
    render_options = comment_markdown.RenderOptions(
        show_like_count=bool(getattr(wb, "comment_show_like_count", 0)),
        show_reply_target=bool(getattr(wb, "comment_show_reply_target", 1)),
        image_dir=wb.markdown_file_dir.get("comment_image_dir", "评论图片"),
    )

    results = []
    backed_up_files: set[str] = set()
    for weibo_id, md_path in list(targets.items()):
        record = {
            "weibo_id": weibo_id,
            "md_path": md_path,
            "status": "",
            "total_number": 0,
            "roots": 0,
            "replies": 0,
            "requests": 0,
            "errors": 0,
            "note": "",
            "retried": weibo_id in retried_ids,
        }
        try:
            if not os.path.isfile(md_path):
                record.update(status="跳过", note="Markdown 文件不存在")
                retry_map.pop(weibo_id, None)
                results.append(record)
                continue
            result = comment_fetch.fetch_comments(
                wb.session, wb.headers, weibo_id, options
            )
            if allowed_users:
                result = comment_fetch.filter_comments(result, allowed_users)
            record["filtered_users"] = len(allowed_users) if allowed_users else 0
            record["total_number"] = result.total_number
            record["roots"] = len(result.roots)
            record["replies"] = result.stats.replies_fetched
            record["requests"] = result.stats.requests
            record["errors"] = result.stats.errors
            if result.failed:
                # 网络/HTTP/风控失败不是「无评论」：记入持久化重试清单，
                # 下一轮自动重试，避免该微博评论永久缺失
                messages = getattr(result.stats, "error_messages", None) or []
                reason = (
                    messages[-1]
                    if messages
                    else (result.stats.stopped_reason or "未知")
                )
                note = "抓取失败: %s" % reason
                record.update(status="失败", note=note)
                retry_map[weibo_id] = {
                    "md_path": md_path,
                    "note": note,
                    "updated_at": datetime.now().strftime(DTFORMAT),
                }
                results.append(record)
                continue
            if not result.roots:
                note = "接口未返回评论"
                if allowed_users:
                    note = "按评论者白名单过滤后无内容"
                record.update(status="无评论", note=note)
                retry_map.pop(weibo_id, None)
                # 无评论也算「已刷新」，记录时间避免下一轮重复请求
                if refresh_state is not None:
                    state_item = archive_state.get_weibo(refresh_state, weibo_id)
                    if state_item is not None:
                        state_item["comment_refreshed_at"] = datetime.now().strftime(
                            DTFORMAT
                        )
                results.append(record)
                continue
            if getattr(wb, "comment_pic_download", 0):
                wb._download_comment_images(result, md_path)
            block = comment_markdown.render_comment_block(
                weibo_id,
                result.roots,
                total_number=result.total_number,
                options=render_options,
            )
            # 同一文件只读一次，合并与写回共用（两次读之间无写操作）。
            try:
                with open(md_path, encoding="utf-8") as handle:
                    original_text = handle.read()
            except OSError:
                original_text = None
            # 与 md 里已有评论块按 cid 合并（旧评论不丢，新评论追加/更新）
            block = wb._merge_comment_block(
                md_path, weibo_id, block, original_text=original_text
            )
            changed, update_status = comment_markdown.update_markdown_file_status(
                md_path,
                weibo_id,
                block,
                backup=bool(getattr(wb, "comment_markdown_backup", 0)),
                backup_state=backed_up_files,
                original_text=original_text,
                emoji_store=wb._emoji_store(),
            )
            if update_status == comment_markdown.UPDATE_MISSING:
                record.update(
                    status="跳过",
                    note="未找到微博块（文件可能被人工修改）",
                )
                retry_map.pop(weibo_id, None)
                results.append(record)
                continue
            record.update(
                status="已写入" if changed else "未变化",
                note="停止原因: %s" % (result.stats.stopped_reason or "未知"),
            )
            if refresh_state is not None:
                state_item = archive_state.get_weibo(refresh_state, weibo_id)
                if state_item is not None:
                    state_item["comment_refreshed_at"] = datetime.now().strftime(
                        DTFORMAT
                    )
            retry_map.pop(weibo_id, None)
        except Exception as e:  # noqa: BLE001 兜底：失败不阻断
            logger.exception("抓取/写入评论失败 微博id:%s", weibo_id)
            note = "%s: %s" % (type(e).__name__, e)
            record.update(status="失败", note=note)
            if os.path.isfile(md_path):
                retry_map[weibo_id] = {
                    "md_path": md_path,
                    "note": note,
                    "updated_at": datetime.now().strftime(DTFORMAT),
                }
        results.append(record)

    # 清理重试清单：md 已不存在的条目不再重试
    for weibo_id in list(retry_map):
        md_path = str((retry_map.get(weibo_id) or {}).get("md_path") or "")
        if not md_path or not os.path.isfile(md_path):
            retry_map.pop(weibo_id, None)
    wb._save_comment_retry(retry_map)
    if refresh_state is not None and refresh_state_path:
        try:
            archive_state.save_state(
                refresh_state, refresh_state_path, wb._atomic_write_text
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("保存状态索引失败: %s", e)

    written_count = sum(1 for item in results if item["status"] == "已写入")
    failed_count = sum(1 for item in results if item["status"] == "失败")
    logger.info(
        "评论写入 Markdown 完毕：%d 条已写入，%d 条失败（已记入重试清单，下次自动重试），%d 条无评论/跳过",
        written_count,
        failed_count,
        len(results) - written_count - failed_count,
    )
    if retry_map:
        logger.warning(
            "评论失败重试清单还有 %d 条，将在下一轮继续重试", len(retry_map)
        )
    if getattr(wb, "comment_summary_enabled", 0):
        wb.generate_comment_summary_report(results)
