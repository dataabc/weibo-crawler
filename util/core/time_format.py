"""时间格式常量与标题时间解析的单一来源。

- ``DTFORMAT``：时间格式的单一来源，避免报告/核对/回填口径不一致。
- ``parse_heading_datetime``：微博块标题时间解析的统一实现，兼容
  ``markdown_blocks._parse_heading`` 与 ``archive_index.parse_heading_datetime``
  的格式集合，调用方各自决定是否只接受规范格式。
"""

from __future__ import annotations

from datetime import datetime

DTFORMAT = "%Y-%m-%dT%H:%M:%S"


def parse_heading_datetime(text: str, fallback_date: datetime | None) -> datetime | None:
    """解析微博块标题里的时间（不含 ``###`` 前缀）。

    支持的格式（按优先级）：

    - ``HH:MM:SS``：需 ``fallback_date``（取自文件名 ``YYYY-MM-DD.md``）；
    - ``YYYY-MM-DD HH:MM:SS`` / ``YYYY-MM-DD HH:MM`` / ``YYYY-MM-DD``；
    - 兜底：文本前 19 位按 ``YYYY-MM-DD HH:MM:SS`` 解析。

    解析失败返回 None。
    """
    text = (text or "").strip()
    if not text:
        return None
    if len(text) == 8 and text.count(":") == 2 and fallback_date is not None:
        try:
            t = datetime.strptime(text, "%H:%M:%S")
        except ValueError:
            return None
        return fallback_date.replace(hour=t.hour, minute=t.minute, second=t.second)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    if len(text) >= 19:
        try:
            return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    return None
