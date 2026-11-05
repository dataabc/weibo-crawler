'scripts/weibo_config_diagnostics.py 单元测试。'

import os
import sys
import unittest

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT_DIR, "scripts"))

import weibo_config_diagnostics as diagnostics  # noqa: E402


class ConfigDiagnosticsTest(unittest.TestCase):
    def test_healthy_config(self):
        text = diagnostics.build_diagnostics(
            {"since_date": "2024-01-01", "write_mode": ["markdown"]},
            ["since_date", "write_mode"],
            {"code_key_count": 2, "example_key_count": 2, "code_only_real": []},
        )
        self.assertIn("配置校验问题: 0 个", text)
        self.assertIn("配置健康，可正常运行", text)

    def test_invalid_config_reports_problem(self):
        text = diagnostics.build_diagnostics(
            {"markdown_split_by": "week"},
            ["markdown_split_by"],
            {"code_key_count": 1, "example_key_count": 1, "code_only_real": []},
        )
        self.assertIn("markdown_split_by", text)
        self.assertIn("存在配置取值问题", text)
