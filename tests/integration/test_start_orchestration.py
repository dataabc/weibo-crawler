'start / _process_user 编排测试。'

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests import _support  # noqa: E402

_support.bootstrap()

import weibo  # noqa: E402
from util.core import run_loop  # noqa: E402


class StartOrchestrationTest(unittest.TestCase):
    def _make_start_wb(self):
        wb = weibo.Weibo.__new__(weibo.Weibo)
        wb.user_config_list = [{"user_id": "1", "query_list": []}]
        wb._reset_run_state = mock.Mock()
        wb._process_user = mock.Mock()
        wb.generate_crawl_summary_total_report = mock.Mock()
        wb.save_cookies_to_file = mock.Mock()
        lock = mock.Mock()
        wb._acquire_run_lock = mock.Mock(return_value=lock)
        return wb, lock

    def test_returns_immediately_when_lock_busy(self):
        wb, _lock = self._make_start_wb()
        wb._acquire_run_lock = mock.Mock(return_value=None)
        wb.start()
        wb._process_user.assert_not_called()
        # 锁忙时在 try 之前直接 return，finally 不执行（不生成汇总、不保存 cookie）
        wb.generate_crawl_summary_total_report.assert_not_called()
        wb.save_cookies_to_file.assert_not_called()

    def test_runs_users_and_releases_lock(self):
        wb, lock = self._make_start_wb()
        with mock.patch.object(run_loop, "run_users") as run_users:
            wb.start()
        run_users.assert_called_once()
        _args, kwargs = run_users.call_args
        self.assertEqual(kwargs["before_each"], wb._reset_run_state)
        self.assertEqual(kwargs["process"], wb._process_user)
        wb.generate_crawl_summary_total_report.assert_called_once()
        wb.save_cookies_to_file.assert_called_once()
        lock.release.assert_called_once()

    def test_exception_still_runs_finally(self):
        wb, lock = self._make_start_wb()
        with mock.patch.object(run_loop, "run_users", side_effect=RuntimeError("boom")):
            with self.assertLogs("weibo", level="ERROR") as logs:
                wb.start()
        self.assertTrue(any("异常中断" in line for line in logs.output))
        wb.generate_crawl_summary_total_report.assert_called_once()
        wb.save_cookies_to_file.assert_called_once()
        lock.release.assert_called_once()

    def test_summary_failure_does_not_block_release(self):
        wb, lock = self._make_start_wb()
        wb.generate_crawl_summary_total_report = mock.Mock(side_effect=OSError("disk"))
        with mock.patch.object(run_loop, "run_users"):
            wb.start()
        wb.save_cookies_to_file.assert_called_once()
        lock.release.assert_called_once()


class ProcessUserOrchestrationTest(unittest.TestCase):
    """_process_user 的调用顺序与「抓取未完成不推进时间」。"""

    def _make_wb(self, *, query_list=None, success=True, user_config_file_path="u.txt"):
        wb = weibo.Weibo.__new__(weibo.Weibo)
        wb.query = ""
        wb.weibo = []
        wb.crawled_ids_this_run = set()
        wb.user = {"screen_name": "测试"}
        wb.user_config_file_path = user_config_file_path
        wb.start_date = "2024-01-01"
        wb.crawl_success = {"attempted": True, "success": success, "reason": ""}

        order = []
        for name in (
            "initialize_info",
            "get_pages",
            "write_markdown_comments_for_new_weibo",
            "export_comments_to_csv_for_current_user",
            "generate_archive_check_report",
            "generate_deleted_weibos_report",
            "generate_crawl_summary_report",
            "generate_media_check_report",
            "generate_write_failure_report",
            "_review_invisible_archive",
            "_generate_change_report",
            "update_user_config_file",
        ):
            setattr(
                wb,
                name,
                mock.Mock(side_effect=lambda *args, _n=name, **kwargs: order.append(_n)),
            )
        wb._order = order
        self.user_config = {"user_id": "1", "query_list": query_list or []}
        return wb

    def test_normal_mode_call_order(self):
        wb = self._make_wb()
        wb._process_user(self.user_config)
        self.assertEqual(
            wb._order,
            [
                "initialize_info",
                "get_pages",
                "write_markdown_comments_for_new_weibo",
                "export_comments_to_csv_for_current_user",
                "generate_archive_check_report",
                "generate_deleted_weibos_report",
                "generate_crawl_summary_report",
                "generate_media_check_report",
                "generate_write_failure_report",
                "_review_invisible_archive",
                "_generate_change_report",
                "update_user_config_file",
            ],
        )

    def test_query_mode_orchestration(self):
        wb = self._make_wb(query_list=["关键词"])
        wb._process_user(self.user_config)
        self.assertIn("initialize_info", wb._order)
        self.assertIn("get_pages", wb._order)
        wb.update_user_config_file.assert_called_once()

    def test_incomplete_crawl_does_not_advance_success_time(self):
        wb = self._make_wb(success=False)
        wb._process_user(self.user_config)
        wb.update_user_config_file.assert_not_called()

    def test_incomplete_crawl_does_not_advance_for_query_mode(self):
        wb = self._make_wb(query_list=["关键词"], success=False)
        wb._process_user(self.user_config)
        # query 模式不受 success 门控（页数天然少于估算值）
        wb.update_user_config_file.assert_called_once()

    def test_no_user_config_file_does_not_advance(self):
        wb = self._make_wb(user_config_file_path="")
        wb._process_user(self.user_config)
        wb.update_user_config_file.assert_not_called()


if __name__ == "__main__":
    unittest.main()
