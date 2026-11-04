"""媒体完整性报告：扫描 markdown 里的本地媒体链接，检查文件是否存在/非空/明显损坏。

纯读取模块（只读 markdown 与媒体文件，不联网、不写文件）；写报告由 weibo.py 负责。
覆盖范围：用户活跃存档 markdown（排除「已不可见」等目录）里的图片/视频/Live Photo
链接，以及评论块（``<!-- comments: ... -->`` … ``<!-- /comments -->``）里的评论图片。
不覆盖：平台侧已删除的资源、外部链接。
"""

from __future__ import annotations

import bisect
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable
from urllib.parse import unquote

from util.core import archive_index
from util.media import media_check
from util.comment.comment_markdown import COMMENT_END, COMMENT_START_RE

# marker/标题正则与媒体扩展名复用单一来源
MARKER_RE = archive_index.MARKER_RE
HEADING_RE = archive_index.HEADING_RE
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
DISPLAY_DT = "%Y-%m-%d %H:%M:%S"
MEDIA_EXTENSIONS = media_check.IMAGE_EXTENSIONS | media_check.VIDEO_EXTENSIONS


@dataclass
class MediaIssue:
    """一个媒体链接的问题。"""

    kind: str  # missing / empty / invalid
    md_path: str
    link: str
    resolved_path: str
    weibo_id: str | None = None
    created_at: datetime | None = None
    reason: str = ""
    # 链接位于评论块内（评论图片）：报告单独标注
    is_comment: bool = False


@dataclass
class MediaScanResult:
    files_scanned: int = 0
    links_total: int = 0
    ok_count: int = 0
    issues: list[MediaIssue] = field(default_factory=list)
    files_with_issues: set[str] = field(default_factory=set)

    @property
    def issue_count(self) -> int:
        return len(self.issues)

    def count(self, kind: str) -> int:
        return sum(1 for issue in self.issues if issue.kind == kind)

    @property
    def comment_issue_count(self) -> int:
        return sum(1 for issue in self.issues if issue.is_comment)


def _iter_links_with_context(
    text: str, file_date: datetime | None
) -> list[tuple[str, str | None, datetime | None, int]]:
    """返回 [(原始链接, weibo_id, 发布日期, 位置)]；日期取链接前最近的标题。

    每个链接用 bisect 在前缀位置表里定位「最近的前一个 marker/标题」，
    避免对每个链接从头线性回溯。
    """
    markers = [(m.start(), m.group(1)) for m in MARKER_RE.finditer(text)]
    headings = [(m.start(), m.group(1)) for m in HEADING_RE.finditer(text)]
    marker_positions = [pos for pos, _ in markers]
    heading_positions = [pos for pos, _ in headings]
    results: list[tuple[str, str | None, datetime | None, int]] = []

    for match in LINK_RE.finditer(text):
        pos = match.start()
        raw_link = match.group(1).strip().strip("<>").strip()
        marker_index = bisect.bisect_left(marker_positions, pos) - 1
        weibo_id = markers[marker_index][1] if marker_index >= 0 else None
        heading_index = bisect.bisect_left(heading_positions, pos) - 1
        heading = headings[heading_index][1] if heading_index >= 0 else None
        dt = (
            archive_index.parse_heading_datetime(heading, file_date)
            if heading is not None
            else file_date
        )
        results.append((raw_link, weibo_id, dt, pos))
    return results


def _normalize_link(link: str) -> str | None:
    """返回本地媒体路径（可能是相对路径）；外部链接/非媒体返回 None。"""
    value = link.strip()
    if not value:
        return None
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1].strip()
    lower = value.lower()
    if lower.startswith(("http://", "https://", "data:", "mailto:", "#")):
        return None
    value = value.split("#", 1)[0].strip()
    if not value:
        return None
    value = unquote(value)
    ext = os.path.splitext(value.split("?", 1)[0])[1].lower()
    if ext not in MEDIA_EXTENSIONS:
        return None
    return value


