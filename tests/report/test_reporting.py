'util/reporting.py 单元测试。'

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from util.report import reporting  # noqa: E402


class ReportingTest(unittest.TestCase):
    def test_safe_name_replaces_illegal_chars(self):
        self.assertEqual(
            reporting.safe_name('a/b\\c:d*e?f"g<h>i|j'), "a_b_c_d_e_f_g_h_i_j"
        )
        self.assertEqual(reporting.safe_name(""), "unknown")
        self.assertEqual(reporting.safe_name(None), "unknown")

    def test_report_filename_format(self):
        name = reporting.report_filename(
            reporting.PREFIX_CRAWL_SUMMARY,
            "1234567890",
            "测试用户",
            timestamp="20261026_120000",
        )
        self.assertEqual(
            name, "weibo_crawl_summary_1234567890_测试用户_20261026_120000.txt"
        )

    def test_total_report_filename(self):
        name = reporting.total_report_filename(
            reporting.PREFIX_CRAWL_SUMMARY, ext="md", timestamp="20261026_120000"
        )
        self.assertEqual(name, "weibo_crawl_summary_total_20261026_120000.md")

    def test_report_timestamp_format(self):
        stamp = reporting.report_timestamp()
        self.assertEqual(len(stamp), 15)
        self.assertEqual(stamp[8], "_")


if __name__ == "__main__":
    unittest.main()
