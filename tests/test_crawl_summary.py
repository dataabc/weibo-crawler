"""本轮抓取情况报告的纯函数与生成逻辑测试（无网络、临时目录）。"""

import shutil
import sys
import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path


def _install_import_stubs() -> None:
    """让 weibo.py 能在未安装依赖的环境里被导入。"""
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

import weibo  # noqa: E402
from util import crawl_summary  # noqa: E402

sys.modules.pop("json5", None)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_md(*entries: str) -> str:
    return "## 2024-06-01 [测试用户] 微博存档\n\n" + "\n".join(entries)


def entry(time_text: str, weibo_id: str) -> str:
    return f"### {time_text}\n<!-- weibo_id: {weibo_id} -->\n内容\n\n---\n"


class BuildSummaryTest(unittest.TestCase):
    def test_split_new_and_existing(self) -> None:
        summary = crawl_summary.build_crawl_summary(
            ["1001", "1002", "2001"],
            {"1001": datetime(2024, 6, 1, 10), "2001": datetime(2024, 6, 2, 11)},
            archived_ids={"1001", "1002"},
            archived_count=10,
        )
        self.assertEqual(summary.crawled_count, 3)
        self.assertEqual(summary.existing_count, 2)
        self.assertEqual(summary.new_count, 1)
        self.assertEqual([wid for wid, _dt in summary.new_entries], ["2001"])
        self.assertEqual(summary.archived_count, 10)
        self.assertTrue(summary.baseline_available)

    def test_sort_new_by_id_desc_and_dedupe(self) -> None:
        summary = crawl_summary.build_crawl_summary(
            ["20", "100", "20", "3"], {}, archived_ids=set()
        )
        self.assertEqual([wid for wid, _dt in summary.new_entries], ["100", "20", "3"])
        self.assertEqual(summary.crawled_count, 3)

    def test_month_distribution(self) -> None:
        summary = crawl_summary.build_crawl_summary(
            ["1", "2", "3"],
            {
                "1": datetime(2024, 6, 1, 10),
                "2": datetime(2024, 6, 2, 10),
                "3": datetime(2024, 7, 1, 10),
            },
            archived_ids=set(),
        )
        self.assertEqual(summary.month_counts, {"2024-06": 2, "2024-07": 1})
        self.assertEqual(summary.earliest_new, datetime(2024, 6, 1, 10))
        self.assertEqual(summary.latest_new, datetime(2024, 7, 1, 10))

    def test_unknown_dates(self) -> None:
        summary = crawl_summary.build_crawl_summary(
            ["1", "2"],
            {"1": None, "2": datetime(2024, 6, 1, 10)},
            archived_ids=set(),
        )
        self.assertEqual(summary.new_count, 2)
        self.assertEqual(summary.unknown_date_count, 1)
        self.assertEqual(summary.month_counts, {"2024-06": 1})

    def test_baseline_unavailable_keeps_total_only(self) -> None:
        summary = crawl_summary.build_crawl_summary(
            ["1", "2"], {}, archived_ids={"1"}, archived_count=5,
            baseline_available=False,
        )
        self.assertEqual(summary.crawled_count, 2)
        self.assertEqual(summary.new_count, 0)
        self.assertEqual(summary.existing_count, 0)
        self.assertFalse(summary.baseline_available)
        self.assertIsNone(summary.archived_count)

    def test_parse_created_at(self) -> None:
        self.assertEqual(
            crawl_summary.parse_created_at("2024-06-01T10:00:00"),
            datetime(2024, 6, 1, 10),
        )
        self.assertIsNone(crawl_summary.parse_created_at(""))
        self.assertIsNone(crawl_summary.parse_created_at("2024-06-01"))


