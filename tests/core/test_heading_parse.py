'标题时间解析的统一实现与两边能力集差异。'

import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests import _support  # noqa: E402

_support.bootstrap()

from util.core import archive_index, markdown_blocks, time_format  # noqa: E402


class TimeFormatParserTest(unittest.TestCase):
    """统一实现本身支持的格式。"""

    def test_time_only_with_fallback_date(self):
        fallback = datetime(2024, 6, 1)
        self.assertEqual(
            time_format.parse_heading_datetime("10:20:30", fallback),
            datetime(2024, 6, 1, 10, 20, 30),
        )

    def test_time_only_without_fallback_returns_none(self):
        self.assertIsNone(time_format.parse_heading_datetime("10:20:30", None))

    def test_full_datetime(self):
        self.assertEqual(
            time_format.parse_heading_datetime("2024-06-02 10:20:30", None),
            datetime(2024, 6, 2, 10, 20, 30),
        )

    def test_minute_precision(self):
        self.assertEqual(
            time_format.parse_heading_datetime("2024-06-02 10:20", None),
            datetime(2024, 6, 2, 10, 20),
        )

    def test_date_only(self):
        self.assertEqual(
            time_format.parse_heading_datetime("2024-06-02", None),
            datetime(2024, 6, 2),
        )

    def test_garbage_returns_none(self):
        for text in ("", "   ", "不是日期", "2024/06/02"):
            self.assertIsNone(time_format.parse_heading_datetime(text, None))


class MarkdownBlocksStrictTest(unittest.TestCase):
    """markdown_blocks 只认规范块标题。"""

    def _parse(self, heading, fallback="2024-06-01"):
        return markdown_blocks._parse_heading(heading, fallback)

    def test_time_only_heading_uses_fallback(self):
        created, dt = self._parse("### 10:20:30")
        self.assertEqual(created, "2024-06-01T10:20:30")
        self.assertEqual(dt, datetime(2024, 6, 1, 10, 20, 30))

    def test_full_heading_with_space(self):
        created, dt = self._parse("### 2024-06-02 10:20:30")
        self.assertEqual(created, "2024-06-02T10:20:30")
        self.assertEqual(dt, datetime(2024, 6, 2, 10, 20, 30))

    def test_full_heading_with_t_separator(self):
        created, dt = self._parse("### 2024-06-02T10:20:30")
        self.assertEqual(created, "2024-06-02T10:20:30")
        self.assertEqual(dt, datetime(2024, 6, 2, 10, 20, 30))

    def test_minute_precision_rejected(self):
        self.assertEqual(self._parse("### 2024-06-02 10:20"), (None, None))

    def test_date_only_rejected(self):
        self.assertEqual(self._parse("### 2024-06-02"), (None, None))

    def test_bad_fallback_returns_none(self):
        self.assertEqual(self._parse("### 10:20:30", fallback="bad-date"), (None, None))


class ArchiveIndexLenientTest(unittest.TestCase):
    'archive_index 保持宽容能力。'

    def test_time_only(self):
        self.assertEqual(
            archive_index.parse_heading_datetime("10:20:30", datetime(2024, 6, 1)),
            datetime(2024, 6, 1, 10, 20, 30),
        )

    def test_full_and_minute_and_date_only(self):
        self.assertEqual(
            archive_index.parse_heading_datetime("2024-06-02 10:20:30", None),
            datetime(2024, 6, 2, 10, 20, 30),
        )
        self.assertEqual(
            archive_index.parse_heading_datetime("2024-06-02 10:20", None),
            datetime(2024, 6, 2, 10, 20),
        )
        self.assertEqual(
            archive_index.parse_heading_datetime("2024-06-02", None),
            datetime(2024, 6, 2),
        )

    def test_unknown_format_returns_none(self):
        self.assertIsNone(archive_index.parse_heading_datetime("不是时间", None))

    def test_block_parse_uses_same_baseline(self):
        """``parse_markdown`` 的块时间应与 markdown_blocks 门控一致。"""
        text = (
            "## 2024-06-01\n\n"
            "### 10:20:30\n<!-- weibo_id: 100 -->\n正文\n\n---\n\n"
            "### 2024-06-02 10:20:30\n<!-- weibo_id: 200 -->\n正文\n\n---\n"
        )
        doc = markdown_blocks.parse_markdown(text, fallback_date="2024-06-01")
        self.assertEqual(doc.find("100").created_at, "2024-06-01T10:20:30")
        self.assertEqual(doc.find("200").created_at, "2024-06-02T10:20:30")


if __name__ == "__main__":
    unittest.main()
