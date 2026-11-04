"""规则：报告根目录保持不变（配置项语义不变），在根目录下按用户建子目录：
``<报告根>/<用户目录名>/``，用户目录名与用户数据目录一致
（``user_id_as_folder_name=0`` 用昵称，``=1`` 用用户 ID；非法字符替换为下划线）。"""

import os
import tempfile
import unittest


from tests import _support  # noqa: E402

_support.bootstrap()

import weibo  # noqa: E402


def _wb(output_dir, screen_name="测试用户", user_id="123", user_id_as_folder_name=0):
    wb = weibo.Weibo.__new__(weibo.Weibo)
    wb.output_directory = str(output_dir)
    wb.user = {"screen_name": screen_name}
    wb.user_config = {"user_id": user_id}
    wb.user_id_as_folder_name = user_id_as_folder_name
    return wb


class ReportUserDirTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_default_uses_screen_name(self) -> None:
        wb = _wb(self.root)
        path = wb._report_output_dir("report")
        self.assertEqual(path, os.path.join(self.root, "report", "测试用户"))

    def test_user_id_as_folder_name(self) -> None:
        wb = _wb(self.root, user_id_as_folder_name=1)
        path = wb._report_output_dir("report")
        self.assertEqual(path, os.path.join(self.root, "report", "123"))

    def test_illegal_filename_chars_replaced(self) -> None:
        wb = _wb(self.root, screen_name='a/b:c*?"<>|')
        self.assertEqual(wb._user_report_dir_name(), "a_b_c______")

    def test_absolute_report_root_gets_user_subdir(self) -> None:
        wb = _wb(self.root)
        abs_root = os.path.join(self.root, "abs_reports")
        path = wb._report_output_dir(abs_root)
        self.assertEqual(path, os.path.join(abs_root, "测试用户"))

    def test_empty_nickname_falls_back_to_user_id(self) -> None:
        wb = _wb(self.root, screen_name="")
        self.assertEqual(wb._user_report_dir_name(), "123")


if __name__ == "__main__":
    unittest.main()
