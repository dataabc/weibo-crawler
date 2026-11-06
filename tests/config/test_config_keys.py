"""配置键覆盖与 validate 分支测试。

两件事：
1. **键级漂移检测**：`config/config.json.example` 的顶层键必须被 `apply()` 读取
   （脚本级键与内部注入键在白名单里），反之 `apply()` 读取的键必须在模板中有据可查。
   这样可以拦住「新增配置键只改了一处」的漂移。
2. **validate 分支**：35 处 sys.exit 中挑出可稳定构造的分支逐个覆盖，"""

import ast
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests import _support  # noqa: E402

_support.bootstrap()

import weibo  # noqa: E402
from util.config import config_loader, config_migrate  # noqa: E402

EXAMPLE_PATH = os.path.join(_support.PROJECT_ROOT, "config", "config.json.example")
LOADER_PATH = os.path.join(_support.PROJECT_ROOT, "util", "config", "config_loader.py")

#: 由 scripts/ 直接读取、不经 apply() 的脚本级键
SCRIPT_LEVEL_KEYS = {
    "weibo_archive_check_full",
    "weibo_comment_backfill",
    "weibo_comment_backfill_dir",
}
#: apply() 读取但不对用户公开的键（内部注入 / 未验证功能，见 docs/未验证功能.md）
INTERNAL_KEYS = {
    "_readonly_mode",
    "llm_config",
}


class _FakeCookieJar:
    def __init__(self):
        self.data = {}

    def update(self, other):
        self.data.update(other or {})

    def __iter__(self):
        return iter([])


class _FakeSession:
    """最小 Session：满足 apply() 的 cookies/mount/get 调用，不联网。"""

    def __init__(self, *args, **kwargs):
        self.cookies = _FakeCookieJar()
        self.headers = {}
        self.mounted = {}

    def get(self, *args, **kwargs):
        raise OSError("测试环境不联网")

    def mount(self, prefix, adapter):
        self.mounted[prefix] = adapter


def template_config():
    config = _support.load_json5_file(EXAMPLE_PATH)
    config["user_id_list"] = ["1234567890"]
    config["cookie"] = ""
    config.pop("llm_config", None)
    return config


def example_top_level_keys():
    return set(config_migrate.load_config_file(EXAMPLE_PATH).keys())


def apply_read_keys():
    """从源码静态提取 apply() 读取的顶层配置键。"""
    src = open(LOADER_PATH, encoding="utf-8").read()
    tree = ast.parse(src)
    apply_fn = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "apply"
    )
    keys = set()
    for node in ast.walk(apply_fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            is_config_get = (
                node.func.attr == "get"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "config"
            )
            if is_config_get and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    keys.add(first.value)
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == "config"
        ):
            slice_node = node.slice
            if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
                keys.add(slice_node.value)
    return keys


class ConfigKeyDriftTest(unittest.TestCase):
    """模板与 apply 的键集合互相对账。"""

    def test_every_template_key_is_read_by_apply(self):
        unread = example_top_level_keys() - apply_read_keys() - SCRIPT_LEVEL_KEYS
        self.assertEqual(
            unread,
            set(),
            f"config.json.example 里这些键没有被 apply() 读取（模板漂移）：{sorted(unread)}",
        )

    def test_every_apply_key_is_documented_in_template(self):
        undocumented = apply_read_keys() - example_top_level_keys() - INTERNAL_KEYS
        self.assertEqual(
            undocumented,
            set(),
            f"apply() 读取了模板中不存在的键（用户不可发现）：{sorted(undocumented)}",
        )


