"""覆盖：
- ：本地媒体损坏判定不再假阴性（2 字节垃圾、截断 mp4、GIF 分号误命中）；
- ：核对报告「本地文件」路径以 output_directory 为基准，不再显示 ../；
- ：全量核对脚本以 readonly 模式运行（不写 users.csv、不建用户目录）；
- ：关键词（query）用户被跳过，与主流程口径一致。"""

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path


from tests import _support  # noqa: E402

_support.bootstrap()

import weibo  # noqa: E402
from util.report import archive_check_core  # noqa: E402
from util.media import media_check  # noqa: E402
from util.media import media_report  # noqa: E402

_SCRIPTS_DIR = str(Path(__file__).resolve().parents[2] / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
import weibo_archive_check_full as full_check  # noqa: E402

sys.modules.pop("json5", None)


class MediaCorruptionTest(unittest.TestCase):
    ""

    def _write(self, tmp: str, name: str, data: bytes) -> str:
        path = os.path.join(tmp, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def test_tiny_garbage_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "tiny.jpg", b"\x00\x01")
            ok, reason = media_check.validate_file(path)
            self.assertFalse(ok, reason)
            self.assertIn("过小", reason)

    def test_truncated_mp4_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "trunc.mp4", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 12)
            ok, reason = media_check.validate_file(path)
            self.assertFalse(ok, reason)

    def test_mp4_without_moov_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(
                tmp, "no_moov.mp4", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 200
            )
            ok, reason = media_check.validate_file(path)
            self.assertFalse(ok, reason)
            self.assertIn("容器", reason)

    def test_gif_semicolon_in_tail_but_not_at_end_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = b"GIF89a" + b"\x00" * 50 + b";" + b"\x00" * 50
            path = self._write(tmp, "fake.gif", data)
            ok, reason = media_check.validate_file(path)
            self.assertFalse(ok, reason)
            self.assertIn("分号", reason)

    def test_valid_media_still_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            jpg = self._write(tmp, "ok.jpg", b"\xff\xd8\xff" + b"0" * 100 + b"\xff\xd9")
            mp4 = self._write(
                tmp, "ok.mp4", b"\x00\x00\x00\x18ftypmp42" + b"0" * 200 + b"moov"
            )
            self.assertTrue(media_check.validate_file(jpg)[0])
            self.assertTrue(media_check.validate_file(mp4)[0])


class ArchiveReportPathTest(unittest.TestCase):
    """报告里的本地路径基准是 output_directory。"""

    def test_local_path_relative_to_output_root(self) -> None:
        diff = types.SimpleNamespace(
            not_returned=["101"],
            not_saved=[],
            local_entries={"101": (None, "/data/weibo_data/用户A/2026-10/2026-10-22.md")},
        )
        report = archive_check_core.render_report(
            "本轮存档核对报告",
            "123",
            "用户A",
            diff,
            header_lines=["说明: 本地文件路径相对于 output_directory。"],
            base_dir="/data/weibo_data",
        )
        self.assertIn("用户A/2026-10/2026-10-22.md", report.replace(os.sep, "/"))
        self.assertNotIn("../", report)

    def test_relpath_failure_falls_back_to_absolute(self) -> None:
        """Windows 跨盘 relpath 抛 ValueError 时回退绝对路径而不是让报告失败。"""
        orig = os.path.relpath
        try:
            os.path.relpath = lambda *a, **k: (_ for _ in ()).throw(ValueError("跨盘"))
            diff = types.SimpleNamespace(
                not_returned=["101"],
                not_saved=[],
                local_entries={"101": (None, "/data/a.md")},
            )
            report = archive_check_core.render_report(
                "本轮存档核对报告", "123", "用户A", diff, base_dir="/data"
            )
            self.assertIn("/data/a.md", report)
        finally:
            os.path.relpath = orig


class ReadonlyModeTest(unittest.TestCase):
    """全量核对只读。"""

    def test_user_to_database_skipped_in_readonly(self) -> None:
        wb = weibo.Weibo.__new__(weibo.Weibo)
        wb.readonly_mode = True
        wb.write_mode = ["csv", "sqlite", "mysql", "mongo"]
        called = []
        wb.user_to_csv = lambda: called.append("csv")
        wb.user_to_mysql = lambda: called.append("mysql")
        wb.user_to_sqlite = lambda: called.append("sqlite")
        wb.user_to_mongodb = lambda: called.append("mongo")
        wb.user_to_database()
        self.assertEqual(called, [])

    def test_get_filepath_does_not_create_dir_in_readonly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wb = weibo.Weibo.__new__(weibo.Weibo)
            wb.readonly_mode = True
            wb.output_directory = tmp
            wb.user = {"screen_name": "读者A"}
            wb.user_config = {"user_id": "123"}
            wb.user_id_as_folder_name = 0
            path = wb.get_filepath("markdown")
            self.assertIn("读者A", path)
            self.assertFalse(os.path.isdir(path))


class CommentImageReportTest(unittest.TestCase):
    """（D5）：评论图片纳入媒体完整性报告并单独标注。"""

    def test_comment_image_scanned_and_marked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user = Path(tmp) / "用户A"
            (user / "img").mkdir(parents=True)
            (user / "img" / "a.jpg").write_bytes(
                b"\xff\xd8\xff" + b"0" * 10 + b"\xff\xd9"
            )
            md = user / "2024-05-03.md"
            md.write_text(
                "### 10:00:00\n<!-- weibo_id: 101 -->\n"
                "![img](img/a.jpg)\n\n---\n\n"
                "#### 评论\n<!-- comments: 101 total=1 saved=1 -->\n"
                "> **甲** · 10:00:00\n> 文字\n"
                "> ![评论图片](评论图片/missing.jpg)\n"
                "<!-- /comments -->\n",
                encoding="utf-8",
            )
            issues, _links_total, _ok_count = media_report._scan_md_file_full(str(md))
            self.assertEqual(len(issues), 1)
            self.assertTrue(issues[0].is_comment)
            result = media_report.MediaScanResult(
                files_scanned=1, links_total=2, ok_count=1, issues=issues
            )
            report = media_report.render_media_report(
                "123", "用户A", result, output_root=tmp
            )
            self.assertIn("评论图片问题: 1", report)
            self.assertIn("来源: 评论图片", report)


class QueryUserSkipTest(unittest.TestCase):
    """关键词用户跳过全量核对。"""

    def test_query_user_skipped_before_network(self) -> None:
        wb = types.SimpleNamespace(
            initialize_info=lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应初始化"))
        )
        args = types.SimpleNamespace(since=None, max_pages=1)
        code = full_check.audit_one_user(
            wb, {"user_id": "123", "query_list": ["梦想"]}, args
        )
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
