"""存档核对报告拼装。

`archive_check_core` 里已有比对与渲染；本模块只负责把「头部信息 + 机器可读
summary 行」与报告文件名拼装好，保证 weibo.py 里的报告方法只做数据准备。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

from util.core.time_format import DTFORMAT
from util.core import archive_index
from util.report import archive_check_core
from util.report import reporting

logger = logging.getLogger("weibo")


def report_filename(user_id: str, screen_name: str, timestamp: str | None = None) -> str:
    """存档核对报告文件名（与旧命名一致）。"""
    return reporting.report_filename(
        reporting.PREFIX_ARCHIVE_CHECK, user_id, screen_name, timestamp=timestamp
    )


def build_headers(
    *,
    generated_at: datetime,
    mode_label: str,
    source_label: str,
    success_reason: str,
    has_diff: bool,
    archived_count: int,
    crawled_count: int,
) -> list[str]:
    """报告头部行（生成时间/抓取方式/核对结果/数量说明）。"""
    return [
        f"生成时间: {generated_at.strftime('%Y-%m-%d %H:%M:%S')}",
        f"本轮抓取方式: {mode_label}（起始时间来自: {source_label}）",
        f"本轮抓取是否顺利完成: 是（{success_reason}）",
        f"核对结果: {'有发现' if has_diff else '无差异'}",
        f"核对范围内已存档微博数: {archived_count}",
        f"本次抓到微博数: {crawled_count}",
        "说明: 本地文件路径相对于 output_directory。",
    ]


def build_summary_lines(
    *,
    check_start: datetime,
    check_end: datetime,
    is_incremental: bool,
    archived_count: int,
    crawled_count: int,
    not_returned_count: int,
    not_saved_count: int,
    known_invisible_count: int,
) -> list[str]:
    """机器可读的 summary 行（便于后续统计/脚本读取）。"""
    return [
        f"check_start: {check_start.strftime(DTFORMAT)}",
        f"check_end: {check_end.strftime(DTFORMAT)}",
        f"mode: {'incremental' if is_incremental else 'full'}",
        "crawl_success: true",
        f"archived_in_range: {archived_count}",
        f"crawled_this_run: {crawled_count}",
        f"not_returned_count: {not_returned_count}",
        f"not_saved_count: {not_saved_count}",
        f"known_invisible_excluded: {known_invisible_count}",
    ]


def generate(wb) -> None:
    """生成本轮存档核对报告（比对逻辑与全量存档核对共用 util/archive_check_core.py）。

    口径：
    - 本轮未抓到的已存档微博 = 核对范围内主存档有、本轮爬取没返回（可能已删除/不可见）；
    - 未保存的微博 = 本轮已爬到、写盘后主存档仍然没有（保存异常线索）。
    - 本轮抓取顺利完成时每轮都生成报告；无差异时报告里标注「无差异」，名单显示（无）。
    旧的「本次未重爬」报告已移除。
    """
    if not wb.archive_check_enabled:
        return
    if wb.user_config.get("query_list"):
        return
    if "markdown" not in wb.write_mode:
        return
    cov = wb.crawl_success
    if not cov.get("attempted"):
        logger.info("本轮存档核对：本轮未爬取，跳过")
        return
    if not cov.get("success"):
        logger.warning(
            "本轮存档核对：本轮抓取未顺利完成（%s），不出报告",
            cov.get("reason", "未知"),
        )
        return
    if wb.archived_ids_before is None or wb.check_start_dt is None:
        return
    if not wb.user or not wb.user_config:
        return
    try:
        md_dir = wb.get_filepath("markdown")
        crawl_end = (
            datetime.strptime(wb.start_date, DTFORMAT)
            if wb.start_date
            else datetime.now()
        )
        crawled = {str(i) for i in wb.crawled_ids_this_run if i}
        if getattr(wb, "_known_invisible_loaded", False) and not getattr(
            wb, "_known_invisible_load_failed", False
        ):
            # 抓取阶段已成功加载，直接复用，省一轮「已不可见/」全量扫描。
            known_gone = set(getattr(wb, "known_invisible_ids", ()) or ())
        else:
            known_gone = archive_index.collect_invisible_weibo_ids(
                md_dir, wb._archive_exclude_dirs()
            )
        crawled_dates: dict[str, datetime | None] = {}
        for w in wb.weibo:
            wid = str(w.get("id") or "")
            if not wid:
                continue
            raw = w.get("created_at") or ""
            try:
                crawled_dates[wid] = datetime.strptime(raw, DTFORMAT)
            except ValueError:
                crawled_dates[wid] = None
        platform = {wid: crawled_dates.get(wid) for wid in crawled}
        # 「未保存的微博」基于本轮**写盘之后**的主存档判断：
        # 抓取前快照只用于「已抓取过/新抓到」统计（crawl_summary）。
        local_entries = wb.archived_entries_before or {}
        try:
            local_entries, after_scan_stats = archive_index.collect_archive_entries(
                md_dir,
                wb.check_start_dt,
                crawl_end,
                wb._archive_exclude_dirs(),
            )
            logger.info(
                "存档核对：写盘后重扫主存档 %d 条（扫描 %d 个文件，跳过 %d 个）",
                len(local_entries),
                after_scan_stats.files_scanned,
                after_scan_stats.files_skipped,
            )
        except Exception as scan_error:  # noqa: BLE001 兜底：失败不阻断
            logger.warning(
                "存档核对：写盘后重扫主存档失败（%s），改用抓取前快照，"
                "本轮「未保存」判定可能偏保守",
                scan_error,
            )
        diff = archive_check_core.compare_archive(
            local_entries,
            platform,
            known_gone,
            wb.check_start_dt,
            crawl_end,
        )
        has_diff = bool(diff.not_returned or diff.not_saved)
        if not has_diff:
            logger.info(
                "本轮存档核对：本轮核对范围内无差异（主存档 %d 条，本次爬到 %d 条），仍生成报告",
                len(diff.local_entries), len(crawled),
            )

        base_dir = wb._report_output_dir(
            getattr(wb, "archive_check_dir", "report")
        )
        # 报告里「本地文件」路径以 output_directory 为基准（与报告抬头一致）。
        path_base_dir = wb._output_root_dir()
        user_screen_name = wb.user.get(
            "screen_name", wb.user_config.get("user_id", "unknown")
        )
        user_id = wb.user_config.get("user_id", "unknown")
        report_path = os.path.join(
            base_dir,
            report_filename(
                user_id, user_screen_name
            ),
        )
        is_incremental = wb.user_config.get("since_date_source") == "user_list"
        mode_label = "增量" if is_incremental else "全量"
        source_label = "用户列表第三列" if is_incremental else "配置文件"
        os.makedirs(base_dir, exist_ok=True)
        header_lines = build_headers(
            generated_at=datetime.now(),
            mode_label=mode_label,
            source_label=source_label,
            success_reason=cov.get("reason", ""),
            has_diff=has_diff,
            archived_count=len(diff.local_entries),
            crawled_count=len(crawled),
        )
        summary_lines = build_summary_lines(
            check_start=wb.check_start_dt,
            check_end=crawl_end,
            is_incremental=is_incremental,
            archived_count=len(diff.local_entries),
            crawled_count=len(crawled),
            not_returned_count=len(diff.not_returned),
            not_saved_count=len(diff.not_saved),
            known_invisible_count=len(known_gone),
        )
        report = archive_check_core.render_report(
            "本轮存档核对报告",
            user_id,
            user_screen_name,
            diff,
            header_lines=header_lines,
            range_start=wb.check_start_dt,
            range_end=crawl_end,
            summary_lines=summary_lines,
            base_dir=path_base_dir,
        )
        wb._atomic_write_text(report_path, report)
        logger.info(
            "存档核对报告已生成: %s（未抓到 %d，未保存 %d）",
            report_path, len(diff.not_returned), len(diff.not_saved),
        )
    except Exception as e:  # noqa: BLE001 兜底：失败不阻断
        logger.exception("生成存档核对报告失败: %s", e)
