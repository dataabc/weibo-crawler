"""weibo.py 编排与守卫方法测试。

1. ``_finalize_crawl_success``：本轮是否「顺利完成」的判定（决定报告是否生成、
   ``since_date`` 是否推进），有 10 个分支，错一个就会导致每轮重抓或报告永不生成；
2. ``_run_write_step``：写入子步骤的失败隔离（单点失败不阻断其他后端）；
3. ``_reset_run_state`` / ``_snapshot_archived_ids``：每用户开始前的状态重置与
   抓取前快照。"""

import os
import sys
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests import _support  # noqa: E402

_support.bootstrap()

import weibo  # noqa: E402


def make_wb(**attrs):
    """构造一个最小 Weibo 实例（不跑 __init__，避免配置/依赖）。"""
    wb = weibo.Weibo.__new__(weibo.Weibo)
    for key, value in attrs.items():
        setattr(wb, key, value)
    return wb


class FinalizeCrawlSuccessTest(unittest.TestCase):
    """``_finalize_crawl_success`` 的 10 个结束判定分支。"""

    def _run(self, cov=None, start_page=1, last_page=0, page_count=0):
        wb = make_wb(start_page=start_page, crawl_success=dict(cov or {}))
        wb._finalize_crawl_success(last_page, page_count)
        return wb.crawl_success

    def test_already_success_is_noop(self):
        cov = {"success": True, "reason": "已判定"}
        self.assertEqual(self._run(cov), cov)

    def test_page_errors_mark_failed(self):
        result = self._run({"page_errors": [3]})
        self.assertFalse(result["success"])
        self.assertIn("第3页请求异常", result["reason"])

    def test_start_page_after_first_is_failed(self):
        result = self._run({"start_page": 5}, start_page=5)
        self.assertFalse(result["success"])
        self.assertIn("未从范围开头抓取", result["reason"])

    def test_hit_since_date_marks_success(self):
        result = self._run({"hit_since_date": True})
        self.assertTrue(result["success"])
        self.assertIn("命中起始时间下界", result["reason"])

    def test_append_done_marks_success(self):
        result = self._run({"append_done": True})
        self.assertTrue(result["success"])
        self.assertIn("append 模式命中上次微博 ID", result["reason"])

    def test_api_ended_with_big_gap_mentions_platform_limit(self):
        result = self._run({"api_ended": True}, last_page=2, page_count=10)
        self.assertTrue(result["success"])
        self.assertIn("按设计完成", result["reason"])
        self.assertIn("第2/10页", result["reason"])

    def test_api_ended_small_gap_reason_without_gap(self):
        result = self._run({"api_ended": True}, last_page=9, page_count=10)
        self.assertTrue(result["success"])
        self.assertIn("接口返回无更多数据", result["reason"])
        self.assertNotIn("差距较大", result["reason"])

    def test_api_ended_at_last_page(self):
        result = self._run({"api_ended": True}, last_page=10, page_count=10)
        self.assertTrue(result["success"])
        self.assertIn("顺利完成", result["reason"])

    def test_scanned_all_pages_marks_success(self):
        result = self._run({}, start_page=1, last_page=10, page_count=10)
        self.assertTrue(result["success"])
        self.assertIn("已扫完所有页", result["reason"])

    def test_zero_page_count_marks_success(self):
        result = self._run({}, start_page=1, last_page=0, page_count=0)
        self.assertTrue(result["success"])

    def test_early_stop_marks_failed(self):
        result = self._run({}, start_page=1, last_page=3, page_count=10)
        self.assertFalse(result["success"])
        self.assertIn("提前结束于第3/10页", result["reason"])


