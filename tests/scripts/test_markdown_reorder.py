"""统一重排脚本单元测试。"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_SCRIPTS_DIR = str(Path(__file__).resolve().parents[2] / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import weibo_markdown_reorder as reorder  # noqa: E402

UNSORTED = """## 2024-05-03 [用户] 微博存档

### 12:00:00

<!-- weibo_id: 200 -->

正文二

---

### 08:00:00

<!-- weibo_id: 100 -->

正文一

---
"""

WITH_UNPARSED = """## 2024-05-04 [用户] 微博存档

### 10:00:00

<!-- weibo_id: 300 -->

正文

---

### 09:00:00

人工添加的段落，没有微博 ID。

---
"""

SORTED = """## 2024-05-03 [用户] 微博存档

### 08:00:00

<!-- weibo_id: 100 -->

正文一

---

### 12:00:00

<!-- weibo_id: 200 -->

正文二

---
"""


def _build_tree(tmpdir):
    user_dir = os.path.join(tmpdir, "用户A")
    month_dir = os.path.join(user_dir, "2024-05")
    os.makedirs(month_dir)
    with open(os.path.join(month_dir, "2024-05-03.md"), "w", encoding="utf-8") as f:
        f.write(UNSORTED)
    with open(os.path.join(month_dir, "2024-05-04.md"), "w", encoding="utf-8") as f:
        f.write(WITH_UNPARSED)
    # 报告目录与不可见目录应被排除
    os.makedirs(os.path.join(tmpdir, "report", "用户A"))
    os.makedirs(os.path.join(tmpdir, "用户A", "已不可见"))
    return tmpdir, month_dir


class ReorderTest(unittest.TestCase):
    def test_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _build_tree(tmpdir)
            summary = reorder.reorder_output_dir(tmpdir, dry_run=True)
            self.assertEqual(summary["would_reorder"], 1)
            self.assertEqual(summary["skipped"], 1)
            self.assertEqual(summary["reordered"], 0)
            path = os.path.join(tmpdir, "用户A", "2024-05", "2024-05-03.md")
            with open(path, encoding="utf-8") as f:
                self.assertEqual(f.read(), UNSORTED)

    def test_fix_reorders_and_backups(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _build_tree(tmpdir)
            summary = reorder.reorder_output_dir(tmpdir, dry_run=False)
            self.assertEqual(summary["reordered"], 1)
            self.assertEqual(summary["skipped"], 1)
            month_dir = os.path.join(tmpdir, "用户A", "2024-05")
            path = os.path.join(month_dir, "2024-05-03.md")
            with open(path, encoding="utf-8") as f:
                text = f.read()
            self.assertLess(text.index("### 08:00:00"), text.index("### 12:00:00"))
            backups = [f for f in os.listdir(month_dir) if ".bak." in f]
            self.assertEqual(len(backups), 1)
            with open(os.path.join(month_dir, backups[0]), encoding="utf-8") as f:
                self.assertEqual(f.read(), UNSORTED)

    def test_idempotent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _build_tree(tmpdir)
            reorder.reorder_output_dir(tmpdir, dry_run=False)
            second = reorder.reorder_output_dir(tmpdir, dry_run=False)
            self.assertEqual(second["reordered"], 0)
            self.assertEqual(second["unchanged"], 1)

    def test_excluded_dirs_not_touched(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _build_tree(tmpdir)
            summary = reorder.reorder_output_dir(tmpdir, dry_run=False)
            # report/ 与「已不可见」目录里的文件不应出现在结果中
            for item in summary["files"]:
                self.assertNotIn("report", item["path"])
                self.assertNotIn("已不可见", item["path"])

    def test_render_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _build_tree(tmpdir)
            summary = reorder.reorder_output_dir(tmpdir, dry_run=True)
            text = reorder.render_summary(summary)
            self.assertIn("Markdown 统一重排报告", text)
            self.assertIn("需要重排: 1", text)
            self.assertIn("跳过（含无 marker 块）: 1", text)

    def test_error_on_missing_dir(self):
        summary = reorder.reorder_output_dir("/nonexistent/weibo_data", dry_run=True)
        self.assertEqual(summary["errors"], 1)


if __name__ == "__main__":
    unittest.main()
