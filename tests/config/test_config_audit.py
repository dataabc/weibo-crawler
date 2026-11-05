'scripts/weibo_config_audit.py 单元测试。'

import os
import sys
import unittest

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT_DIR, "scripts"))

import weibo_config_audit as audit  # noqa: E402


class ConfigAuditTest(unittest.TestCase):
    def test_classifies_internal_and_real_keys(self):
        code_keys = {
            "since_date": ["weibo.py:1"],
            "user_id": ["weibo.py:2"],
            "invisible_archive_dir": ["weibo.py:3"],
        }
        result = audit.build_audit(code_keys, ["since_date"])
        self.assertEqual(result["code_only_real"], ["invisible_archive_dir"])
        self.assertIn("user_id", result["code_only_internal"])

    def test_collect_code_keys_finds_get_calls(self):
        with self._tmp_project() as tmp:
            path = os.path.join(tmp, "weibo.py")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write('a = config.get("since_date", "x")\nb = config.get("x")\n')
            keys = audit.collect_code_keys([path])
            self.assertIn("since_date", keys)

    def _tmp_project(self):
        import tempfile

        return tempfile.TemporaryDirectory()


if __name__ == "__main__":
    unittest.main()
