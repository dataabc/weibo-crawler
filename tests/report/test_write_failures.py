"""覆盖：
- Markdown 读取失败时**绝不覆盖原文件**，并记录到 run_failures；
- write_data 中单个后端失败被隔离（其他后端继续）并记录失败；
- 本轮抓取情况报告包含「本轮写入/读取失败」章节与统计字段；
- 独立「写入失败报告」：有失败才生成，无失败不产生噪音。"""

import os
import sys
import tempfile
import unittest
from unittest import mock


from tests import _support  # noqa: E402

_support.bootstrap()

import weibo  # noqa: E402
from util.report import crawl_summary  # noqa: E402

sys.modules.pop("json5", None)


class MarkdownReadFailureTest(unittest.TestCase):
    ""

    def _make_wb(self, tmpdir):
        wb = weibo.Weibo.__new__(weibo.Weibo)
        wb.user = {"screen_name": "测试用户"}
        wb.user_config = {"user_id": "1234567890"}
        wb.markdown_split_by = "day"
        wb.only_crawl_original = 1
        wb.markdown_sort_order = "old_to_new"
        wb.markdown_image_order = "live_photo_first"
        wb.run_failures = []
        wb.new_written_weibo = {}
        wb.get_filepath = lambda _type: tmpdir
        return wb

    def test_read_failure_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = os.path.join(tmpdir, "2024-05-03.md")
            original = (
                "## 2024-05-03 [测试用户] 微博存档\n\n"
                "### 10:00:00\n<!-- weibo_id: 111 -->\n旧正文\n\n---\n"
            )
            # 用 GBK 写入，让 UTF-8 读取抛 UnicodeDecodeError（模拟读失败）
            with open(md_path, "wb") as f:
                f.write(original.encode("gbk"))

            wb = self._make_wb(tmpdir)
            new_weibo = [{"id": "222", "created_at": "2024-05-03T10:00:00"}]
            with mock.patch("weibo.logger"):
                wb.generate_markdown_file("2024-05-03", new_weibo)

            # 关键断言：原文件一个字节都没变
            with open(md_path, "rb") as f:
                self.assertEqual(f.read(), original.encode("gbk"))
            self.assertEqual(len(wb.run_failures), 1)
            self.assertEqual(wb.run_failures[0]["stage"], "Markdown 读取")
            self.assertIn(md_path, wb.run_failures[0]["target"])
            self.assertIn("读取失败", wb.run_failures[0]["error"])

    def test_read_success_still_appends(self):
        ''
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = os.path.join(tmpdir, "2024-05-03.md")
            original = "## 2024-05-03 [测试用户] 微博存档\n\n"
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(original)

            wb = self._make_wb(tmpdir)
            wb.markdown_file_dir = {
                "original_image_dir": "img",
                "original_video_dir": "原创微博视频",
                "original_live_photo_dir": "原创微博Live Photo视频",
            }
            wb.markdown_preview = {}
            wb.original_pic_download = 0
            wb.original_video_download = 0
            wb.original_live_photo_download = 0
            wb.only_crawl_original = 1
            new_weibo = [
                {
                    "id": "222",
                    "created_at": "2024-05-03T10:00:00",
                    "text": "新微博正文",
                    "pics": [],
                    "links": [],
                }
            ]
            with mock.patch("weibo.logger"):
                wb.generate_markdown_file("2024-05-03", new_weibo)

            with open(md_path, encoding="utf-8") as f:
                text = f.read()
            self.assertIn("### 10:00:00", text)
            self.assertIn("<!-- weibo_id: 222 -->", text)
            self.assertEqual(wb.run_failures, [])


