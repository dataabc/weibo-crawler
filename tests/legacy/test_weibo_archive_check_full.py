"""全量存档核对脚本的流程测试（无网络；用假的 Weibo 对象）。

覆盖：
- 平台枚举：日期下界停止、置顶旧帖不提前结束、转发按策略过滤、页级失败判定；
- 置顶越界帖：早于 --since 的置顶旧帖不进 platform、不进「未保存」；
- CLI 参数：只保留 --users / --since / --max-pages。"""

import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


from tests import _support  # noqa: E402

_support.bootstrap()

_SCRIPT_DIR = str(Path(__file__).resolve().parents[2] / "scripts")
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import weibo_archive_check_full  # noqa: E402
from util.report import archive_check_core  # noqa: E402

# 上面的 json5 只是为了让 weibo.py 可导入；测试结束前移除，避免影响
# util.config_migrate 的“无 json5 降级解析”测试。
sys.modules.pop("json5", None)


def make_card(
    weibo_id: str, created_at: str, *, retweet: bool = False, pinned: bool = False
) -> dict:
    mblog = {
        "id": weibo_id,
        "created_at": created_at,
        "mblogtype": 2 if pinned else 0,
    }
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


class PinnedOutOfRangeTest(unittest.TestCase):
    '置顶帖只是时间线顶部的展示位，它只用于「避免因为时间'

    def setUp(self) -> None:
        self.wb = FakeWB(
            {
                1: [
                    # 早于 --since 的置顶旧帖（2023 年），真实场景中很常见
                    make_card("9001", "2023-05-01T10:00:00", pinned=True),
                    make_card("2001", "2024-06-15T10:00:00"),
                    make_card("2002", "2024-06-16T10:00:00"),
                ],
                2: [
                    make_card("1002", "2024-06-05T10:00:00"),
                    # 非置顶越界帖：行为不变，仍用于判定已到达下界
                    make_card("1000", "2024-05-01T10:00:00"),
                ],
            }
        )
        self.since = datetime(2024, 6, 1)
        self.until = datetime(2024, 12, 31)

    def test_pinned_before_since_not_in_platform(self) -> None:
        platform, enumeration = weibo_archive_check_full.enumerate_platform(
            self.wb, self.since, 3, 300
        )
        self.assertTrue(enumeration.ok)
        self.assertTrue(enumeration.hit_since_date)
        self.assertEqual(enumeration.pages_done, 2)
        self.assertNotIn("9001", platform)  # 越界置顶帖不进 platform
        self.assertEqual(set(platform), {"2001", "2002", "1002"})
        self.assertNotIn("1000", platform)  # 非置顶越界帖行为不变

    def test_pinned_out_of_range_not_flagged_not_saved(self) -> None:
        """枚举 → 比对 → 渲染整条链路：报告与 platform_count 都不含该置顶帖。"""
        platform, _ = weibo_archive_check_full.enumerate_platform(
            self.wb, self.since, 3, 300
        )
        # 本地只存档了范围内的 1002；平台上的 2001/2002 确实未保存
        local_entries = {"1002": (datetime(2024, 6, 5), "/tmp/1002.md")}
        diff = archive_check_core.compare_archive(
            local_entries, platform, (), self.since, self.until
        )
        text = archive_check_core.render_report(
            "全量存档核对报告",
            "123",
            "测试用户",
            diff,
            header_lines=[f"平台枚举: {len(platform)} 条（已按 only_crawl_original 过滤）"],
            range_start=self.since,
            range_end=self.until,
            summary_lines=[f"platform_count: {len(platform)}"],
            base_dir="/tmp",
        )

        self.assertEqual(diff.not_saved, ["2002", "2001"])
        self.assertEqual(diff.not_returned, [])
        self.assertNotIn("9001", text)  # 越界置顶帖不出现在报告任何位置
        self.assertNotIn("2023-05-01", text)
        self.assertIn("platform_count: 3", text)
        self.assertIn("未保存的微博（平台有、本地没有）: 2 条", text)


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


class _FullCheckWB:
    """audit_one_user 用的最小 Weibo 替身（不联网）。"""

    only_crawl_original = 1
    anti_ban_enabled = False
    invisible_weibo_dirs: list[str] = []

    def __init__(self, output_root: str, since_date: str, page_count: int = 3):
        self.output_root = output_root
        self.output_directory = output_root
        self.since_date = since_date
        self.page_count = page_count
        self.user = {"screen_name": "用户A"}
        self.user_config = {"user_id": "123"}
        self.query = ""

    def initialize_info(self, user_config):
        self.user_config = user_config

    def get_user_info(self):
        return 0

    def get_filepath(self, type):
        path = os.path.join(self.output_root, "用户A")
        os.makedirs(path, exist_ok=True)
        return path

    def get_page_count(self):
        return self.page_count

    def _report_output_dir(self, name):
        path = os.path.join(self.output_root, name)
        os.makedirs(path, exist_ok=True)
        return path


class AuditSinceRangeTest(unittest.TestCase):
    """全量核对默认从本地存档最早日期开始。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.md_dir = os.path.join(self.root, "用户A")
        os.makedirs(self.md_dir, exist_ok=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_md(self, name: str, weibo_id: str) -> None:
        path = os.path.join(self.md_dir, name)
        Path(path).write_text(
            "### 10:00:00\n<!-- weibo_id: %s -->\n正文\n\n---\n" % weibo_id,
            encoding="utf-8",
        )

    def _run(self, wb, args_since: str | None = None):
        args = SimpleNamespace(since=args_since, max_pages=300)
        captured = {}

        def fake_enumerate(wb_, audit_since, page_count, max_pages):
            captured["since"] = audit_since
            return {}, SimpleNamespace(
                ok=True, reason="测试", pages_done=1, page_count=page_count,
                page_errors=[], hit_since_date=True,
            )

        fake_diff = SimpleNamespace(not_returned=[], not_saved=[], local_entries={})
        with mock.patch.object(
            weibo_archive_check_full, "enumerate_platform", side_effect=fake_enumerate
        ), mock.patch.object(
            weibo_archive_check_full.archive_check_core,
            "compare_archive",
            return_value=fake_diff,
        ):
            code = weibo_archive_check_full.audit_one_user(wb, {"user_id": "123"}, args)
        return code, captured.get("since")

    def test_default_uses_local_archive_earliest(self) -> None:
        # 默认应取本地最早日期，而不是把 2022~2026 的平台微博列成「未保存」。
        self._write_md("2026-10-18.md", "101")
        wb = _FullCheckWB(self.root, "2022-01-01T00:00:00")
        code, since = self._run(wb)
        self.assertEqual(code, 0)
        self.assertEqual(since, datetime(2026, 10, 18, 10, 0, 0))

    def test_no_local_archive_falls_back_to_user_since_date(self) -> None:
        wb = _FullCheckWB(self.root, "2022-01-01T00:00:00")
        code, since = self._run(wb)
        self.assertEqual(code, 0)
        self.assertEqual(since, datetime(2022, 1, 1))

    def test_cli_since_overrides_default(self) -> None:
        self._write_md("2026-10-18.md", "101")
        wb = _FullCheckWB(self.root, "2022-01-01T00:00:00")
        code, since = self._run(wb, args_since="2024-06-01")
        self.assertEqual(code, 0)
        self.assertEqual(since, datetime(2024, 6, 1))


if __name__ == "__main__":
    unittest.main()
