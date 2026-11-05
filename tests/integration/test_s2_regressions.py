"""覆盖：
- weibo.py `_atomic_write_text` / `_atomic_write_bytes`：临时文件 + fsync +
  os.replace；成功后无 .tmp 残留；替换失败时保留原文件（不截断）；"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


from tests import _support  # noqa: E402

_support.bootstrap()

import weibo  # noqa: E402
from util.comment import comment_markdown  # noqa: E402

_SCRIPTS_DIR = str(Path(__file__).resolve().parents[2] / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
import weibo_comment_backfill as backfill  # noqa: E402

sys.modules.pop("json5", None)


class AtomicWriteTest(unittest.TestCase):
    """_atomic_write_text / _atomic_write_bytes：不截断原文件、不留临时文件。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.wb = weibo.Weibo.__new__(weibo.Weibo)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_text_write_content_and_no_tmp_left(self) -> None:
        target = self.root / "sub" / "a.txt"
        self.wb._atomic_write_text(str(target), "hello 世界")
        self.assertEqual(target.read_text(encoding="utf-8"), "hello 世界")
        self.assertEqual(sorted(p.name for p in target.parent.iterdir()), ["a.txt"])

    def test_bytes_write_content_and_no_tmp_left(self) -> None:
        target = self.root / "img.bin"
        self.wb._atomic_write_bytes(str(target), b"\x00\x01\x02")
        self.assertEqual(target.read_bytes(), b"\x00\x01\x02")
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["img.bin"])

    def test_replace_failure_keeps_original_and_cleans_tmp(self) -> None:
        """模拟中断（os.replace 抛错）：原文件必须原样保留，不能出现半截文件。"""
        target = self.root / "a.txt"
        target.write_text("original", encoding="utf-8")
        with mock.patch("weibo.os.replace", side_effect=OSError("模拟中断")):
            with self.assertRaises(OSError):
                self.wb._atomic_write_text(str(target), "new-content")
        self.assertEqual(target.read_text(encoding="utf-8"), "original")
        leftovers = [p.name for p in self.root.iterdir() if p.name != "a.txt"]
        self.assertEqual(leftovers, [])


class BackfillAtomicWriteTest(unittest.TestCase):
    """回填脚本的进度写入必须原子且不残留固定后缀临时文件。"""

    def test_save_progress_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "comment_backfill_progress_用户A.json")
            backfill.save_progress(path, {"101", "102"}, {"processed": 2})
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            self.assertEqual(data["done"], ["101", "102"])
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), [
                "comment_backfill_progress_用户A.json"
            ])

    def test_write_report_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = backfill.write_report(
                tmp,
                {
                    "planned": 1,
                    "processed": 1,
                    "written": 1,
                    "empty": 0,
                    "unchanged": 0,
                    "failed": 0,
                    "skipped": 0,
                    "roots": 1,
                    "replies": 0,
                    "requests": 1,
                    "errors": 0,
                    "filtered_weibo": 0,
                },
                [],
                1.0,
            )
            text = Path(path).read_text(encoding="utf-8")
            self.assertIn("评论回填报告", text)
            self.assertIn("中断: 否", text)
            self.assertEqual(
                sorted(p.name for p in Path(tmp).iterdir()), [Path(path).name]
            )


class MarkdownPermissionTest(unittest.TestCase):
    """评论写回后保留原文件权限。"""

    MD = (
        "## 2024-05-03 [用户A] 微博存档\n\n"
        "### 10:00:00\n<!-- weibo_id: 101 -->\n正文\n\n---\n"
    )

    def test_permission_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "2024-05-03.md"
            path.write_text(self.MD, encoding="utf-8")
            os.chmod(path, 0o640)
            block = (
                "#### 评论\n"
                "<!-- comments: 101 total=1 saved=1 -->\n"
                "> **甲** · 10:00:00\n"
                "> 内容\n"
                "<!-- /comments -->\n"
            )
            changed, _status = comment_markdown.update_markdown_file_status(
                str(path), "101", block, backup=False
            )
            self.assertTrue(changed)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o640)
            self.assertFalse(
                [p for p in path.parent.iterdir() if p.suffix == ".tmp"]
            )


if __name__ == "__main__":
    unittest.main()
