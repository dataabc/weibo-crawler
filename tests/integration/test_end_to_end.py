'端到端集成测试：写 md → 抓取情况报告 → 变化报告。'

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


from tests import _support  # noqa: E402

_support.bootstrap()

import weibo  # noqa: E402
from util.core import markdown_blocks  # noqa: E402

sys.modules.pop("json5", None)


def _make_wb(tmpdir):
    wb = weibo.Weibo.__new__(weibo.Weibo)
    wb.user = {"screen_name": "测试用户", "statuses_count": 10}
    wb.user_config = {"user_id": "1234567890", "since_date": "2024-05-01T00:00:00"}
    wb.markdown_split_by = "day"
    wb.markdown_sort_order = "old_to_new"
    wb.markdown_image_order = "live_photo_first"
    wb.markdown_file_dir = {
        "original_image_dir": "img",
        "retweet_image_dir": "img",
        "original_video_dir": "原创微博视频",
        "retweet_video_dir": "转发微博视频",
        "original_live_photo_dir": "原创微博Live Photo视频",
        "retweet_live_photo_dir": "转发微博Live Photo视频",
        "comment_image_dir": "评论图片",
    }
    wb.markdown_preview = {}
    wb.markdown_write_backup = 0
    wb.only_crawl_original = 1
    wb.original_pic_download = 0
    wb.original_video_download = 0
    wb.original_live_photo_download = 0
    wb.retweet_pic_download = 0
    wb.retweet_video_download = 0
    wb.retweet_live_photo_download = 0
    wb.weibo = []
    wb.new_written_weibo = {}
    wb.run_failures = []
    wb.edit_changes = []
    wb.visibility_changes = []
    wb.invisible_review = {}
    wb.crawl_summary_enabled = 1
    wb.crawl_summary_dir = "report"
    wb.archive_check_enabled = 0
    wb.media_check_enabled = 0
    wb.comment_summary_enabled = 0
    wb.weibo_change_report = 1
    wb.change_report_dir = "report"
    wb.weibo_write_failure_dir = "report"
    wb.crawl_success = {
        "attempted": True,
        "success": True,
        "reason": "",
        "window_start": "2024-05-01T00:00:00",
        "window_end": "2024-05-03T23:59:59",
        "pages_processed": 1,
        "page_count": 1,
    }
    wb.start_date = "2024-05-03T23:59:59"
    wb.archived_entries_before = {}
    wb.archive_scan_stats = None
    wb.crawl_summary_baseline_note = ""
    wb.filtered_retweet_count = 2
    wb.skipped_invisible_count = 0
    wb.media_failure_count = 0
    wb.get_filepath = lambda _type: tmpdir
    wb._report_output_dir = lambda _dir: os.path.join(tmpdir, "report", "测试用户")
    wb._atomic_write_text = lambda path, text: (
        os.makedirs(os.path.dirname(path), exist_ok=True),
        open(path, "w", encoding="utf-8").write(text),
    ) and None
    wb._record_write_failure = lambda stage, target, error: wb.run_failures.append(
        {"stage": stage, "target": target, "error": error, "time": "now"}
    )
    wb._ensure_archive_state = lambda: {"weibos": {}}
    wb._state_path = os.path.join(tmpdir, "archive_state.json")
    wb._locate_block_file = lambda wid, item, created: (None, "")
    return wb


def _weibo_item(wid, created_at, text):
    return {
        "id": wid,
        "created_at": created_at,
        "text": text,
        "pics": "",
        "links": [],
        "edit_at": "",
        "visible": {},
    }


class EndToEndTest(unittest.TestCase):
    def test_write_md_then_reports(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb = _make_wb(tmpdir)
            items = [
                _weibo_item(300, "2024-05-03T12:00:00", "第二条"),
                _weibo_item(100, "2024-05-03T10:00:00", "第一条"),
            ]
            wb.weibo = items
            with mock.patch("weibo.logger"):
                # 1) 写 markdown（按发布时间插入）
                wb.generate_markdown_file("2024-05-03", items)
                md_path = os.path.join(tmpdir, "2024-05-03.md")
                self.assertTrue(os.path.isfile(md_path))
                with open(md_path, encoding="utf-8") as handle:
                    md_text = handle.read()
                doc = markdown_blocks.parse_markdown(
                    md_text, fallback_date="2024-05-03"
                )
                self.assertEqual(
                    [block.weibo_id for block in doc.blocks], ["100", "300"]
                )
                self.assertIn("### 10:00:00\n\n<!-- weibo_id: 100 -->", md_text)

                # 2) 本轮抓取情况报告
                wb.generate_crawl_summary_report()
                # 3) 变化报告
                wb._generate_change_report()

            report_dir = os.path.join(tmpdir, "report", "测试用户")
            files = os.listdir(report_dir)
            crawl_reports = [
                name for name in files if name.startswith("weibo_crawl_summary_")
            ]
            change_reports = [
                name for name in files if name.startswith("weibo_change_report_")
            ]
            self.assertEqual(len(crawl_reports), 1)
            self.assertEqual(len(change_reports), 1)
            with open(
                os.path.join(report_dir, crawl_reports[0]), encoding="utf-8"
            ) as handle:
                crawl_text = handle.read()
            self.assertIn("本轮抓到微博数: 2 条", crawl_text)
            self.assertIn("四、平台数量对比（本轮全量实测）", crawl_text)
            with open(
                os.path.join(report_dir, change_reports[0]), encoding="utf-8"
            ) as handle:
                change_text = handle.read()
            self.assertIn("微博存档变化报告", change_text)
            # 无写入失败
            self.assertEqual(wb.run_failures, [])


if __name__ == "__main__":
    unittest.main()
