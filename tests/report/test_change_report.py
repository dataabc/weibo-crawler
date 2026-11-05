"""变化报告 单元测试。"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


from tests import _support  # noqa: E402

_support.bootstrap()

import weibo  # noqa: E402
from util.core import archive_state  # noqa: E402
from util.report import change_report  # noqa: E402

sys.modules.pop("json5", None)


class RenderChangeReportTest(unittest.TestCase):
    def test_with_changes(self):
        text = change_report.render_change_report(
            user_id="123",
            screen_name="测试用户",
            window_start="2022-01-01",
            window_end="2026-10-25",
            crawl_ok=True,
            pages=12,
            captured_count=20,
            edit_changes=[
                {
                    "weibo_id": "100",
                    "created_at": "2024-05-03T10:00:00",
                    "new_edit_at": "2024-06-01 00:00:00",
                    "snapshot": "编辑历史/2024-06/2024-06-01_100.md",
                }
            ],
            visibility_changes=[
                {
                    "weibo_id": "101",
                    "old_label": "公开",
                    "new_label": "分组可见（list_id=3）",
                }
            ],
            missing_weibos=[
                {"weibo_id": "102", "created_at": "2024-01-01T00:00:00"}
            ],
            restored_weibos=[
                {"weibo_id": "103", "created_at": "2024-02-01T00:00:00", "old_status": "suspect_invisible"}
            ],
        )
        self.assertIn("微博存档变化报告", text)
        self.assertIn("一、编辑变化: 1 条", text)
        self.assertIn("二、可见性变化: 1 条", text)
        self.assertIn("三、疑似不可见/删除: 1 条", text)
        self.assertIn("四、恢复可见: 1 条", text)
        self.assertIn("五、隔离区审核: 0 条", text)
        self.assertIn("captured_count: 20", text)
        self.assertIn("edit_changes: 1", text)
        self.assertIn("编辑历史/2024-06/2024-06-01_100.md", text)

    def test_no_changes(self):
        text = change_report.render_change_report(
            user_id="123", screen_name="测试用户", captured_count=0
        )
        self.assertIn("（本轮无编辑变化）", text)
        self.assertIn("（本轮无可见性变化）", text)
        self.assertIn("（本轮没有已存档微博缺席）", text)
        self.assertIn("（本轮没有恢复可见的微博）", text)

    def test_incomplete_crawl_warning(self):
        text = change_report.render_change_report(
            user_id="123",
            screen_name="测试用户",
            crawl_ok=False,
            crawl_reason="第3页请求失败",
        )
        self.assertIn("未顺利完成", text)
        self.assertIn("第3页请求失败", text)


class GenerateChangeReportTest(unittest.TestCase):
    def _make_wb(self, tmpdir, state):
        wb = weibo.Weibo.__new__(weibo.Weibo)
        wb.user = {"screen_name": "测试用户"}
        wb.user_config = {"user_id": "1234567890"}
        wb.weibo = [{"id": 100}, {"id": 200}]
        wb.edit_changes = [
            {
                "weibo_id": "100",
                "created_at": "2024-05-03T10:00:00",
                "new_edit_at": "2024-06-01 00:00:00",
                "snapshot": "编辑历史/2024-06/2024-06-01_100.md",
            }
        ]
        wb.visibility_changes = []
        wb.crawl_success = {
            "success": True,
            "window_start": "2022-01-01",
            "window_end": "2026-10-25",
            "pages_processed": 10,
        }
        wb.weibo_change_report = 1
        wb.change_report_dir = "report"
        wb._state_path = ""
        wb.run_failures = []
        wb._report_output_dir = lambda _dir: tmpdir
        wb._atomic_write_text = lambda path, text: open(
            path, "w", encoding="utf-8"
        ).write(text)
        wb._record_write_failure = lambda *args: None
        wb._ensure_archive_state = lambda: state
        return wb

    @staticmethod
    def _state():
        return {
            "weibos": {
                "100": {
                    "created_at": "2024-05-03T10:00:00",
                    "status": "active",
                    "missing_rounds": 0,
                },
                "200": {
                    "created_at": "2024-05-04T10:00:00",
                    "status": "suspect_invisible",
                    "missing_rounds": 2,
                },
                "300": {
                    "created_at": "2024-05-05T10:00:00",
                    "status": "active",
                    "missing_rounds": 0,
                },
            }
        }

    def test_report_written_and_status_restored(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = self._state()
            wb = self._make_wb(tmpdir, state)
            with mock.patch("weibo.logger"):
                wb._generate_change_report()
            files = [
                f for f in os.listdir(tmpdir) if f.startswith("weibo_change_report_")
            ]
            self.assertEqual(len(files), 1)
            with open(os.path.join(tmpdir, files[0]), encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn("三、疑似不可见/删除: 1 条", text)
            self.assertIn("300", text)
            self.assertIn("四、恢复可见: 1 条", text)
            self.assertIn("200", text)
            # 恢复可见的 200 状态改为 active
            self.assertEqual(state["weibos"]["200"]["status"], "active")

    def test_disabled_skips(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb = self._make_wb(tmpdir, self._state())
            wb.weibo_change_report = 0
            with mock.patch("weibo.logger"):
                wb._generate_change_report()
            self.assertEqual(
                [f for f in os.listdir(tmpdir) if f.startswith("weibo_change_report_")],
                [],
            )


class VisibilityChangeTest(unittest.TestCase):
    def test_visibility_label_diff(self):
        old = {"type": 0, "list_id": 0}
        new = {"type": 6, "list_id": 3}
        self.assertNotEqual(old, new)
        self.assertEqual(archive_state.visibility_label(old), "公开")
        self.assertIn("分组可见", archive_state.visibility_label(new))


class LongTextFailureReportTest(unittest.TestCase):
    def test_long_text_failures_section(self):
        text = change_report.render_change_report(
            user_id="123",
            screen_name="测试用户",
            long_text_failures=[
                {
                    "weibo_id": "100",
                    "created_at": "2024-05-03T10:00:00",
                    "md_path": "2024-05/2024-05-03.md",
                    "archived": True,
                },
                {
                    "weibo_id": "200",
                    "created_at": "2024-05-04T10:00:00",
                    "md_path": "2024-05/2024-05-04.md",
                    "archived": False,
                },
            ],
        )
        self.assertIn("六、长文获取失败: 2 条", text)
        self.assertIn("已保留旧存档、本轮不更新", text)
        self.assertIn("新微博仅保存截断正文", text)
        self.assertIn("long_text_failures: 2", text)


if __name__ == "__main__":
    unittest.main()
