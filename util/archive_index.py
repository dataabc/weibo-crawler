"""存档扫描与本轮存档核对工具。

纯函数模块：只读本地 markdown，不联网、不写文件。
用途：
- 布局感知扫描（用户根目录 + YYYY-MM 月份目录），天然跳过媒体目录、隐藏目录、人工目录；
- 从 md 中提取 `<!-- weibo_id: ... -->` 与每条微博的日期；
- 按时间窗过滤；
- 收集 `已不可见/` 等"已确认不可见的微博"目录中的 ID。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Iterator

DTFORMAT = "%Y-%m-%dT%H:%M:%S"

# 宽松匹配：兼容历史/人工文件里带 tab 或多余空格的 marker（B6）
MARKER_RE = re.compile(r"<!--\s*weibo_id:\s*(\d+)\s*-->")
HEADING_RE = re.compile(r"^###[ \t]*(.+?)[ \t]*$", re.MULTILINE)
FILE_DAY_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\.md$", re.IGNORECASE)
MONTH_DIR_RE = re.compile(r"^\d{4}-\d{2}$")

DEFAULT_EXCLUDE_DIRS = ("已不可见",)


@dataclass
class ScanStats:
    """一次扫描的统计，用于报告与排查。"""

    files_scanned: int = 0
    files_skipped: int = 0
    entries: int = 0
    unknown_dates: int = 0
    excluded_dirs: list[str] = field(default_factory=list)


def _is_md_name(name: str) -> bool:
    return name.lower().endswith(".md") and not name.startswith(".")


def iter_active_md_paths(
    user_dir: str, exclude_dirs: Iterable[str] = DEFAULT_EXCLUDE_DIRS
) -> Iterator[str]:
    """按生成布局返回活跃存档的 md 路径。

    只扫用户根目录的 md 与 `YYYY-MM` 月份的 md（覆盖 day / day_by_month /
    month / year / all 五种 markdown_split_by 的实际产物）；不进入媒体目录、
    隐藏目录、`已不可见/` 等人工目录。
    """
    if not os.path.isdir(user_dir):
        return
    exclude = set(exclude_dirs or ())

    for name in sorted(os.listdir(user_dir)):
        path = os.path.join(user_dir, name)
        if os.path.isfile(path) and _is_md_name(name):
            yield path

    for name in sorted(os.listdir(user_dir)):
        path = os.path.join(user_dir, name)
        if (
            os.path.isdir(path)
            and not name.startswith(".")
            and name not in exclude
            and MONTH_DIR_RE.match(name)
        ):
            for fname in sorted(os.listdir(path)):
                fpath = os.path.join(path, fname)
                if os.path.isfile(fpath) and _is_md_name(fname):
                    yield fpath


def read_md_text(path: str) -> str:
    """读取 md 文本；编码异常时忽略坏字节，避免 AppleDouble 等坏文件中断扫描。"""
    with open(path, "r", encoding="utf-8-sig", errors="ignore") as f:
        return f.read()


def file_date_from_name(path: str) -> datetime | None:
    """从 `YYYY-MM-DD.md` 文件名提取日期；其他布局返回 None（由标题补全）。"""
    m = FILE_DAY_RE.match(os.path.basename(path))
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def parse_heading_datetime(
    heading: str, file_date: datetime | None
) -> datetime | None:
    """解析微博标题里的时间。

    支持三种实际格式：
    - `HH:MM:SS`（day / day_by_month，日期由文件名提供）；
    - `YYYY-MM-DD HH:MM:SS`（month / year / all）；
    - `YYYY-MM-DD`（容错）。
    """
    text = heading.strip()
    if not text:
        return None

    # HH:MM:SS
    if len(text) == 8 and text.count(":") == 2 and file_date is not None:
        try:
            t = datetime.strptime(text, "%H:%M:%S")
        except ValueError:
            return None
        return file_date.replace(
            hour=t.hour, minute=t.minute, second=t.second
        )

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    # 有时标题带日期但格式不标准，尝试从文本中截取前 19 位
    if len(text) >= 19:
        try:
            return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    return None


def parse_md_entries(
    text: str, file_date: datetime | None
) -> list[tuple[str, datetime | None]]:
    """解析一份 md 中的所有 (weibo_id, 日期)。

    日期取 marker 之前最近的 `### 标题`；若标题只有时间，用文件日期补全；
    解析不到的日期返回 None。
    """
    headings = [(m.start(), m.group(1)) for m in HEADING_RE.finditer(text)]
    entries: list[tuple[str, datetime | None]] = []
    heading_idx = 0
    current_heading: str | None = None

    for marker in MARKER_RE.finditer(text):
        while heading_idx < len(headings) and headings[heading_idx][0] < marker.start():
            current_heading = headings[heading_idx][1]
            heading_idx += 1
        dt = (
            parse_heading_datetime(current_heading, file_date)
            if current_heading is not None
            else None
        )
        if dt is None and file_date is not None:
            dt = file_date
        entries.append((marker.group(1), dt))
    return entries


def collect_archive_entries(
    user_dir: str,
    start: datetime | None = None,
    end: datetime | None = None,
    exclude_dirs: Iterable[str] = DEFAULT_EXCLUDE_DIRS,
) -> tuple[dict[str, tuple[datetime | None, str]], ScanStats]:
    """扫描活跃存档，返回 {weibo_id: (日期, 文件路径)} 与统计。

    给了 start/end 时只保留核对范围内的条目（无日期条目计入 unknown_dates，
    不参与范围判定）。文件路径供报告定位本地存档用。
    """
    entries: dict[str, tuple[datetime | None, str]] = {}
    stats = ScanStats()
    window = start is not None or end is not None

    for path in iter_active_md_paths(user_dir, exclude_dirs):
        stats.files_scanned += 1
        file_date = file_date_from_name(path)
        # 快速跳过明显在核对范围外的按天文件（增量核对通常只读最近几个月）
        if window and file_date is not None:
            if start is not None and file_date.date() < start.date():
                stats.files_skipped += 1
                continue
            if end is not None and file_date.date() > end.date():
                stats.files_skipped += 1
                continue
        try:
            text = read_md_text(path)
        except OSError:
            stats.files_skipped += 1
            continue
        for wid, dt in parse_md_entries(text, file_date):
            stats.entries += 1
            if dt is None:
                stats.unknown_dates += 1
                if window:
                    continue
            elif window and (
                (start is not None and dt < start)
                or (end is not None and dt > end)
            ):
                continue
            entries.setdefault(wid, (dt, path))
    return entries, stats


def collect_archive_ids(
    user_dir: str,
    start: datetime | None = None,
    end: datetime | None = None,
    exclude_dirs: Iterable[str] = DEFAULT_EXCLUDE_DIRS,
) -> tuple[dict[str, datetime | None], ScanStats]:
    """扫描活跃存档，返回 {weibo_id: 日期} 与统计（collect_archive_entries 的简化版）。"""
    entries, stats = collect_archive_entries(user_dir, start, end, exclude_dirs)
    return {wid: dt for wid, (dt, _path) in entries.items()}, stats


def collect_known_invisible_ids(
    user_dir: str, exclude_dirs: Iterable[str] = DEFAULT_EXCLUDE_DIRS
) -> set[str]:
    """收集"已确认不可见的微博"目录（默认 `已不可见/`）里的全部 weibo_id。

    这些目录存放的是已核实被账号主删除/隐藏的微博内容，不能当未保存的微博补回。
    解析时使用宽松正则，避免漏掉带 tab 的历史 marker。
    """
    known: set[str] = set()
    for name in exclude_dirs or ():
        path = os.path.join(user_dir, name)
        if not os.path.isdir(path):
            continue
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fname in files:
                if fname.startswith(".") or not fname.lower().endswith(".md"):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    text = read_md_text(fpath)
                except OSError:
                    continue
                known.update(MARKER_RE.findall(text))
    return known


IGNORE_FILE_NAME = "ignore_ids.txt"


def collect_ignore_file_ids(
    user_dir: str, filename: str = IGNORE_FILE_NAME
) -> set[str]:
    """读取用户目录下的 `ignore_ids.txt`（每行一个 ID，支持 # 注释与行尾备注）。

    用于「没有 md 可移动」的人工忽略场景：人工删除过文件、或从不想补回的 ID。
    """
    ids: set[str] = set()
    path = os.path.join(user_dir, filename)
    if not os.path.isfile(path):
        return ids
    try:
        with open(path, "r", encoding="utf-8-sig", errors="ignore") as f:
            text = f.read()
    except OSError:
        return ids
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # 允许 "ID 原因"、"ID\t原因" 等写法，取第一个全数字 token 作为 ID
        for token in line.replace("\t", " ").split():
            if token.isdigit():
                ids.add(token)
                break
    return ids


def collect_invisible_weibo_ids(
    user_dir: str,
    exclude_dirs: Iterable[str] = DEFAULT_EXCLUDE_DIRS,
    filename: str = IGNORE_FILE_NAME,
) -> set[str]:
    """已确认不可见的微博集合 = `已不可见/` 等目录 + `ignore_ids.txt` 人工忽略清单。"""
    return collect_known_invisible_ids(user_dir, exclude_dirs) | collect_ignore_file_ids(
        user_dir, filename
    )


def filter_ids_in_range(
    ids: dict[str, datetime | None],
    start: datetime,
    end: datetime,
) -> dict[str, datetime | None]:
    """按 [start, end] 过滤 {id: 日期}；无日期条目视为检查范围外。"""
    return {
        wid: dt
        for wid, dt in ids.items()
        if dt is not None and start <= dt <= end
    }


def canonical_marker(weibo_id: str | int) -> str:
    """程序写入时统一使用的 marker 格式。"""
    return f"<!-- weibo_id: {weibo_id} -->"
