"""评论块重复清理脚本 单元测试。"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_SCRIPTS_DIR = str(Path(__file__).resolve().parents[2] / "scripts" / "legacy")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import weibo_dedup_comment_groups as dedup  # noqa: E402

MD_WITH_DUP = """## 2024-05-03 [用户] 微博存档

### 10:00:00

<!-- weibo_id: 100 -->

正文内容

#### 评论

<!-- comments: 100 total=2 saved=2 -->

> **评论者A** · 2024-05-03 10:01:00
> 重复的评论

> **评论者A** · 2024-05-03 10:01:00 <!-- cid: 1 -->
> 重复的评论

<!-- /comments -->

---
"""

MD_CLEAN = """## 2024-05-04 [用户] 微博存档

### 09:00:00

<!-- weibo_id: 200 -->

另一条正文

---
"""


class DedupTest(unittest.TestCase):
    def _tree(self, tmpdir):
        user_dir = os.path.join(tmpdir, "用户A", "2024-05")
        os.makedirs(user_dir)
        dup_path = os.path.join(user_dir, "2024-05-03.md")
        clean_path = os.path.join(user_dir, "2024-05-04.md")
        with open(dup_path, "w", encoding="utf-8") as handle:
            handle.write(MD_WITH_DUP)
        with open(clean_path, "w", encoding="utf-8") as handle:
            handle.write(MD_CLEAN)
        # 排除目录里的文件不应处理
        excluded = os.path.join(tmpdir, "用户A", "已删除")
        os.makedirs(excluded)
        with open(os.path.join(excluded, "x.md"), "w", encoding="utf-8") as handle:
            handle.write(MD_WITH_DUP)
        return dup_path, clean_path

    def test_dry_run_reports_without_writing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dup_path, _ = self._tree(tmpdir)
            summary = dedup.dedup_output_dir(tmpdir, dry_run=True)
            self.assertEqual(summary["would_fix"], 1)
            self.assertEqual(summary["groups_removed"], 1)
            with open(dup_path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), MD_WITH_DUP)

    def test_fix_removes_duplicate_keeps_cid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dup_path, clean_path = self._tree(tmpdir)
            summary = dedup.dedup_output_dir(tmpdir, dry_run=False)
            self.assertEqual(summary["fixed"], 1)
            self.assertEqual(summary["groups_removed"], 1)
            with open(dup_path, encoding="utf-8") as handle:
                text = handle.read()
            self.assertEqual(text.count("重复的评论"), 1)
            self.assertIn("<!-- cid: 1 -->", text)
            self.assertIn("正文内容", text)
            # 干净文件不变
            with open(clean_path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), MD_CLEAN)
            # 有备份
            files = os.listdir(os.path.dirname(dup_path))
            self.assertTrue(any(".bak." in name for name in files))
            # 排除目录未处理
            excluded = os.path.join(tmpdir, "用户A", "已删除", "x.md")
            with open(excluded, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), MD_WITH_DUP)

    def test_idempotent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._tree(tmpdir)
            dedup.dedup_output_dir(tmpdir, dry_run=False)
            second = dedup.dedup_output_dir(tmpdir, dry_run=False)
            self.assertEqual(second["fixed"], 0)
            self.assertEqual(second["unchanged"], 2)


if __name__ == "__main__":
    unittest.main()