def _resolve(md_path: str, link: str) -> str:
    if os.path.isabs(link):
        return os.path.normpath(link)
    return os.path.normpath(os.path.join(os.path.dirname(md_path), link))


def _scan_md_file_full(md_path: str) -> tuple[list[MediaIssue], int, int]:
    """扫描单个 markdown 文件，返回 (问题列表, 媒体链接总数, 正常数)。"""
    try:
        text = archive_index.read_md_text(md_path)
    except OSError:
        return [], 0, 0
    file_date = archive_index.file_date_from_name(md_path)
    # 评论块区间：评论图片同样纳入检查
    comment_spans = []
    for start_match in COMMENT_START_RE.finditer(text):
        end = text.find(COMMENT_END, start_match.end())
        comment_spans.append(
            (start_match.start(), end if end != -1 else len(text))
        )
    # 链接位置递增：用 bisect 定位候选 span，避免每个链接扫全部评论块
    # 。
    comment_spans.sort()
    comment_starts = [start for start, _end in comment_spans]

    def _in_comment(pos: int) -> bool:
        index = bisect.bisect_right(comment_starts, pos) - 1
        # 同时检查 index 与 index-1，容忍极端的嵌套/重叠区间
        for candidate in (index, index - 1):
            if 0 <= candidate < len(comment_spans):
                start, end = comment_spans[candidate]
                if start <= pos <= end:
                    return True
        return False

    issues: list[MediaIssue] = []
    links_total = 0
    ok_count = 0
    for raw_link, weibo_id, dt, pos in _iter_links_with_context(text, file_date):
        link = _normalize_link(raw_link)
        if link is None:
            continue
        links_total += 1
        resolved = _resolve(md_path, link)
        is_comment = _in_comment(pos)
        if not os.path.exists(resolved):
            issues.append(
                MediaIssue(
                    "missing", md_path, link, resolved, weibo_id, dt, "文件不存在",
                    is_comment=is_comment,
                )
            )
            continue
        if os.path.isdir(resolved):
            issues.append(
                MediaIssue(
                    "invalid", md_path, link, resolved, weibo_id, dt, "路径是目录",
                    is_comment=is_comment,
                )
            )
            continue
        ok, reason = media_check.validate_file(resolved)
        if not ok:
            kind = "empty" if "空" in reason else "invalid"
            issues.append(
                MediaIssue(
                    kind, md_path, link, resolved, weibo_id, dt, reason,
                    is_comment=is_comment,
                )
            )
        else:
            ok_count += 1
    return issues, links_total, ok_count



def scan_user_media(
    user_dir: str,
    exclude_dirs: Iterable[str] = archive_index.DEFAULT_EXCLUDE_DIRS,
) -> MediaScanResult:
    """扫描一个用户目录下全部活跃 markdown 的本地媒体链接（含评论图片）。"""
    result = MediaScanResult()
    for md_path in archive_index.iter_active_md_paths(user_dir, exclude_dirs):
        result.files_scanned += 1
        issues, links_total, ok_count = _scan_md_file_full(md_path)
        result.links_total += links_total
        result.ok_count += ok_count
        if issues:
            result.files_with_issues.add(md_path)
            result.issues.extend(issues)
    return result


def _fmt_dt(dt: datetime | None) -> str:
    return dt.strftime(DISPLAY_DT) if dt else "未知"


def _rel(path: str, base_dir: str | None) -> str:
    if not base_dir:
        return path
    try:
        return os.path.relpath(path, base_dir)
    except ValueError:
        return path


