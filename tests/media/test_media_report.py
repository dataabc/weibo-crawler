"""媒体完整性报告：链接扫描、问题分类与生成逻辑测试。"""

import shutil
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from util.media import media_report




def make_md(*entries: str) -> str:
    return "## 2024-06-01 [测试用户] 微博存档\n\n" + "\n".join(entries)


def entry(time_text: str, weibo_id: str, body: str = "内容") -> str:
    return f"### {time_text}\n<!-- weibo_id: {weibo_id} -->\n{body}\n\n---\n"


class ScanMediaTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.user_dir = Path(self.tmp.name) / "测试用户"
        self.user_dir.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_scan_finds_missing_empty_invalid(self) -> None:
        month = self.user_dir / "2024-06"
        # 正常文件
        (month / "img").mkdir(parents=True)
        (month / "img" / "ok.jpg").write_bytes(JPEG)
        # 空文件
        (month / "img" / "empty.jpg").write_bytes(b"")
        # 损坏文件
        (month / "img" / "broken.jpg").write_bytes(JPEG[:-2])
        write_text(
            month / "2024-06-01.md",
            make_md(
                entry("10:00:00", "1001", "![img](img/ok.jpg)"),
                entry("11:00:00", "1002", "![img](img/missing.jpg)"),
                entry("12:00:00", "1003", "![img](img/empty.jpg)"),
                entry("13:00:00", "1004", "![img](img/broken.jpg)"),
                entry("14:00:00", "1005", "![img](https://example.com/a.jpg)"),
            ),
        )
        result = media_report.scan_user_media(str(self.user_dir), ["已不可见"])
        self.assertEqual(result.files_scanned, 1)
        self.assertEqual(result.links_total, 4)  # 外链不计
        self.assertEqual(result.ok_count, 1)
        self.assertEqual(result.count("missing"), 1)
        self.assertEqual(result.count("empty"), 1)
        self.assertEqual(result.count("invalid"), 1)
        ids = {issue.weibo_id for issue in result.issues}
        self.assertEqual(ids, {"1002", "1003", "1004"})

    def test_scan_ignores_invisible_dir(self) -> None:
        write_text(
            self.user_dir / "已不可见" / "2024-06" / "2024-06-01.md",
            make_md(entry("10:00:00", "2001", "![img](img/x.jpg)")),
        )
        result = media_report.scan_user_media(str(self.user_dir), ["已不可见"])
        self.assertEqual(result.files_scanned, 0)
        self.assertEqual(result.issues, [])

    def test_render_report(self) -> None:
        result = media_report.MediaScanResult(
            files_scanned=2,
            links_total=3,
            ok_count=2,
            issues=[
                media_report.MediaIssue(
                    "missing",
                    "/tmp/u/2024-06/2024-06-01.md",
                    "img/x.jpg",
                    "/tmp/u/2024-06/img/x.jpg",
                    "3001",
                    datetime(2024, 6, 1, 10),
                    "文件不存在",
                )
            ],
        )
        text = media_report.render_media_report(
            "123", "测试用户", result, output_root="/tmp/u"
        )
        self.assertIn("媒体完整性报告", text)
        self.assertIn("用户: 测试用户 (ID: 123)", text)
        self.assertIn("媒体链接: 3 条；正常: 2 条；问题: 1 条", text)
        self.assertIn("https://m.weibo.cn/detail/3001", text)
        self.assertIn("发布日期: 2024-06-01 10:00:00", text)
        self.assertIn("本地路径: 2024-06/img/x.jpg", text)
        self.assertIn("media_missing: 1", text)
        self.assertIn("报告完毕。", text)


from tests import _support  # noqa: E402
from tests._support import JPEG  # noqa: E402
from tests._support import write_text  # noqa: E402

_support.bootstrap()

import weibo  # noqa: E402

sys.modules.pop("json5", None)


class GenerateMediaReportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.user_dir = Path(self.tmp.name) / "测试用户"
        self.user_dir.mkdir()
        self.out_dir_name = f"_test_media_report_{id(self)}"
        self.wb = weibo.Weibo.__new__(weibo.Weibo)
        self.wb.media_check_enabled = 1
        self.wb.media_check_dir = "report"
        self.wb.write_mode = ["markdown"]
        self.wb.output_directory = self.out_dir_name
        self.wb.invisible_weibo_dirs = ["已不可见"]
        self.wb.user_config = {"user_id": "123", "query_list": []}
        self.wb.user = {"id": "123", "screen_name": "测试用户"}
        self.wb.crawl_success = {
            "attempted": True,
            "success": True,
            "reason": "命中起始时间下界，本轮抓取顺利完成",
        }
        self.wb.get_filepath = lambda _type: str(self.user_dir)

        # 这里给出「无下载来源记录」场景（假 session 不会被调用到 404 分支）
        class _ProbeSession:
            def get(self, *args, **kwargs):
                raise AssertionError("无下载来源时不应发起探测请求")

        self.wb.session = _ProbeSession()
        self.wb.headers = {}

    def tearDown(self) -> None:
        self.tmp.cleanup()
        shutil.rmtree(
            Path(__file__).resolve().parents[2] / self.out_dir_name, ignore_errors=True
        )

    def _reports(self):
        root = Path(__file__).resolve().parents[2] / self.out_dir_name / "report"
        # 报告按用户分目录（report/<用户>/），递归扫描
        return sorted(root.rglob("weibo_media_check_*.txt")) if root.exists() else []

    def test_generates_report(self) -> None:
        write_text(
            self.user_dir / "2024-06" / "2024-06-01.md",
            make_md(entry("10:00:00", "4001", "![img](img/missing.jpg)")),
        )
        self.wb.generate_media_check_report()
        reports = self._reports()
        self.assertEqual(len(reports), 1)
        text = reports[0].read_text(encoding="utf-8")
        self.assertIn("媒体完整性报告", text)
        self.assertIn("https://m.weibo.cn/detail/4001", text)
        self.assertIn("media_missing: 1", text)
        self.assertIn("归因: 无下载来源记录", text)

    def test_disabled_skips(self) -> None:
        self.wb.media_check_enabled = 0
        self.wb.generate_media_check_report()
        self.assertEqual(self._reports(), [])

    def test_non_markdown_skips(self) -> None:
        self.wb.write_mode = []
        self.wb.generate_media_check_report()
        self.assertEqual(self._reports(), [])




class DownloadUrlHintTest(unittest.TestCase):
    ""

    def test_missing_item_shows_download_source(self):
        from datetime import datetime

        issue = media_report.MediaIssue(
            kind="missing",
            md_path="/tmp/u/2024-05/2024-05-03.md",
            link="原创微博图片/x.jpg",
            resolved_path="/tmp/u/2024-05/原创微博图片/x.jpg",
            weibo_id="123",
            created_at=datetime(2024, 5, 3, 10, 0, 0),
            reason="文件不存在",
        )
        result = media_report.MediaScanResult()
        result.files_scanned = 1
        result.links_total = 1
        result.issues = [issue]
        text = media_report.render_media_report(
            "1",
            "测试用户",
            result,
            download_urls={
                "/tmp/u/2024-05/原创微博图片/x.jpg": (
                    "123",
                    "https://livephoto.example/x.mov",
                )
            },
        )
        self.assertIn("下载来源: 微博ID 123 | 原始URL https://livephoto.example/x.mov", text)

class RenderAttributionTest(unittest.TestCase):
    """缺失项归因渲染。"""

    def _result(self):
        result = media_report.MediaScanResult(
            files_scanned=1, links_total=3, ok_count=2
        )
        result.issues.append(
            media_report.MediaIssue(
                "missing",
                "/u/2023-09/2023-09-13.md",
                "原创微博Live_Photo视频/a.mov",
                "/u/2023-09/原创微博Live_Photo视频/a.mov",
                "1000000004",
                datetime(2023, 9, 13, 21, 50, 26),
                "文件不存在",
            )
        )
        return result

    def test_shows_attribution_line(self):
        from util.media import media_probe

        text = media_report.render_media_report(
            "1000000001",
            "测试用户",
            self._result(),
            attributions={
                "/u/2023-09/原创微博Live_Photo视频/a.mov": (
                    media_probe.ATTRIBUTION_PLATFORM_DELETED
                )
            },
        )
        self.assertIn("归因: 平台侧已删除", text)
        self.assertIn("media_missing_platform_deleted: 1", text)

    def test_without_attribution_keeps_old_format(self):
        text = media_report.render_media_report(
            "1000000001", "测试用户", self._result()
        )
        self.assertNotIn("归因:", text)
        self.assertNotIn("media_missing_platform_deleted", text)


if __name__ == "__main__":
    unittest.main()
