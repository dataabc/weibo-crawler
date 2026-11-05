'util/config_schema.py 单元测试。'

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from util.config import config_schema  # noqa: E402


class ConfigSchemaTest(unittest.TestCase):
    def test_valid_config(self):
        config = {
            "since_date": "2024-01-01",
            "write_mode": ["markdown", "json"],
            "markdown_split_by": "day_by_month",
            "comment_refresh_scope": "all",
            "comment_refresh_limit": 300,
            "only_crawl_original": 1,
        }
        self.assertEqual(config_schema.validate_config(config), [])

    def test_invalid_choices(self):
        issues = config_schema.validate_config(
            {"markdown_split_by": "week", "markdown_sort_order": "random"}
        )
        self.assertTrue(any("markdown_split_by" in issue for issue in issues))
        self.assertTrue(any("markdown_sort_order" in issue for issue in issues))

    def test_invalid_write_mode_and_limit(self):
        issues = config_schema.validate_config(
            {"write_mode": ["markdown", "excel"], "comment_refresh_limit": -1}
        )
        self.assertTrue(any("write_mode" in issue for issue in issues))
        self.assertTrue(any("comment_refresh_limit" in issue for issue in issues))

    def test_unknown_keys_ignored(self):
        self.assertEqual(config_schema.validate_config({"some_legacy_key": 1}), [])

    def test_apply_defaults(self):
        result = config_schema.apply_defaults({"since_date": "2024-01-01"})
        self.assertEqual(result["markdown_split_by"], "day")
        self.assertEqual(result["comment_refresh_limit"], 300)
