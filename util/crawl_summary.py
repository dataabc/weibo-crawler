"""本轮抓取情况报告：抓取结束后按账号统计并渲染 txt。

纯函数模块：不读写文件、不联网，便于单元测试；写文件由 weibo.py 负责。

口径（与《口径与叫法.md》一致）：
- 已抓取过的微博 = 抓取开始前本地 markdown 主存档里已经有的微博；
- 本轮新抓到的微博 = 本轮抓到、但抓取开始前本地主存档里没有的微博；
- 抓取前已存档微博 = 抓取开始前扫描到的主存档（排除已确认不可见的微博）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Mapping

DTFORMAT = "%Y-%m-%dT%H:%M:%S"
DISPLAY_DT = "%Y-%m-%d %H:%M:%S"
TITLE = "本轮抓取情况报告"
SEP = "=" * 60


def parse_created_at(raw: object) -> datetime | None:
    """把微博的 created_at（标准格式 YYYY-MM-DDTHH:MM:SS）解析为 datetime。"""
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw), DTFORMAT)
    except (TypeError, ValueError):
        return None


def _sort_ids_desc(ids: Iterable[str]) -> list[str]:
    """按微博 ID 从新到旧排序（先转字符串，去重，忽略空值）。"""

    def key(value: str) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    return sorted({str(i) for i in ids if i}, key=key, reverse=True)


@dataclass
class CrawlSummary:
    """一次抓取情况的统计结果。"""

    crawled_count: int = 0
    new_count: int = 0
    existing_count: int = 0
    # 新抓到微博 [(ID, 发布日期)]，按 ID 从新到旧
    new_entries: list[tuple[str, datetime | None]] = field(default_factory=list)
    # 新抓到微博按月计数 {"2026-10": 3}，只统计日期已知的
    month_counts: dict[str, int] = field(default_factory=dict)
    unknown_date_count: int = 0
    earliest_new: datetime | None = None
    latest_new: datetime | None = None
    # 抓取前存档基准是否可用；不可用时 new/existing 均无意义
    baseline_available: bool = False
    archived_count: int | None = None


def build_crawl_summary(
    crawled_ids: Iterable[str],
    crawled_dates: Mapping[str, datetime | None] | None = None,
    archived_ids: Iterable[str] | None = None,
    archived_count: int | None = None,
    baseline_available: bool = True,
) -> CrawlSummary:
    """按「抓取前存档」拆分本轮抓到的微博。

    - crawled_ids：本轮抓到/写入的微博 ID；
    - crawled_dates：{ID: 发布日期}，缺日期或解析失败传 None；
    - archived_ids：抓取开始前主存档里的 ID；
    - archived_count：抓取开始前主存档条数（用于报告展示）；
    - baseline_available：False 表示没有可用基准（非 markdown 模式、扫描失败等），
      此时不硬算新旧，只保留抓到总数。
    """
    ids = _sort_ids_desc(crawled_ids)
    dates = crawled_dates or {}
    crawled_count = len(ids)

    if not baseline_available:
        return CrawlSummary(crawled_count=crawled_count, baseline_available=False)

    archived = {str(i) for i in (archived_ids or ())}
    new_ids = [wid for wid in ids if wid not in archived]
    existing_count = crawled_count - len(new_ids)

    entries = [(wid, dates.get(wid)) for wid in new_ids]
    dated = [dt for _wid, dt in entries if dt is not None]
    month_counts: dict[str, int] = {}
    for dt in dated:
        key = dt.strftime("%Y-%m")
        month_counts[key] = month_counts.get(key, 0) + 1

    return CrawlSummary(
        crawled_count=crawled_count,
        new_count=len(new_ids),
        existing_count=existing_count,
        new_entries=entries,
        month_counts=dict(sorted(month_counts.items())),
        unknown_date_count=len(entries) - len(dated),
        earliest_new=min(dated) if dated else None,
        latest_new=max(dated) if dated else None,
        baseline_available=True,
        archived_count=archived_count,
    )


@dataclass
class CrawlRunInfo:
    """渲染报告所需的本次运行信息（与统计结果分开，便于测试）。"""

    user_id: str
    screen_name: str
    mode: str  # incremental / full
    mode_label: str  # 增量 / 全量
    source_label: str  # 用户列表第三列 / 配置文件
    range_start: datetime | None
    range_end: datetime | None
    success: bool
    success_reason: str
    pages_processed: int = 0
    page_count: int = 0
    page_errors: int = 0
    skipped_invisible: int = 0
    filtered_retweet: int = 0
    media_failures: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    baseline_note: str = ""


def format_elapsed(seconds: int | None) -> str:
    """把秒数格式化为「X 分 Y 秒」；不足 1 分钟只显示秒。"""
    if seconds is None:
        return "未知"
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} 秒"
    return f"{seconds // 60} 分 {seconds % 60} 秒"


def _fmt_datetime(dt: datetime | None) -> str:
    return dt.strftime(DISPLAY_DT) if dt else "未知"


def _fmt_range(dt: datetime | None) -> str:
    return dt.strftime(DTFORMAT) if dt else "未知"


def _fmt_date_range(summary: CrawlSummary) -> str:
    if not summary.new_entries:
        return "（无）"
    if summary.earliest_new is None:
        return "未知"
    return (
        f"{summary.earliest_new.strftime('%Y-%m-%d')} ~ "
        f"{summary.latest_new.strftime('%Y-%m-%d')}"
    )


def render_crawl_summary(info: CrawlRunInfo, summary: CrawlSummary) -> str:
    """渲染人可读的本轮抓取情况报告（txt）。"""
    elapsed = None
    if info.started_at and info.finished_at:
        elapsed = int((info.finished_at - info.started_at).total_seconds())

    lines: list[str] = [TITLE]
    lines.append(f"用户: {info.screen_name} (ID: {info.user_id})")
    lines.append(f"生成时间: {_fmt_datetime(info.finished_at)}")
    lines.append(f"本轮抓取方式: {info.mode_label}（起始时间来自: {info.source_label}）")
    lines.append(
        f"抓取范围: {_fmt_range(info.range_start)} ~ {_fmt_range(info.range_end)}"
    )
    success_text = "是" if info.success else "否"
    if info.success_reason:
        success_text += f"（{info.success_reason}）"
    lines.append(f"本轮抓取是否顺利完成: {success_text}")
    lines.append(
        f"抓取页数: {info.pages_processed} / {info.page_count}"
        f"（页级失败 {info.page_errors}）    耗时: {format_elapsed(elapsed)}"
    )

    if summary.baseline_available:
        if summary.archived_count is None:
            archived_text = "未知"
        else:
            archived_text = f"{summary.archived_count} 条"
    else:
        archived_text = "无法获取"
        if info.baseline_note:
            archived_text += f"（{info.baseline_note}）"
    lines.append(f"抓取前已存档微博数（抓取范围内）: {archived_text}")
    lines.append(f"本轮抓到微博数: {summary.crawled_count} 条")
    if summary.baseline_available:
        lines.append(f"  其中已抓取过（抓取前本地存档已有）: {summary.existing_count} 条")
        lines.append(f"  本轮新抓到: {summary.new_count} 条")
    else:
        lines.append("  其中已抓取过（抓取前本地存档已有）: 无法判断")
        lines.append("  本轮新抓到: 无法判断")
    lines.append(f"新抓到微博日期范围: {_fmt_date_range(summary)}")
    lines.append(f"已确认不可见而跳过写入: {info.skipped_invisible} 条")
    lines.append(f"因只抓原创而跳过的转发: {info.filtered_retweet} 条")
    lines.append(f"媒体下载失败: {info.media_failures} 个文件")
    lines.append(
        "说明: 「已抓取过」= 抓取开始前本地 markdown 主存档里已有；"
        "已确认不可见的微博不参与。"
    )
    lines.append(SEP)

    lines.append(f"一、本轮新抓到的微博（按 ID 从新到旧）: {summary.new_count} 条")
    lines.append("")
    if not summary.baseline_available:
        note = info.baseline_note or "没有可用的抓取前存档基准"
        lines.append(f"  （无法判断：{note}）")
        lines.append("")
    elif not summary.new_entries:
        lines.append("  （无）")
        lines.append("")
    else:
        for wid, dt in summary.new_entries:
            lines.append(f"  https://m.weibo.cn/detail/{wid}")
            lines.append(f"    发布日期: {_fmt_datetime(dt)}")
            lines.append("")

    lines.append(SEP)
    lines.append("二、新抓到微博按月分布")
    lines.append("")
    if not summary.baseline_available:
        lines.append("  （无法判断）")
        lines.append("")
    elif not summary.month_counts and summary.unknown_date_count == 0:
        lines.append("  （无）")
        lines.append("")
    else:
        for month, count in summary.month_counts.items():
            lines.append(f"  {month}: {count} 条")
        if summary.unknown_date_count:
            lines.append(f"  日期未知: {summary.unknown_date_count} 条")
        lines.append("")

    lines.append(SEP)
    lines.append("统计（便于机器读取）")
    lines.append(f"crawl_success: {'true' if info.success else 'false'}")
    lines.append(f"mode: {info.mode}")
    lines.append(f"range_start: {_fmt_range(info.range_start)}")
    lines.append(f"range_end: {_fmt_range(info.range_end)}")
    lines.append(f"crawled_count: {summary.crawled_count}")
    lines.append(f"baseline_available: {'true' if summary.baseline_available else 'false'}")
    if summary.baseline_available:
        lines.append(f"archived_count: {summary.archived_count}")
        lines.append(f"already_archived_count: {summary.existing_count}")
        lines.append(f"new_count: {summary.new_count}")
    else:
        lines.append("archived_count: unknown")
        lines.append("already_archived_count: unknown")
        lines.append("new_count: unknown")
    lines.append(f"pages_processed: {info.pages_processed}")
    lines.append(f"page_count: {info.page_count}")
    lines.append(f"page_errors: {info.page_errors}")
    lines.append(f"skipped_invisible: {info.skipped_invisible}")
    lines.append(f"filtered_retweet: {info.filtered_retweet}")
    lines.append(f"media_download_failures: {info.media_failures}")
    lines.append("报告完毕。")
    return "\n".join(lines) + "\n"
