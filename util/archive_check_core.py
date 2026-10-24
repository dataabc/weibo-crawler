"""存档核对公共核心：本地存档与平台名单的双向差集 + 统一报告渲染。

本轮存档核对（weibo.py）与全量存档核对（weibo_archive_check_full.py）共用本模块，
保证两者的比对逻辑、边界与报告格式完全一致；两个入口只负责「取哪份数据、算哪个范围」。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

DTFORMAT = "%Y-%m-%dT%H:%M:%S"


@dataclass
class EnumerationStatus:
    """全量核对时，本轮平台枚举是否顺利完成。"""

    ok: bool = False
    reason: str = "未开始"
    pages_done: int = 0
    page_count: int = 0
    hit_since_date: bool = False
    api_ended: bool = False
    page_errors: list[int] = field(default_factory=list)


def _sort_ids_desc(ids: Iterable[str]) -> list[str]:
    """按微博 ID 从新到旧排序（ID 是数字字符串）。"""

    def key(value: str) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    return sorted(ids, key=key, reverse=True)


@dataclass
class ArchiveDiff:
    """一次核对的双向差集结果。"""

    # 本轮未抓到的已存档微博（本地有、平台未返回）
    not_returned: list[str] = field(default_factory=list)
    # 未保存的微博（平台有、本地没有）
    not_saved: list[str] = field(default_factory=list)
    # 参与比对的本地条目 {id: (日期, 文件路径)} 与平台条目 {id: 日期}，供报告展示
    local_entries: dict[str, tuple[datetime | None, str]] = field(default_factory=dict)
    platform_dates: dict[str, datetime | None] = field(default_factory=dict)


def compare_archive(
    local_entries: dict[str, tuple[datetime | None, str]],
    platform_dates: dict[str, datetime | None],
    invisible_ids: Iterable[str] = (),
    range_start: datetime | None = None,
    range_end: datetime | None = None,
) -> ArchiveDiff:
    """对本地存档与平台名单做双向差集。

    - `local_entries`：本地活跃存档 {id: (发布日期, 文件路径)}；无日期条目一律不参与比对
      （与原实现一致）；给了 range_start/range_end 时只保留范围内条目；
    - `platform_dates`：平台返回的 {id: 发布日期}（枚举/抓取阶段已按范围取数，这里不再过滤日期）；
    - `invisible_ids`：已确认不可见的微博，双向都不参与。
    """
    invisible_set = {str(i) for i in invisible_ids}

    local_in_range: dict[str, tuple[datetime | None, str]] = {}
    for wid, (dt, path) in local_entries.items():
        wid = str(wid)
        if wid in invisible_set or dt is None:
            continue
        if range_start is not None and dt < range_start:
            continue
        if range_end is not None and dt > range_end:
            continue
        local_in_range[wid] = (dt, path)

    platform: dict[str, datetime | None] = {
        str(wid): dt for wid, dt in platform_dates.items()
    }
    local_ids = set(local_entries)

    not_returned = _sort_ids_desc(
        wid for wid in local_in_range if wid not in platform
    )
    not_saved = _sort_ids_desc(
        wid
        for wid in platform
        if wid not in local_ids and wid not in invisible_set
    )
    return ArchiveDiff(
        not_returned=not_returned,
        not_saved=not_saved,
        local_entries=local_entries,
        platform_dates=platform,
    )


def _fmt_date(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d") if dt else "未知"


def _fmt_range(dt: datetime | None) -> str:
    return dt.strftime(DTFORMAT) if dt else "未知"


def render_report(
    title: str,
    user_id: str,
    screen_name: str,
    diff: ArchiveDiff,
    header_lines: Iterable[str] = (),
    range_start: datetime | None = None,
    range_end: datetime | None = None,
    summary_lines: Iterable[str] = (),
    base_dir: str | None = None,
) -> str:
    """渲染统一格式的人可读核对报告（txt）。"""
    import os

    lines: list[str] = [title]
    lines.append(f"用户: {screen_name} (ID: {user_id})")
    if range_start is not None or range_end is not None:
        lines.append(f"核对范围: {_fmt_range(range_start)} ~ {_fmt_range(range_end)}")
    lines.extend(header_lines)
    lines.append("=" * 60)

    lines.append(
        "一、本轮未抓到的已存档微博（本地有、平台未返回；可能已删除/不可见，"
        f"需打开链接确认）: {len(diff.not_returned)} 条"
    )
    lines.append("")
    if diff.not_returned:
        for wid in diff.not_returned:
            dt, path = diff.local_entries.get(wid, (None, ""))
            rel_path = os.path.relpath(path, base_dir) if (base_dir and path) else path
            lines.append(f"  https://m.weibo.cn/detail/{wid}")
            lines.append(
                f"    发布日期: {_fmt_date(dt)}    本地文件: {rel_path or '未知'}"
            )
            lines.append("")
    else:
        lines.append("  （无）")
        lines.append("")

    lines.append("=" * 60)
    lines.append(f"二、未保存的微博（平台有、本地没有）: {len(diff.not_saved)} 条")
    lines.append("")
    if diff.not_saved:
        for wid in diff.not_saved:
            dt = diff.platform_dates.get(wid)
            lines.append(f"  https://m.weibo.cn/detail/{wid}")
            lines.append(
                f"    发布日期: {_fmt_date(dt)}    本地文件: （本地无文件）"
            )
            lines.append("")
    else:
        lines.append("  （无）")
        lines.append("")

    lines.append("=" * 60)
    lines.extend(summary_lines)
    lines.append("报告完毕。")
    return "\n".join(lines) + "\n"
