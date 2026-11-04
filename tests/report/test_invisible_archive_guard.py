"""隔离区审核的「未爬取则跳过」测试。"""

import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests import _support  # noqa: E402

_support.bootstrap()

from util.report import invisible_archive  # noqa: E402


def _fake_wb(attempted: bool, success: bool):
    wb = types.SimpleNamespace()
    wb.invisible_archive_enabled = 1
    wb.user_config = {}
    wb.crawl_success = {"attempted": attempted, "success": success}
    wb.get_filepath = mock.Mock(side_effect=AssertionError("未爬取时不应调用 get_filepath"))
    return wb


class InvisibleArchiveGuardTest(unittest.TestCase):
    def test_skips_when_not_attempted(self):
        wb = _fake_wb(attempted=False, success=True)
        invisible_archive.review_invisible_archive(wb)
        wb.get_filepath.assert_not_called()

    def test_skips_when_not_success(self):
        wb = _fake_wb(attempted=True, success=False)
        invisible_archive.review_invisible_archive(wb)
        wb.get_filepath.assert_not_called()

    def test_disabled_by_config(self):
        wb = _fake_wb(attempted=True, success=True)
        wb.invisible_archive_enabled = 0
        invisible_archive.review_invisible_archive(wb)
        wb.get_filepath.assert_not_called()


if __name__ == "__main__":
    unittest.main()
