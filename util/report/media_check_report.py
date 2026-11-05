"""媒体完整性报告。"""

from __future__ import annotations

import logging
import os

from util.core import project
from util.media import media_probe, media_report
from util.report import reporting

logger = logging.getLogger("weibo")


def generate_media_check_report(wb) -> None:
    """生成媒体完整性报告（每个账号一份，默认关闭）。

    扫描该用户活跃 markdown 里的本地媒体链接，检查文件是否存在、是否为空、
    文件头/尾部是否明显损坏；只读，不下载、不修复。
    """
    if not getattr(wb, "media_check_enabled", 0):
        return
    if "markdown" not in wb.write_mode:
        logger.info("媒体完整性报告：write_mode 不含 markdown，跳过")
        return
    if wb.user_config.get("query_list"):
        logger.info("媒体完整性报告：关键词(query)模式跳过")
        return
    cov = wb.crawl_success
    if not cov.get("attempted"):
        logger.info("媒体完整性报告：本轮未爬取，跳过")
        return
    if not wb.user or not wb.user_config:
        return
    try:
        user_id = str(wb.user_config.get("user_id", "unknown"))
        screen_name = str(wb.user.get("screen_name") or user_id)
        md_dir = wb.get_filepath("markdown")
        result = media_report.scan_user_media(md_dir, wb._archive_exclude_dirs())
        base_dir = wb._report_output_dir(
            getattr(wb, "media_check_dir", "report")
        )
        os.makedirs(base_dir, exist_ok=True)
        output_root = os.path.join(project.project_root(), wb.output_directory)
        report_path = os.path.join(
            base_dir,
            reporting.report_filename(
                reporting.PREFIX_MEDIA_CHECK, user_id, screen_name
            ),
        )
        # 对缺失项做在线归因（区分「平台侧已删除」与「本地缺失」）。
        # 只探测缺失项（通常个位数）；探测失败降级为「无法确认」，不阻断报告。
        download_urls = wb._load_not_downloaded_map()
        missing_paths = [
            issue.resolved_path
            for issue in result.issues
            if issue.kind == "missing"
        ]
        attributions: dict[str, str] = {}
        if missing_paths:
            try:
                attributions = media_probe.probe_missing_sources(
                    wb.session, wb.headers, missing_paths, download_urls
                )
                logger.info(
                    "媒体完整性报告：对 %d 条缺失媒体做了在线归因探测",
                    len(missing_paths),
                )
            except Exception as e:  # noqa: BLE001 兜底：探测失败不阻断报告
                logger.warning("媒体完整性报告：缺失媒体在线归因失败: %s", e)
        report = media_report.render_media_report(
            user_id,
            screen_name,
            result,
            output_root=output_root,
            crawl_success=bool(cov.get("success")),
            crawl_reason=cov.get("reason", ""),
            download_urls=download_urls,
            attributions=attributions,
        )
        wb._atomic_write_text(report_path, report)
        logger.info(
            "媒体完整性报告已生成: %s（扫描 %d 个 md，媒体链接 %d 条，问题 %d 条）",
            report_path,
            result.files_scanned,
            result.links_total,
            result.issue_count,
        )
    except Exception as e:  # noqa: BLE001 兜底：单点失败不阻断主流程
        logger.exception("生成媒体完整性报告失败: %s", e)
