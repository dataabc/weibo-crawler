"""Weibo.get_filepath 的健壮性测试。"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests import _support  # noqa: E402

_support.bootstrap()

import weibo  # noqa: E402


class GetFilepathTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wb = weibo.Weibo.__new__(weibo.Weibo)
        self.wb.user = {}
        self.wb.user_config = {"user_id": "1234567890"}
        self.wb.user_id_as_folder_name = 0
        self.wb.readonly_mode = 1  # 只读：get_filepath 不创建目录
        self.wb._output_root_dir = lambda: self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_falls_back_to_user_id_when_user_missing(self):
        path = self.wb.get_filepath("markdown")
        self.assertTrue(path.endswith("1234567890"), path)
        self.assertTrue(path.startswith(self._tmp.name), path)

    def test_uses_screen_name_when_available(self):
        self.wb.user = {"screen_name": "测试用户"}
        path = self.wb.get_filepath("markdown")
        self.assertEqual(path, os.path.join(self._tmp.name, "测试用户"))

    def test_user_id_folder_mode(self):
        self.wb.user = {"screen_name": "测试用户"}
        self.wb.user_id_as_folder_name = 1
        path = self.wb.get_filepath("markdown")
        self.assertEqual(path, os.path.join(self._tmp.name, "1234567890"))


if __name__ == "__main__":
    unittest.main()
