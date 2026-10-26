"""存档核对报告生成逻辑的集成测试（无网络、临时目录）。

weibo.py 依赖若干第三方库；测试环境未安装时用轻量 stub 顶替，
只验证 generate_archive_check_report() 的判定与输出，不触发任何抓取逻辑。
"""

import os
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

    requests = add("requests", Session=object, get=lambda *a, **k: None)
    add("requests.exceptions", RequestException=Exception)
    add("requests.adapters", HTTPAdapter=object)
    add("json5", loads=lambda text: {})
    add("tqdm", tqdm=lambda iterable, **kwargs: iterable)
    lxml = add("lxml")
    lxml.etree = add("lxml.etree")
    add("piexif")


_install_import_stubs()

import weibo  # noqa: E402

# 上面的 json5 只是为了让 weibo.py 可导入；测试结束前移除，避免影响
# util.config_migrate 的“无 json5 降级解析”测试。
sys.modules.pop("json5", None)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_md(*entries: str) -> str:
    return "## 2024-06-01 [测试用户] 微博存档\n\n" + "\n".join(entries)


def entry(time_text: str, weibo_id: str) -> str:
    return f"### {time_text}\n<!-- weibo_id: {weibo_id} -->\n内容\n\n---\n"


class ArchiveCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.user_dir = Path(self.tmp.name) / "测试用户"
        self.user_dir.mkdir()
        self.out_dir_name = f"_test_output_{id(self)}"
        self.wb = weibo.Weibo.__new__(weibo.Weibo)
        self.wb.archive_check_enabled = 1
        self.wb.write_mode = ["markdown"]
        self.wb.output_directory = self.out_dir_name
        self.wb.invisible_weibo_dirs = ["已不可见"]
        self.wb.user_config = {
            "user_id": "123",
            "screen_name": "测试用户",
            "since_date": "2024-06-01T00:00:00",
            "since_date_source": "user_list",
            "query_list": [],
        }
        self.wb.user = {"id": "123", "screen_name": "测试用户"}
        self.wb.weibo = []
        self.wb.start_date = "2024-06-30T00:00:00"
        self.wb.crawled_ids_this_run = set()
        self.wb.get_filepath = lambda _type: str(self.user_dir)

    def tearDown(self) -> None:
        self.tmp.cleanup()
        shutil.rmtree(
            Path(__file__).resolve().parents[1] / self.out_dir_name, ignore_errors=True
        )

    def _collect_window(self):
        from util import archive_index

        start = datetime(2024, 6, 1)
        end = datetime(2024, 6, 30)
        return archive_index.collect_archive_entries(
            str(self.user_dir), start, end, self.wb.invisible_weibo_dirs
        )[0]

    def _mark_success(self) -> None:
        self.wb.crawl_success = {
            "attempted": True,
            "success": True,
            "reason": "命中起始时间下界，本轮抓取顺利完成",
        }
        entries = self._collect_window()
        self.wb.archived_entries_before = entries
        self.wb.archived_ids_before = {
            wid: dt for wid, (dt, _path) in entries.items()
        }
        self.wb.check_start_dt = datetime(2024, 6, 1)
        self.wb.check_end_dt = datetime(2024, 6, 30)

    def _reports(self, sub: str = "report"):
        root = Path(__file__).resolve().parents[1] / self.out_dir_name / sub
        return sorted(root.glob("weibo_archive_check_*.txt")) if root.exists() else []

    def test_snapshot_archived_ids_succeeds(self) -> None:
        """抓取前快照必须成功（曾因日志里引用未定义变量而被异常吞掉）。"""
        write_text(
            self.user_dir / "2024-06" / "2024-06-01.md",
            make_md(entry("2024-06-01 10:00:00", "1111111111111111")),
        )
        self.wb._snapshot_archived_ids()
        self.assertIsNotNone(self.wb.archived_ids_before)
        self.assertIsNotNone(self.wb.archived_entries_before)
        self.assertEqual(self.wb.check_start_dt, datetime(2024, 6, 1))
        self.assertIn("1111111111111111", self.wb.archived_ids_before)

    def test_not_returned_count_detected(self) -> None:
        """本次检查范围内主存档有、本轮没返回 → 本轮未抓到的已存档微博。"""
        write_text(
            self.user_dir / "2024-06" / "2024-06-01.md",
            make_md(entry("10:00:00", "1001"), entry("11:00:00", "1002")),
        )
        self._mark_success()
        self.wb.crawled_ids_this_run = {"1002"}
        self.wb.generate_archive_check_report()
        reports = self._reports()
        self.assertEqual(len(reports), 1)
        text = reports[0].read_text(encoding="utf-8")
        self.assertIn("https://m.weibo.cn/detail/1001", text)
        self.assertNotIn("https://m.weibo.cn/detail/1002", text)
        self.assertIn("发布日期: 2024-06-01", text)
        self.assertIn("本地文件:", text)
        self.assertIn("not_returned_count: 1", text)

    def test_no_diff_still_generates_report(self) -> None:
        """本次检查范围内完全一致 → 仍生成报告，并标注无差异。"""
        write_text(
            self.user_dir / "2024-06" / "2024-06-01.md",
            make_md(entry("10:00:00", "2001")),
        )
        self._mark_success()
        self.wb.crawled_ids_this_run = {"2001"}
        self.wb.generate_archive_check_report()
        reports = self._reports()
        self.assertEqual(len(reports), 1)
        text = reports[0].read_text(encoding="utf-8")
        self.assertIn("核对结果: 无差异", text)
        self.assertIn("（无）", text)
        self.assertIn("not_returned_count: 0", text)
        self.assertIn("not_saved_count: 0", text)

    def test_custom_report_dir(self) -> None:
        """weibo_archive_check_dir 可自定义（相对 output_directory）。"""
        write_text(
            self.user_dir / "2024-06" / "2024-06-01.md",
            make_md(entry("10:00:00", "2001")),
        )
        self._mark_success()
        self.wb.crawled_ids_this_run = {"2001"}
        self.wb.archive_check_dir = "custom_reports"
        self.wb.generate_archive_check_report()
        self.assertEqual(len(self._reports("custom_reports")), 1)
        self.assertEqual(self._reports(), [])

    def test_report_output_dir_resolution(self) -> None:
        """目录配置解析：空=默认 report；相对=相对 output_directory；绝对=原样。"""
        project = Path(weibo.__file__).resolve().parent
        self.assertEqual(
            self.wb._report_output_dir(""),
            str(project / self.out_dir_name / "report"),
        )
        self.assertEqual(
            self.wb._report_output_dir("x/y"),
            str(project / self.out_dir_name / "x/y"),
        )
        abs_path = str(project / "abs_reports")
        self.assertEqual(self.wb._report_output_dir(abs_path), abs_path)

    def test_crawl_not_success_skips_report(self) -> None:
        """本轮抓取未顺利完成 → 不出报告（避免误报）。"""
        write_text(
            self.user_dir / "2024-06" / "2024-06-01.md",
            make_md(entry("10:00:00", "3001")),
        )
        self._mark_success()
        self.wb.crawl_success = {
            "attempted": True,
            "success": False,
            "reason": "第3页请求异常",
        }
        self.wb.crawled_ids_this_run = set()
        self.wb.generate_archive_check_report()
        self.assertEqual(self._reports(), [])

    def test_not_saved_count_detected(self) -> None:
        """本次爬到但主存档没有 → 未保存的微博（保存异常线索）。"""
        write_text(
            self.user_dir / "2024-06" / "2024-06-01.md",
            make_md(entry("10:00:00", "4001")),
        )
        self._mark_success()
        # 4001 已存档；4002 本次爬到但不在存档里
        self.wb.crawled_ids_this_run = {"4001", "4002"}
        self.wb.weibo = [{"id": "4002", "created_at": "2024-06-01T10:00:00"}]
        self.wb.generate_archive_check_report()
        reports = self._reports()
        self.assertEqual(len(reports), 1)
        text = reports[0].read_text(encoding="utf-8")
        self.assertIn("未保存的微博", text)
        self.assertIn("https://m.weibo.cn/detail/4002", text)
        self.assertIn("发布日期: 2024-06-01", text)
        self.assertIn("本地文件: （本地无文件）", text)
        self.assertIn("not_saved_count: 1", text)

    def test_known_gone_not_treated_as_gap(self) -> None:
        """已确认不可见的微博里的 ID 不应被当成未保存的微博。"""
        write_text(
            self.user_dir / "已不可见" / "2024-06" / "2024-06-02.md",
            make_md(entry("10:00:00", "5001")),
        )
        write_text(
            self.user_dir / "2024-06" / "2024-06-01.md",
            make_md(entry("10:00:00", "5002")),
        )
        self._mark_success()
        self.wb.crawled_ids_this_run = {"5002", "5001"}
        self.wb.generate_archive_check_report()
        reports = self._reports()
        self.assertEqual(len(reports), 1)
        text = reports[0].read_text(encoding="utf-8")
        # 5001 属于已确认不可见，不应被当成「未保存的微博」
        self.assertNotIn("https://m.weibo.cn/detail/5001", text)
        self.assertIn("not_saved_count: 0", text)
        self.assertIn("核对结果: 无差异", text)

    def test_query_mode_skips(self) -> None:
        """关键词(query)模式跳过存档核对。"""
        write_text(
            self.user_dir / "2024-06" / "2024-06-01.md",
            make_md(entry("10:00:00", "6001")),
        )
        self._mark_success()
        self.wb.user_config["query_list"] = ["关键词"]
        self.wb.generate_archive_check_report()
        self.assertEqual(self._reports(), [])

    def test_crawl_success_rules(self) -> None:
        """本轮抓取是否顺利完成的判定规则（命中下界 / 页数 / 接口结束 / 异常 / 断点续爬）。"""
        wb = weibo.Weibo.__new__(weibo.Weibo)
        wb.start_page = 1

        # 命中起始时间下界 → 完整
        wb.crawl_success = {"success": False, "hit_since_date": True, "page_errors": []}
        wb._finalize_crawl_success(1, 100)
        self.assertTrue(wb.crawl_success["success"])

        # 单页异常 → 不完整
        wb.crawl_success = {"success": False, "page_errors": [3]}
        wb._finalize_crawl_success(5, 100)
        self.assertFalse(wb.crawl_success["success"])

        # 断点续爬（start_page > 1）→ 不完整
        wb.start_page = 2
        wb.crawl_success = {"success": False, "page_errors": []}
        wb._finalize_crawl_success(2, 100)
        self.assertFalse(wb.crawl_success["success"])

        # 接口提前返回无数据 → 不完整
        wb.start_page = 1
        wb.crawl_success = {"success": False, "page_errors": [], "api_ended": True}
        wb._finalize_crawl_success(3, 100)
        self.assertFalse(wb.crawl_success["success"])

        # 接口在最后一页正常结束 → 完整
        wb.crawl_success = {"success": False, "page_errors": [], "api_ended": True}
        wb._finalize_crawl_success(100, 100)
        self.assertTrue(wb.crawl_success["success"])

        # 扫完 page_count → 完整
        wb.crawl_success = {"success": False, "page_errors": []}
        wb._finalize_crawl_success(100, 100)
        self.assertTrue(wb.crawl_success["success"])


if __name__ == "__main__":
    unittest.main()