class ValidateBranchTest(unittest.TestCase):
    """validate() 的拒绝分支。"""

    def setUp(self):
        self.wb = weibo.Weibo.__new__(weibo.Weibo)
        self.config = template_config()

    def _assert_exit(self, config):
        with self.assertRaises(SystemExit):
            config_loader.validate(self.wb, config)

    def test_invalid_timestamp_prefix_format_rejected(self):
        self.config["timestamp_prefix_format"] = "%Y/%m/%d"
        self._assert_exit(self.config)

    def test_accepts_template(self):
        config_loader.validate(self.wb, self.config)  # 不抛异常即通过

    def test_zero_one_argument_must_be_0_or_1(self):
        self.config["only_crawl_original"] = 2
        self._assert_exit(self.config)

    def test_report_dir_must_be_string(self):
        self.config["weibo_archive_check_dir"] = ["report"]
        self._assert_exit(self.config)

    def test_invisible_dirs_must_be_list(self):
        self.config["invisible_weibo_dirs"] = "已不可见"
        self._assert_exit(self.config)

    def test_invisible_dirs_entries_must_be_strings(self):
        self.config["invisible_weibo_dirs"] = [1, 2]
        self._assert_exit(self.config)

    def test_query_list_must_be_list_or_str(self):
        self.config["query_list"] = {"a": 1}
        self._assert_exit(self.config)

    def test_page_weibo_count_must_be_int(self):
        self.config["page_weibo_count"] = "10"
        self._assert_exit(self.config)

    def test_page_weibo_count_must_be_in_range(self):
        self.config["page_weibo_count"] = 101
        self._assert_exit(self.config)

    def test_comment_max_requests_must_be_in_range(self):
        self.config["comment_max_requests"] = 0
        self._assert_exit(self.config)

    def test_write_mode_must_be_list(self):
        self.config["write_mode"] = "markdown"
        self._assert_exit(self.config)

    def test_unknown_write_mode_rejected(self):
        self.config["write_mode"] = ["unknown_mode"]
        self._assert_exit(self.config)

    def test_append_mode_requires_sqlite(self):
        self.config["write_mode"] = ["markdown"]
        with mock.patch.object(config_loader.const, "MODE", "append"):
            self._assert_exit(self.config)

    def test_append_mode_with_sqlite_accepted(self):
        self.config["write_mode"] = ["markdown", "sqlite"]
        with mock.patch.object(config_loader.const, "MODE", "append"):
            config_loader.validate(self.wb, self.config)

    def test_markdown_split_by_invalid(self):
        self.config["markdown_split_by"] = "week"
        self._assert_exit(self.config)

    def test_markdown_image_order_invalid(self):
        self.config["markdown_image_order"] = "random"
        self._assert_exit(self.config)

    def test_markdown_sort_order_invalid(self):
        self.config["markdown_sort_order"] = "random"
        self._assert_exit(self.config)

    def test_since_date_invalid(self):
        self.config["since_date"] = "不是日期"
        self._assert_exit(self.config)

    def test_end_date_invalid(self):
        self.config["end_date"] = "2024-13-45"
        self._assert_exit(self.config)


class ApplyKeySnapshotTest(unittest.TestCase):
    """apply() 的「配置键 → 属性」关键映射快照。"""

    def setUp(self):
        self._saved_session = sys.modules["requests"].Session
        sys.modules["requests"].Session = _FakeSession
        self.wb = weibo.Weibo.__new__(weibo.Weibo)
        self.config = template_config()

    def tearDown(self):
        sys.modules["requests"].Session = self._saved_session

    def test_apply_maps_report_and_archive_keys(self):
        config_loader.apply(self.wb, self.config)
        for attr in (
            "archive_check_enabled",
            "crawl_summary_enabled",
            "media_check_enabled",
            "invisible_archive_enabled",
        ):
            self.assertTrue(hasattr(self.wb, attr), attr)
        self.assertEqual(self.wb.archive_check_dir, self.config["weibo_archive_check_dir"])

    def test_apply_wires_change_report_dir(self):
        ""
        self.config["weibo_change_report_dir"] = "custom_reports"
        config_loader.apply(self.wb, self.config)
        self.assertEqual(self.wb.change_report_dir, "custom_reports")

    def test_apply_maps_comment_refresh_keys(self):
        config_loader.apply(self.wb, self.config)
        self.assertEqual(
            self.wb.comment_refresh_scope, self.config.get("comment_refresh_scope", "all")
        )
        self.assertEqual(
            self.wb.comment_refresh_limit, int(self.config.get("comment_refresh_limit", 300))
        )

    def test_apply_maps_markdown_dirs(self):
        config_loader.apply(self.wb, self.config)
        self.assertIn("original_image_dir", self.wb.markdown_file_dir)
        self.assertIn("comment_image_dir", self.wb.markdown_file_dir)
        self.assertIn("original_image_folder", self.wb.download_folder_name)

    def test_apply_maps_anti_ban_and_delay(self):
        config_loader.apply(self.wb, self.config)
        self.assertTrue(hasattr(self.wb, "anti_ban_enabled"))
        self.assertIn("enabled", self.wb.anti_ban_config)
        self.assertIn("batch_delay", self.wb.anti_ban_config)
        self.assertEqual(self.wb.start_page, self.config.get("start_page", 1))
        self.assertEqual(self.wb.page_weibo_count, self.config.get("page_weibo_count"))

    def test_apply_maps_not_downloaded_and_sqlite(self):
        config_loader.apply(self.wb, self.config)
        self.assertTrue(hasattr(self.wb, "not_downloaded_record"))
        self.assertEqual(self.wb.sqlite_db_path, self.config.get("sqlite_db_path", "weibodata.db"))


if __name__ == "__main__":
    unittest.main()