def render_media_report(
    user_id: str,
    screen_name: str,
    result: MediaScanResult,
    output_root: str | None = None,
    crawl_success: bool = True,
    crawl_reason: str = "",
    generated_at: datetime | None = None,
    download_urls: dict[str, tuple[str, str]] | None = None,
    attributions: dict[str, str] | None = None,
) -> str:
    """渲染人可读的媒体完整性报告（txt）。"""
    generated_at = generated_at or datetime.now()
    sep = "=" * 60
    lines: list[str] = ["媒体完整性报告"]
    lines.append(f"用户: {screen_name} (ID: {user_id})")
    lines.append(f"生成时间: {generated_at.strftime(DISPLAY_DT)}")
    status = "是" if crawl_success else "否"
    if crawl_reason:
        status += f"（{crawl_reason}）"
    lines.append(f"本轮抓取是否顺利完成: {status}")
    lines.append(
        "检查范围: 用户目录下全部活跃 markdown（排除已确认不可见的微博目录），"
        "含微博媒体与评论块中的评论图片"
    )
    lines.append(
        f"扫描文件: {result.files_scanned} 个；媒体链接: {result.links_total} 条；"
        f"正常: {result.ok_count} 条；问题: {result.issue_count} 条"
        f"（其中评论图片问题: {result.comment_issue_count} 条）"
    )
    lines.append(
        "说明: 只检查 markdown 里引用的本地媒体（含评论图片）；缺失条目已做在线探测归因"
        "（平台侧已删除 / 本地缺失仍可下载 / 平台拒绝访问 / 无法确认）。"
    )
    lines.append(sep)

    sections = (
        ("一、缺失的媒体文件", "missing", "文件不存在"),
        ("二、空文件（0 字节）", "empty", "文件为空"),
        ("三、损坏或异常文件", "invalid", "文件头/尾部校验失败"),
    )
    for title, kind, default_reason in sections:
        items = [issue for issue in result.issues if issue.kind == kind]
        lines.append(f"{title}: {len(items)} 条")
        lines.append("")
        if not items:
            lines.append("  （无）")
            lines.append("")
            continue
        for issue in items:
            if issue.weibo_id:
                lines.append(f"  https://m.weibo.cn/detail/{issue.weibo_id}")
            else:
                lines.append("  （无法确定微博 ID）")
            lines.append(f"    发布日期: {_fmt_dt(issue.created_at)}")
            lines.append(f"    引用文件: {_rel(issue.md_path, output_root)}")
            lines.append(f"    本地路径: {_rel(issue.resolved_path, output_root)}")
            if download_urls and kind == "missing":
                info = download_urls.get(
                    os.path.abspath(issue.resolved_path)
                ) or download_urls.get(issue.resolved_path)
                if info:
                    lines.append(
                        f"    下载来源: 微博ID {info[0]} | 原始URL {info[1]}"
                    )
            if attributions and kind == "missing":
                attr = attributions.get(issue.resolved_path) or attributions.get(
                    os.path.abspath(issue.resolved_path)
                )
                if attr:
                    lines.append(f"    归因: {attr}")
            if issue.is_comment:
                lines.append("    来源: 评论图片（评论块内引用）")
            lines.append(f"    原因: {issue.reason or default_reason}")
            lines.append("")
        lines.append(sep)

    lines.append("统计（便于机器读取）")
    lines.append(f"crawl_success: {'true' if crawl_success else 'false'}")
    lines.append(f"files_scanned: {result.files_scanned}")
    lines.append(f"media_links: {result.links_total}")
    lines.append(f"media_ok: {result.ok_count}")
    lines.append(f"media_missing: {result.count('missing')}")
    if attributions:
        # 缺失项归因统计（便于机器读取与长期趋势）
        from util.media import media_probe

        for label, value in (
            ("media_missing_platform_deleted", media_probe.ATTRIBUTION_PLATFORM_DELETED),
            ("media_missing_recoverable", media_probe.ATTRIBUTION_RECOVERABLE),
            ("media_missing_restricted", media_probe.ATTRIBUTION_RESTRICTED),
            ("media_missing_unknown", media_probe.ATTRIBUTION_UNKNOWN),
        ):
            lines.append(
                f"{label}: {sum(1 for item in attributions.values() if item == value)}"
            )
    lines.append(f"media_empty: {result.count('empty')}")
    lines.append(f"media_invalid: {result.count('invalid')}")
    lines.append(f"files_with_issues: {len(result.files_with_issues)}")
    lines.append("报告完毕。")
    return "\n".join(lines) + "\n"
