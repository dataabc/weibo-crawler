'util/comment_backfill.py 单元测试。'

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from util.comment import comment_backfill  # noqa: E402

OLD_MD = """## 2024-05-03 [测试用户] 微博存档

### 10:00:00

<!-- weibo_id: 100 -->

旧正文

#### 评论

<!-- comments: 100 total=2 saved=2 -->

> **甲** · 2024-05-03 11:00:00 <!-- cid: c1 -->
> 旧评论1

<!-- /comments -->

---
"""

NEW_BLOCK = """#### 评论

<!-- comments: 100 total=3 saved=3 -->

> **乙** · 2024-05-03 12:00:00 <!-- cid: c2 -->
> 新评论2

<!-- /comments -->

---
"""


class CommentBackfillTest(unittest.TestCase):
    def test_merge_keeps_old_and_adds_new(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = os.path.join(tmpdir, "2024-05-03.md")
            with open(md_path, "w", encoding="utf-8") as handle:
                handle.write(OLD_MD)
            merged = comment_backfill.merge_comment_block(md_path, "100", NEW_BLOCK)
            self.assertIn("cid: c1", merged)
            self.assertIn("cid: c2", merged)

    def test_missing_md_returns_new_block(self):
        merged = comment_backfill.merge_comment_block(
            "/tmp/not-exists.md", "100", NEW_BLOCK
        )
        self.assertEqual(merged, NEW_BLOCK)

    def test_block_without_old_comments_returns_new(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = os.path.join(tmpdir, "2024-05-03.md")
            with open(md_path, "w", encoding="utf-8") as handle:
                handle.write(OLD_MD.split("#### 评论")[0])
            merged = comment_backfill.merge_comment_block(md_path, "100", NEW_BLOCK)
            self.assertEqual(merged, NEW_BLOCK)
