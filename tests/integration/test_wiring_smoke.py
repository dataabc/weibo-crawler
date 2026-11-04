"""本文件补的是「接线」层面的护栏：不联网、不写盘，只验证

1. `Weibo` 类上真的挂着调用方使用的方法（`wb.get_random_headers()` 等）；
2. `get_one_page` 里 `wb` 参数不会被微博 dict 覆盖；
3. 项目根解析指向仓库根，而不是子包目录；
4. `__main__.py` 能作为模块加载。"""

import importlib.util
import logging
import os
import sys
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


from tests import _support  # noqa: E402

_support.bootstrap()

import weibo  # noqa: E402
from util.config import config_loader  # noqa: E402
from util.core import project  # noqa: E402
from util.fetch import fetch_pages  # noqa: E402

ANTI_BAN_METHODS = (
    "get_random_headers",
    "calculate_dynamic_delay",
    "should_pause_session",
    "check_batch_delay",
    "update_crawl_stats",
    "reset_crawl_stats",
    "perform_anti_ban_rest",
)


class _FakeWB:
    """最小 Weibo 替身：只提供调用链需要的属性，不联网。"""

    def __init__(self):
        self.weibo = []
        self.weibo_id_list = []
        self.got_count = 0
        self.crawl_success = {"attempted": True, "success": True}
        self.query = ""
        self.user_config = {
            "user_id": "1234567890",
            "since_date": "2020-01-01T00:00:00",
            "end_date": "",
        }
        self.user = {"screen_name": "测试用户", "id": "1234567890"}
        self.only_crawl_original = 0
        self.first_crawler = False
        self.last_weibo_id = ""
        self.last_weibo_date = "2020-01-01T00:00:00"
        self.latest_weibo_id = ""
        self.known_invisible_ids = set()
        self.skipped_invisible_count = 0
        self.filtered_retweet_count = 0
        self.anti_ban_enabled = 0
        self.anti_ban_config = {}
        self.headers = {}
        self.crawl_stats = {
            "weibo_count": 0,
            "request_count": 0,
            "api_errors": 0,
            "start_time": None,
            "batch_count": 0,
            "last_batch_time": None,
        }

    def get_weibo_json(self, page):
        return {
            "ok": 1,
            "data": {
                "cards": [
                    {
                        "card_type": 9,
                        "mblog": {
                            "id": "5000000000000001",
                            "text": "示例正文",
                            "created_at": "2024-01-01T00:00:00",
                        },
                    }
                ]
            },
        }

    def get_one_weibo(self, w):
        return {
            "id": "5000000000000001",
            "text": "示例正文",
            "created_at": "2024-01-01T00:00:00",
            "user_id": "1234567890",
        }

    def update_crawl_stats(self, weibo_count=0, request_count=0, api_error=False):
        pass

    def should_pause_session(self):
        return False, ""

    def print_weibo(self, weibo):
        pass


class WiringSmokeTest(unittest.TestCase):
    ""

    def test_weibo_class_has_anti_ban_methods(self):
        '7 个防封禁方法必须挂在 Weibo 类上。'
        missing = [name for name in ANTI_BAN_METHODS if not hasattr(weibo.Weibo, name)]
        self.assertEqual(missing, [], f"Weibo 类缺少防封禁方法: {missing}")

    def test_anti_ban_methods_callable_with_fake_wb(self):
        """防封禁方法可用假 wb 调用，内部不再有未定义名。"""
        fake = _FakeWB()
        expected = {
            "get_random_headers": dict,
            "calculate_dynamic_delay": (int, float),
            "should_pause_session": tuple,
            "check_batch_delay": type(None),
            "update_crawl_stats": type(None),
            "reset_crawl_stats": type(None),
            "perform_anti_ban_rest": type(None),
        }
        for name, result_type in expected.items():
            method = getattr(weibo.Weibo, name)
            with self.subTest(method=name):
                result = method(fake)
                self.assertIsInstance(result, result_type)

    def test_get_one_page_does_not_shadow_wb(self):
        """get_one_page 内 wb 参数不能被微博 dict 覆盖。"""
        fake = _FakeWB()
        fetch_pages.get_one_page(fake, 1)
        self.assertEqual(fake.crawl_success.get("page_errors", []), [])
        self.assertEqual(len(fake.weibo), 1)
        self.assertIsInstance(fake.weibo[0], dict)
        # 实例本身没有被换成 dict：这些属性仍然存在
        self.assertEqual(fake.weibo_id_list, ["5000000000000001"])

    def test_project_root_points_to_repo_root(self):
        """项目根必须是含 weibo.py 的那一层。"""
        root = project.project_root()
        self.assertTrue(os.path.isfile(os.path.join(root, "weibo.py")), root)
        self.assertNotEqual(
            os.path.dirname(os.path.dirname(os.path.realpath(config_loader.__file__))),
            root,
            "项目根不应等于 util/ 目录",
        )

    # test_main_entry_importable 见下

    def test_main_entry_importable(self):
        """__main__.py 必须能作为模块加载。"""
        spec = importlib.util.spec_from_file_location(
            "weibo_crawler_main", _PROJECT_ROOT / "__main__.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(callable(module.main))

    def test_logs_redirected_in_tests(self):
        """测试环境日志必须重定向到 WEIBO_LOG_DIR，不能写仓库 log/。"""
        log_dir = os.environ.get("WEIBO_LOG_DIR")
        self.assertTrue(log_dir, "未设置 WEIBO_LOG_DIR（_support.bootstrap 未生效）")
        handlers = [
            handler
            for logger_obj in (logging.getLogger(), logging.getLogger("weibo"))
            for handler in logger_obj.handlers
            if isinstance(handler, logging.FileHandler)
        ]
        self.assertTrue(handlers, "未找到文件日志 handler")
        real_log_dir = os.path.realpath(log_dir)
        for handler in handlers:
            self.assertTrue(
                os.path.realpath(handler.baseFilename).startswith(real_log_dir),
                f"日志仍写仓库: {handler.baseFilename}",
            )


    def test_runtime_reexports_used_by_util(self):
        'util 层延迟 import 的运行时原语必须留在 weibo 模块上。'
        for name in (
            "HTTPAdapter",
            "resolve_cookie",
            "sleep",
            "logger",
            "WRITE_INFO_KEYS",
        ):
            with self.subTest(name=name):
                self.assertTrue(hasattr(weibo, name), f"weibo 缺少 {name}（util 层依赖它）")


if __name__ == "__main__":
    unittest.main()