class WriteFailureCollectionTest(unittest.TestCase):
    ""

    def test_backend_failure_recorded_and_others_continue(self):
        wb = weibo.Weibo.__new__(weibo.Weibo)
        wb.got_count = 1
        wb.write_mode = ["csv", "json"]
        wb.run_failures = []
        wb.original_pic_download = 0
        wb.original_video_download = 0
        wb.original_live_photo_download = 0
        wb.only_crawl_original = 1
        wb.retweet_pic_download = 0
        wb.retweet_video_download = 0
        wb.retweet_live_photo_download = 0

        called = []

        def boom(_):
            raise RuntimeError("磁盘只读")

        wb.write_csv = boom
        wb.write_json = lambda _: called.append("json")
        wb._capture_edit_history = lambda: None
        wb.weibo = []
        wb._apply_edit_updates = lambda _count: None

        with mock.patch("weibo.logger"):
            wb.write_data(0)

        self.assertEqual(called, ["json"])  # 一个后端失败，另一个照常执行
        self.assertEqual(len(wb.run_failures), 1)
        self.assertEqual(wb.run_failures[0]["stage"], "CSV 写入")
        self.assertIn("磁盘只读", wb.run_failures[0]["error"])

    def test_no_new_weibo_skips_write(self):
        wb = weibo.Weibo.__new__(weibo.Weibo)
        wb.got_count = 0
        wb.write_mode = ["csv"]
        wb.run_failures = []
        wb.write_csv = mock.Mock()
        with mock.patch("weibo.logger"):
            wb.write_data(0)
        wb.write_csv.assert_not_called()
        self.assertEqual(wb.run_failures, [])


class ReportRenderTest(unittest.TestCase):
    """写失败进入抓取情况报告，并生成独立失败报告。"""

    def test_crawl_summary_contains_write_failures(self):
        info = crawl_summary.CrawlRunInfo(
            user_id="123",
            screen_name="测试",
            mode="full",
            mode_label="全量",
            source_label="配置文件",
            range_start=None,
            range_end=None,
            success=True,
            success_reason="完成",
            write_failures=[
                {
                    "time": "2026-10-25 17:00:00",
                    "stage": "JSON 写入",
                    "target": "",
                    "error": "磁盘满",
                }
            ],
        )
        summary = crawl_summary.CrawlSummary()
        text = crawl_summary.render_crawl_summary(info, summary)
        self.assertIn("三、本轮写入/读取失败: 1 项", text)
        self.assertIn("JSON 写入", text)
        self.assertIn("磁盘满", text)
        self.assertIn("write_failures: 1", text)

    def test_crawl_summary_no_failures(self):
        info = crawl_summary.CrawlRunInfo(
            user_id="123",
            screen_name="测试",
            mode="full",
            mode_label="全量",
            source_label="配置文件",
            range_start=None,
            range_end=None,
            success=True,
            success_reason="完成",
        )
        text = crawl_summary.render_crawl_summary(info, crawl_summary.CrawlSummary())
        self.assertIn("三、本轮写入/读取失败: 0 项", text)

    def test_write_failure_report_generated(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb = weibo.Weibo.__new__(weibo.Weibo)
            wb.user = {"screen_name": "测试用户"}
            wb.user_config = {"user_id": "1234567890"}
            wb.run_failures = [
                {
                    "time": "2026-10-25 17:00:00",
                    "stage": "CSV 写入",
                    "target": "",
                    "error": "磁盘满",
                }
            ]
            wb.crawl_summary_dir = "report"
            wb._report_output_dir = lambda configured: tmpdir
            with mock.patch("weibo.logger"):
                wb.generate_write_failure_report()
            files = [
                f
                for f in os.listdir(tmpdir)
                if f.startswith("weibo_write_failures_")
            ]
            self.assertEqual(len(files), 1)
            with open(os.path.join(tmpdir, files[0]), encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn("CSV 写入", text)
            self.assertIn("磁盘满", text)
            self.assertIn("失败项: 1 项", text)

    def test_no_report_without_failures(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb = weibo.Weibo.__new__(weibo.Weibo)
            wb.user = {"screen_name": "测试用户"}
            wb.user_config = {"user_id": "1234567890"}
            wb.run_failures = []
            wb._report_output_dir = lambda configured: tmpdir
            wb.generate_write_failure_report()
            self.assertEqual(os.listdir(tmpdir), [])


if __name__ == "__main__":
    unittest.main()