class RenderSummaryTest(unittest.TestCase):
    def _info(self, **kwargs):
        defaults = dict(
            user_id="123",
            screen_name="测试用户",
            mode="incremental",
            mode_label="增量",
            source_label="用户列表第三列",
            range_start=datetime(2024, 6, 1),
            range_end=datetime(2024, 6, 30),
            success=True,
            success_reason="命中起始时间下界，本轮抓取顺利完成",
            pages_processed=3,
            page_count=3,
            page_errors=0,
            skipped_invisible=1,
            filtered_retweet=2,
            media_failures=0,
            started_at=datetime(2024, 6, 30, 10, 0, 0),
            finished_at=datetime(2024, 6, 30, 10, 1, 5),
        )
        defaults.update(kwargs)
        return crawl_summary.CrawlRunInfo(**defaults)

    def test_report_contains_required_fields(self) -> None:
        summary = crawl_summary.build_crawl_summary(
            ["1001", "2001"],
            {
                "1001": datetime(2024, 6, 1, 10),
                "2001": datetime(2024, 6, 2, 11),
            },
            archived_ids={"1001"},
            archived_count=7,
        )
        text = crawl_summary.render_crawl_summary(self._info(), summary)
        self.assertIn("本轮抓取情况报告", text)
        self.assertIn("用户: 测试用户 (ID: 123)", text)
        self.assertIn("本轮抓取方式: 增量（起始时间来自: 用户列表第三列）", text)
        self.assertIn("本轮抓取是否顺利完成: 是", text)
        self.assertIn("耗时: 1 分 5 秒", text)
        self.assertIn("抓取前已存档微博数（抓取范围内）: 7 条", text)
        self.assertIn("本轮抓到微博数: 2 条", text)
        self.assertIn("其中已抓取过（抓取前本地存档已有）: 1 条", text)
        self.assertIn("本轮新抓到: 1 条", text)
        self.assertIn("已确认不可见而跳过写入: 1 条", text)
        self.assertIn("因只抓原创而跳过的转发: 2 条", text)
        self.assertIn("https://m.weibo.cn/detail/2001", text)
        self.assertNotIn("https://m.weibo.cn/detail/1001", text)
        self.assertIn("发布日期: 2024-06-02 11:00:00", text)
        self.assertIn("2024-06: 1 条", text)
        self.assertIn("new_count: 1", text)
        self.assertIn("already_archived_count: 1", text)
        self.assertIn("报告完毕。", text)

    def test_no_new_posts(self) -> None:
        summary = crawl_summary.build_crawl_summary(
            ["1001"], {"1001": datetime(2024, 6, 1, 10)}, archived_ids={"1001"}
        )
        text = crawl_summary.render_crawl_summary(self._info(), summary)
        self.assertIn("本轮新抓到: 0 条", text)
        self.assertIn("（无）", text)

    def test_baseline_unavailable_render(self) -> None:
        summary = crawl_summary.build_crawl_summary(
            ["1001"], {}, archived_ids=set(), baseline_available=False
        )
        text = crawl_summary.render_crawl_summary(
            self._info(baseline_note="write_mode 不含 markdown"), summary
        )
        self.assertIn("抓取前已存档微博数（抓取范围内）: 无法获取（write_mode 不含 markdown）", text)
        self.assertIn("其中已抓取过（抓取前本地存档已有）: 无法判断", text)
        self.assertIn("本轮新抓到: 无法判断", text)
        self.assertIn("baseline_available: false", text)

    def test_failed_crawl_render_status(self) -> None:
        summary = crawl_summary.build_crawl_summary(["1"], {}, archived_ids=set())
        text = crawl_summary.render_crawl_summary(
            self._info(success=False, success_reason="第3页请求异常"), summary
        )
        self.assertIn("本轮抓取是否顺利完成: 否（第3页请求异常）", text)
        self.assertIn("crawl_success: false", text)

    def test_format_elapsed(self) -> None:
        self.assertEqual(crawl_summary.format_elapsed(None), "未知")
        self.assertEqual(crawl_summary.format_elapsed(0), "0 秒")
        self.assertEqual(crawl_summary.format_elapsed(59), "59 秒")
        self.assertEqual(crawl_summary.format_elapsed(60), "1 分 0 秒")
        self.assertEqual(crawl_summary.format_elapsed(125), "2 分 5 秒")


class GenerateReportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.user_dir = Path(self.tmp.name) / "测试用户"
        self.user_dir.mkdir()
        self.out_dir_name = f"_test_crawl_summary_{id(self)}"
        self.wb = weibo.Weibo.__new__(weibo.Weibo)
        self.wb.crawl_summary_enabled = 1
        self.wb.archive_check_enabled = 0
        self.wb.write_mode = ["markdown"]
        self.wb.output_directory = self.out_dir_name
        self.wb.invisible_weibo_dirs = ["已不可见"]
        self.wb.user_config = {
            "user_id": "123",
            "since_date": "2024-06-01T00:00:00",
            "since_date_source": "user_list",
            "query_list": [],
        }
        self.wb.user = {"id": "123", "screen_name": "测试用户"}
        self.wb.weibo = []
        self.wb.start_date = "2024-06-30T00:00:00"
        self.wb.crawled_ids_this_run = set()
        self.wb.archived_ids_before = None
        self.wb.archived_entries_before = None
        self.wb.archive_scan_stats = None
        self.wb.crawl_summary_baseline_note = ""
        self.wb.skipped_invisible_count = 0
        self.wb.filtered_retweet_count = 0
        self.wb.media_failure_count = 0
        self.wb.crawl_success = {
            "attempted": True,
            "success": True,
            "reason": "命中起始时间下界，本轮抓取顺利完成",
            "pages_processed": 2,
            "page_count": 2,
            "page_errors": [],
        }
        self.wb.get_filepath = lambda _type: str(self.user_dir)

    def tearDown(self) -> None:
        self.tmp.cleanup()
        shutil.rmtree(
            Path(__file__).resolve().parents[1] / self.out_dir_name, ignore_errors=True
        )

    def _reports(self, sub: str = "report"):
        root = Path(__file__).resolve().parents[1] / self.out_dir_name / sub
        return sorted(root.glob("weibo_crawl_summary_*.txt")) if root.exists() else []

    def test_disabled_skips_report(self) -> None:
        self.wb.crawl_summary_enabled = 0
        self.wb.generate_crawl_summary_report()
        self.assertEqual(self._reports(), [])

    def test_success_generates_report_with_new_and_existing(self) -> None:
        write_text(
            self.user_dir / "2024-06" / "2024-06-01.md",
            make_md(entry("10:00:00", "1001"), entry("11:00:00", "1002")),
        )
        self.wb._snapshot_archived_ids()  # 仅开抓取情况报告时也要构建基准
        self.assertIsNotNone(self.wb.archived_entries_before)
        self.wb.crawled_ids_this_run = {"1001", "2001"}
        self.wb.weibo = [
            {"id": "1001", "created_at": "2024-06-01T10:00:00"},
            {"id": "2001", "created_at": "2024-06-02T11:30:00"},
        ]
        self.wb.generate_crawl_summary_report()
        reports = self._reports()
        self.assertEqual(len(reports), 1)
        text = reports[0].read_text(encoding="utf-8")
        self.assertIn("本轮抓到微博数: 2 条", text)
        self.assertIn("其中已抓取过（抓取前本地存档已有）: 1 条", text)
        self.assertIn("本轮新抓到: 1 条", text)
        self.assertIn("https://m.weibo.cn/detail/2001", text)
        self.assertNotIn("https://m.weibo.cn/detail/1001", text)
        self.assertIn("发布日期: 2024-06-02 11:30:00", text)
        self.assertIn("new_count: 1", text)

    def test_custom_summary_dir(self) -> None:
        """weibo_crawl_summary_dir 可自定义（相对 output_directory）。"""
        self.wb._snapshot_archived_ids()
        self.wb.crawled_ids_this_run = {"1001"}
        self.wb.weibo = [{"id": "1001", "created_at": "2024-06-01T10:00:00"}]
        self.wb.crawl_summary_dir = "custom_summary"
        self.wb.generate_crawl_summary_report()
        self.assertEqual(len(self._reports("custom_summary")), 1)
        self.assertEqual(self._reports(), [])

    def test_failed_crawl_skips_report(self) -> None:
        """按拍板结论：仅抓取顺利完成时生成。"""
        self.wb.crawl_success = {
            "attempted": True,
            "success": False,
            "reason": "第3页请求异常",
            "page_errors": [3],
        }
        self.wb.crawled_ids_this_run = {"1001"}
        self.wb.generate_crawl_summary_report()
        self.assertEqual(self._reports(), [])

    def test_query_mode_skips_report(self) -> None:
        self.wb.user_config["query_list"] = ["关键词"]
        self.wb.crawled_ids_this_run = {"1001"}
        self.wb.generate_crawl_summary_report()
        self.assertEqual(self._reports(), [])

    def test_not_attempted_skips_report(self) -> None:
        self.wb.crawl_success = {"attempted": False, "success": False, "reason": "未开始"}
        self.wb.generate_crawl_summary_report()
        self.assertEqual(self._reports(), [])

    def test_baseline_unavailable_still_generates(self) -> None:
        self.wb.write_mode = []
        self.wb.crawl_summary_baseline_note = "write_mode 不含 markdown"
        self.wb.archived_entries_before = None
        self.wb.crawled_ids_this_run = {"1001"}
        self.wb.weibo = [{"id": "1001", "created_at": "2024-06-01T10:00:00"}]
        self.wb.generate_crawl_summary_report()
        reports = self._reports()
        self.assertEqual(len(reports), 1)
        text = reports[0].read_text(encoding="utf-8")
        self.assertIn("本轮抓到微博数: 1 条", text)
        self.assertIn("无法判断", text)
        self.assertIn("baseline_available: false", text)

    def test_snapshot_builds_when_only_summary_enabled(self) -> None:
        write_text(
            self.user_dir / "2024-06" / "2024-06-01.md",
            make_md(entry("10:00:00", "1111111111111111")),
        )
        self.wb._snapshot_archived_ids()
        self.assertIsNotNone(self.wb.archived_entries_before)
        self.assertIn("1111111111111111", self.wb.archived_ids_before)
        self.assertEqual(self.wb.crawl_summary_baseline_note, "")


if __name__ == "__main__":
    unittest.main()
