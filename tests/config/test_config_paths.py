'util/config_paths.py 单元测试。'

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from util.config import config_paths  # noqa: E402
from util.core import file_lock  # noqa: E402


class ConfigPathsTest(unittest.TestCase):
    def test_safe_dir_name(self):
        self.assertEqual(config_paths.safe_dir_name('a/b\\c:d*e'), "a_b_c_d_e".replace("_e", "_e"))
        self.assertEqual(config_paths.safe_dir_name(""), "unknown")

    def test_resolve_output_root_relative_and_absolute(self):
        self.assertEqual(
            file_lock.resolve_output_dir("/proj", "weibo_data"),
            "/proj/weibo_data",
        )
        self.assertEqual(
            file_lock.resolve_output_dir("/proj", "/data/out"), "/data/out"
        )

    def test_account_dir_name_modes(self):
        user = {"screen_name": "测试用户"}
        cfg = {"user_id": "123"}
        self.assertEqual(
            config_paths.account_dir_name(user, cfg, 0), "测试用户"
        )
        self.assertEqual(config_paths.account_dir_name(user, cfg, 1), "123")
        self.assertEqual(config_paths.account_dir_name({}, cfg, 0), "123")
        self.assertEqual(config_paths.account_dir_name({}, {}, 0), "unknown")

    def test_report_dir_relative(self):
        path = config_paths.report_dir(
            "/proj", "weibo_data", "report", "测试用户"
        )
        self.assertEqual(path, os.path.join("/proj/weibo_data/report/测试用户"))

    def test_report_dir_absolute_and_default(self):
        path = config_paths.report_dir(
            "/proj", "weibo_data", "/abs/report", "测试用户"
        )
        self.assertEqual(path, "/abs/report/测试用户")
        fallback = config_paths.report_dir(
            "/proj", "weibo_data", "", "测试用户"
        )
        self.assertEqual(fallback, os.path.join("/proj/weibo_data/report/测试用户"))


    def test_config_files_prefers_config_dir(self):
        """config/ 下有文件时优先用 config/ 下的路径。"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "config"))
            open(os.path.join(tmp, "config", "config.json"), "w").close()
            open(os.path.join(tmp, "config", "config.json.example"), "w").close()
            config_path, example_path = config_paths.config_files(tmp)
            self.assertEqual(config_path, os.path.join(tmp, "config", "config.json"))
            self.assertEqual(
                example_path, os.path.join(tmp, "config", "config.json.example")
            )

    def test_config_files_falls_back_to_legacy_root(self):
        """config/ 下缺失时回退根目录旧位置（兼容旧部署）。"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "config.json"), "w").close()
            open(os.path.join(tmp, "config.json.example"), "w").close()
            config_path, example_path = config_paths.config_files(tmp)
            self.assertEqual(config_path, os.path.join(tmp, "config.json"))
            self.assertEqual(example_path, os.path.join(tmp, "config.json.example"))

    def test_resolve_config_file_prefers_config_dir(self):
        """纯文件名优先 config/ 下（配置相关文件统一放 config/）。"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "config"))
            open(os.path.join(tmp, "config", "comment_user_filter.txt"), "w").close()
            self.assertEqual(
                config_paths.resolve_config_file(tmp, "comment_user_filter.txt"),
                os.path.join(tmp, "config", "comment_user_filter.txt"),
            )

    def test_resolve_config_file_falls_back_to_root(self):
        """config/ 下没有时回退项目根（兼容旧部署）。"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                config_paths.resolve_config_file(tmp, "cookie.txt"),
                os.path.join(tmp, "cookie.txt"),
            )

    def test_resolve_config_file_keeps_relative_dir(self):
        """带目录的相对路径按项目根解析（如 config/user_id_list.txt）。"""
        self.assertEqual(
            config_paths.resolve_config_file("/proj", "config/user_id_list.txt"),
            "/proj/config/user_id_list.txt",
        )
        self.assertEqual(
            config_paths.resolve_config_file("/proj", "data/user_id_list.txt"),
            "/proj/data/user_id_list.txt",
        )

    def test_resolve_config_file_absolute_and_empty(self):
        self.assertEqual(
            config_paths.resolve_config_file("/proj", "/abs/c.txt"), "/abs/c.txt"
        )
        self.assertEqual(config_paths.resolve_config_file("/proj", ""), "")

    def test_config_files_defaults_to_config_dir_when_missing(self):
        """两处都不存在时返回 config/ 路径（由调用方决定报错）。"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            config_path, example_path = config_paths.config_files(tmp)
            self.assertEqual(config_path, os.path.join(tmp, "config", "config.json"))
            self.assertEqual(
                example_path, os.path.join(tmp, "config", "config.json.example")
            )


if __name__ == "__main__":
    unittest.main()