class RunWriteStepTest(unittest.TestCase):
    """``_run_write_step``：成功透传、业务失败与程序性错误的区分。"""

    def _wb(self):
        return make_wb(run_failures=[])

    def test_success_returns_value(self):
        wb = self._wb()
        self.assertEqual(wb._run_write_step("步骤", lambda x: x * 2, 21), 42)
        self.assertEqual(wb.run_failures, [])

    def test_business_failure_is_recorded_and_not_raised(self):
        def boom():
            raise ValueError("后端失败")

        wb = self._wb()
        self.assertIsNone(wb._run_write_step("CSV 写入", boom))
        self.assertEqual(len(wb.run_failures), 1)
        self.assertEqual(wb.run_failures[0]["stage"], "CSV 写入")
        self.assertIn("ValueError", wb.run_failures[0]["error"])

    def test_programming_error_is_marked(self):
        def boom():
            raise NameError("name 'x' is not defined")

        wb = self._wb()
        self.assertIsNone(wb._run_write_step("markdown 写入", boom))
        self.assertEqual(len(wb.run_failures), 1)
        self.assertEqual(wb.run_failures[0]["target"], "程序性错误")

    def test_failures_accumulate(self):
        wb = self._wb()
        wb._run_write_step("a", lambda: 1 / 0)
        wb._run_write_step("b", lambda: 1 / 0)
        self.assertEqual(len(wb.run_failures), 2)


class ResetRunStateTest(unittest.TestCase):
    """``_reset_run_state``：每用户开始前的状态重置（含 C33/C34 新增标记）。"""

    def test_resets_all_per_user_state(self):
        wb = make_wb()
        wb.crawl_success = {"attempted": True, "success": True}
        wb.archived_ids_before = {"1": None}
        wb.known_invisible_ids = {"1"}
        wb._known_invisible_loaded = True
        wb._known_invisible_load_failed = True
        wb._not_downloaded_map_cache = {"x": 1}
        wb.run_failures = [{"stage": "x"}]
        wb.media_failure_count = 9
        wb._edit_captured_ids = {"1"}

        wb._reset_run_state({"user_id": "1"})

        self.assertEqual(
            wb.crawl_success, {"attempted": False, "success": False, "reason": "未开始"}
        )
        self.assertIsNone(wb.archived_ids_before)
        self.assertEqual(wb.known_invisible_ids, set())
        self.assertFalse(wb._known_invisible_loaded)
        self.assertFalse(wb._known_invisible_load_failed)
        self.assertIsNone(wb._not_downloaded_map_cache)
        self.assertEqual(wb.run_failures, [])
        self.assertEqual(wb.media_failure_count, 0)
        self.assertEqual(wb._edit_captured_ids, set())


class SnapshotArchivedIdsTest(unittest.TestCase):
    """``_snapshot_archived_ids``：开关/模式门控、正常扫描、失败兜底。"""

    def _wb(self, **attrs):
        base = dict(
            archive_check_enabled=0,
            crawl_summary_enabled=0,
            write_mode=["markdown"],
            user_config={"since_date": "2024-01-01T00:00:00", "query_list": []},
            run_failures=[],
        )
        base.update(attrs)
        wb = make_wb(**base)
        wb.get_filepath = lambda _type: "/tmp/user"
        wb._archive_exclude_dirs = lambda: []
        return wb

    def test_disabled_both_reports_skips_scan(self):
        wb = self._wb()
        with mock.patch.object(weibo.archive_index, "collect_archive_entries") as collect:
            wb._snapshot_archived_ids()
        collect.assert_not_called()
        self.assertIsNone(wb.archived_ids_before)
        self.assertEqual(wb.crawl_summary_baseline_note, "")

    def test_non_markdown_mode_sets_note(self):
        wb = self._wb(archive_check_enabled=1, write_mode=["csv"])
        wb._snapshot_archived_ids()
        self.assertEqual(wb.crawl_summary_baseline_note, "write_mode 不含 markdown")
        self.assertIsNone(wb.archived_ids_before)

    def test_query_mode_sets_note(self):
        wb = self._wb(
            archive_check_enabled=1,
            user_config={"since_date": "2024-01-01T00:00:00", "query_list": ["关键词"]},
        )
        wb._snapshot_archived_ids()
        self.assertEqual(wb.crawl_summary_baseline_note, "关键词(query)模式")

    def test_success_populates_entries_and_stats(self):
        wb = self._wb(archive_check_enabled=1)
        stats = SimpleNamespace(files_scanned=3, files_skipped=1)
        entries = {"1001": ("2024-01-02T00:00:00", "/tmp/a.md")}
        with mock.patch.object(
            weibo.archive_index, "collect_archive_entries", return_value=(entries, stats)
        ):
            wb._snapshot_archived_ids()
        self.assertEqual(wb.archived_entries_before, entries)
        self.assertEqual(list(wb.archived_ids_before), ["1001"])
        self.assertIs(wb.archive_scan_stats, stats)
        self.assertIsNotNone(wb.check_start_dt)
        self.assertIsNotNone(wb.check_end_dt)
        self.assertEqual(wb.run_failures, [])

    def test_scan_failure_records_failure_and_clears(self):
        wb = self._wb(crawl_summary_enabled=1)
        with mock.patch.object(
            weibo.archive_index, "collect_archive_entries", side_effect=ValueError("扫描炸了")
        ):
            wb._snapshot_archived_ids()
        self.assertEqual(wb.crawl_summary_baseline_note, "抓取前存档扫描失败")
        self.assertIsNone(wb.archived_ids_before)
        self.assertEqual(len(wb.run_failures), 1)
        self.assertEqual(wb.run_failures[0]["stage"], "存档快照")

    def test_scan_programming_error_also_recorded(self):
        wb = self._wb(archive_check_enabled=1)
        with mock.patch.object(
            weibo.archive_index, "collect_archive_entries", side_effect=AttributeError("字段没了")
        ):
            wb._snapshot_archived_ids()
        self.assertEqual(len(wb.run_failures), 1)
        self.assertIn("AttributeError", wb.run_failures[0]["error"])


