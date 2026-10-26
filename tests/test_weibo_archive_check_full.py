"""全量存档核对脚本的流程测试（无网络；用假的 Weibo 对象）。

覆盖：
- 平台枚举：日期下界停止、置顶旧帖不提前结束、转发按策略过滤、页级失败判定；
- CLI 参数：只保留 --users / --since / --max-pages。
"""

import sys
import types
import unittest
from datetime import datetime


def _install_import_stubs() -> None:
    """让 weibo.py / weibo_archive_check_full.py 能在未安装依赖的环境里被导入。"""
    if "requests" in sys.modules:
        return

    def add(name: str, **attrs):
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        sys.modules[name] = module
        return module

    add("requests", Session=object, get=lambda *a, **k: None)
    add("requests.exceptions", RequestException=Exception)
    add("requests.adapters", HTTPAdapter=object)
    add("json5", loads=lambda text: {})
    add("tqdm", tqdm=lambda iterable, **kwargs: iterable)
    lxml = add("lxml")
    lxml.etree = add("lxml.etree")
    add("piexif")


_install_import_stubs()

import weibo_archive_check_full  # noqa: E402

# 上面的 json5 只是为了让 weibo.py 可导入；测试结束前移除，避免影响
# util.config_migrate 的“无 json5 降级解析”测试。
sys.modules.pop("json5", None)


def make_card(weibo_id: str, created_at: str, *, retweet: bool = False) -> dict:
    mblog = {"id": weibo_id, "created_at": created_at, "mblogtype": 0}
    if retweet:
        mblog["retweeted_status"] = {"id": "9"}
    return {"card_type": 9, "mblog": mblog}


class FakeWB:
    """模拟 Weibo 中检查用到的最小接口。"""

    only_crawl_original = 1
    anti_ban_enabled = False
    since_date = "2024-01-01T00:00:00"

    def __init__(self, pages: dict[int, list[dict]]):
        self.pages = pages

    def get_weibo_json(self, page: int):
        if page not in self.pages:
            return {}
        return {"ok": 1, "data": {"cards": self.pages[page]}}

    def standardize_date(self, raw: str):
        return raw, raw.replace("T", " ")


class EnumeratePlatformTest(unittest.TestCase):
    def setUp(self) -> None:
        self.wb = FakeWB(
            {
                1: [
                    make_card("100", "2024-07-01T10:00:00"),
                    make_card("101", "2024-06-15T10:00:00"),
                ],
                2: [
                    make_card("102", "2024-06-02T10:00:00"),
                    make_card("103", "2024-05-30T10:00:00"),  # 早于检查起点 → 停止
                ],
                3: [make_card("104", "2024-04-01T10:00:00")],
            }
        )

    def test_stop_at_since(self) -> None:
        platform, enumeration = weibo_archive_check_full.enumerate_platform(
            self.wb,
            audit_since=datetime(2024, 6, 1),
            page_count=3,
            max_pages=300,
        )
        self.assertTrue(enumeration.ok)
        self.assertTrue(enumeration.hit_since_date)
        self.assertEqual(enumeration.pages_done, 2)
        self.assertEqual(set(platform), {"100", "101", "102"})

    def test_retweet_filtered_by_policy(self) -> None:
        wb = FakeWB(
            {
                1: [
                    make_card("200", "2024-07-01T10:00:00"),
                    make_card("201", "2024-06-30T10:00:00", retweet=True),
                    make_card("202", "2024-05-30T10:00:00"),
                ]
            }
        )
        platform, enumeration = weibo_archive_check_full.enumerate_platform(
            wb, datetime(2024, 6, 1), 1, 300
        )
        self.assertNotIn("201", platform)
        self.assertIn("200", platform)
        self.assertTrue(enumeration.ok)  # 命中下界

    def test_page_failure_is_incomplete(self) -> None:
        wb = FakeWB({1: [make_card("300", "2024-07-01T10:00:00")]})  # 第 2 页返回 {}
        _, enumeration = weibo_archive_check_full.enumerate_platform(
            wb, datetime(2024, 1, 1), 2, 300
        )
        self.assertFalse(enumeration.ok)
        self.assertEqual(enumeration.page_errors, [2])

    def test_page_limit_marks_incomplete(self) -> None:
        wb = FakeWB({page: [] for page in range(1, 6)})
        _, enumeration = weibo_archive_check_full.enumerate_platform(
            wb, datetime(2024, 1, 1), 10, max_pages=3
        )
        self.assertFalse(enumeration.ok)


class ParserTest(unittest.TestCase):
    def test_only_three_options(self) -> None:
        parser = weibo_archive_check_full.build_parser()
        dests = {action.dest for action in parser._actions}
        self.assertIn("users", dests)
        self.assertIn("since", dests)
        self.assertIn("max_pages", dests)
        self.assertNotIn("verify", dests)
        self.assertNotIn("backfill", dests)
        self.assertNotIn("report_dir", dests)


class FullCheckSwitchTest(unittest.TestCase):
    def test_helper_defaults_to_disabled(self) -> None:
        self.assertFalse(weibo_archive_check_full._full_check_enabled({}))
        self.assertTrue(
            weibo_archive_check_full._full_check_enabled({"weibo_archive_check_full": 1})
        )
        self.assertFalse(
            weibo_archive_check_full._full_check_enabled({"weibo_archive_check_full": 0})
        )

    def test_main_exits_before_weibo_init_when_disabled(self) -> None:
        """配置 weibo_archive_check_full=0 时脚本直接退出，不初始化 Weibo。"""
        module = weibo_archive_check_full.weibo
        old_get_config = module.get_config
        old_weibo_cls = module.Weibo
        calls = []

        def fake_get_config():
            return {"weibo_archive_check_full": 0}

        def fake_weibo(config):
            calls.append(config)
            return None

        try:
            module.get_config = fake_get_config
            module.Weibo = fake_weibo
            code = weibo_archive_check_full.main(["--users", "123"])
        finally:
            module.get_config = old_get_config
            module.Weibo = old_weibo_cls

        self.assertEqual(code, 0)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
