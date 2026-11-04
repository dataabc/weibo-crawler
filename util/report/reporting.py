"""报告文件命名与路径工具。

把散落在 weibo.py 各报告生成方法里的「安全化用户名 + 统一文件名 + 时间戳」
集中到这里，避免 6 处重复实现。只做命名，不写文件（写盘仍由调用方原子写）。

命名约定（保持报告目录/文件名规则稳定）：

    weibo_<prefix>_<user_id>_<safe_screen_name>_<YYYYmmdd_HHMMSS>.<ext>
"""

from __future__ import annotations

import re
from datetime import datetime

_ILLEGAL_FILENAME_RE = re.compile(r'[\\/:*?"<>|]')

# 报告类型前缀（与现有文件名保持一致）
PREFIX_ARCHIVE_CHECK = "archive_check"
PREFIX_CRAWL_SUMMARY = "crawl_summary"
PREFIX_MEDIA_CHECK = "media_check"
PREFIX_COMMENT_SUMMARY = "comment_summary"
PREFIX_CHANGE_REPORT = "change_report"
PREFIX_WRITE_FAILURES = "write_failures"


def safe_name(name: str | None) -> str:
    """把用户名转成文件名安全形式（非法字符替换为下划线）。"""
    return _ILLEGAL_FILENAME_RE.sub("_", str(name or "unknown")).strip() or "unknown"


def report_timestamp(now: datetime | None = None) -> str:
    """报告文件名里的时间戳（YYYYmmdd_HHMMSS）。"""
    return (now or datetime.now()).strftime("%Y%m%d_%H%M%S")


def report_filename(
    prefix: str,
    user_id: str,
    screen_name: str,
    *,
    ext: str = "txt",
    timestamp: str | None = None,
) -> str:
    """生成账号级报告文件名：weibo_<prefix>_<user_id>_<safe_name>_<ts>.<ext>。"""
    return "weibo_%s_%s_%s_%s.%s" % (
        prefix,
        str(user_id or "unknown"),
        safe_name(screen_name),
        timestamp or report_timestamp(),
        ext.lstrip("."),
    )


def total_report_filename(prefix: str, *, ext: str = "md", timestamp: str | None = None) -> str:
    """生成汇总报告文件名（不带用户）：weibo_<prefix>_total_<ts>.<ext>。"""
    return "weibo_%s_total_%s.%s" % (
        prefix,
        timestamp or report_timestamp(),
        ext.lstrip("."),
    )