class ArchiveStateCacheTest(unittest.TestCase):
    """状态索引每用户只读一次、共享同一 dict、换用户清空。"""

    def _wb(self):
        wb = make_wb(
            user_config={"user_id": "1"},
            markdown_split_by="day",
            run_failures=[],
        )
        wb._archive_state_path = lambda: "/tmp/state.json"
        wb.get_filepath = lambda _type: "/tmp/user"
        wb._archive_exclude_dirs = lambda: []
        return wb

    def test_loads_once_per_user(self):
        wb = self._wb()
        state = {"weibos": {"100": {"status": "active"}}}
        with mock.patch.object(weibo.archive_state, "load_state", return_value=(state, "")) as load:
            first = wb._ensure_archive_state()
            second = wb._ensure_archive_state()
        self.assertEqual(load.call_count, 1)
        self.assertIs(first, second)

    def test_reset_run_state_clears_cache(self):
        wb = self._wb()
        state = {"weibos": {"100": {"status": "active"}}}
        with mock.patch.object(weibo.archive_state, "load_state", return_value=(state, "")) as load:
            wb._ensure_archive_state()
            wb._reset_run_state({"user_id": "2"})
            wb._ensure_archive_state()
        self.assertEqual(load.call_count, 2)

    def test_shared_mutation_is_visible(self):
        """共享同一对象：一个模块写入的字段，另一个模块能直接看到（无 lost update）。"""
        wb = self._wb()
        state = {"weibos": {"100": {"status": "active"}}}
        with mock.patch.object(weibo.archive_state, "load_state", return_value=(state, "")):
            first = wb._ensure_archive_state()
            first["weibos"]["100"]["comment_refreshed_at"] = "2024-01-01T00:00:00"
            second = wb._ensure_archive_state()
        self.assertEqual(
            second["weibos"]["100"]["comment_refreshed_at"], "2024-01-01T00:00:00"
        )

    def test_empty_state_triggers_rebuild(self):
        wb = self._wb()
        with mock.patch.object(
            weibo.archive_state, "load_state", return_value=({}, "索引为空")
        ), mock.patch.object(
            weibo.archive_index,
            "collect_archive_entries",
            return_value=({"100": (datetime(2024, 1, 2), "/tmp/user/2024-01/a.md")}, None),
        ):
            state = wb._ensure_archive_state()
        self.assertIn("100", state["weibos"])
        self.assertEqual(len(wb.run_failures), 1)  # 索引为空会记一条 warning


if __name__ == "__main__":
    unittest.main()
