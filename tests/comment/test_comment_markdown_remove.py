"""评论块按需移除逻辑测试。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests import _support  # noqa: E402

_support.bootstrap()

from util.comment import comment_markdown  # noqa: E402


class RemoveCommentBlockTest(unittest.TestCase):
    def test_remove_comment_block_keeps_weibo(self):
        text = (
            "### 10:00:00\n\n"
            "<!-- weibo_id: 123 -->\n\n"
            "微博正文\n\n"
            "#### 评论\n\n"
            "<!-- comments: 123 total=2 saved=1 -->\n\n"
            "> **某用户** · 2024-01-01 10:00:00\n> 评论内容\n\n"
            "<!-- /comments -->\n\n"
            "---\n\n"
            "### 09:00:00\n\n"
            "<!-- weibo_id: 456 -->\n\n"
            "另一条正文\n"
        )
        updated, changed = comment_markdown.remove_comment_block(text, "123")
        self.assertTrue(changed)
        self.assertNotIn("<!-- comments:", updated)
        self.assertNotIn("#### 评论", updated)
        self.assertIn("微博正文", updated)
        self.assertIn("另一条正文", updated)
        self.assertIn("<!-- weibo_id: 456 -->", updated)

    def test_no_comment_block_unchanged(self):
        text = "### 10:00:00\n\n<!-- weibo_id: 123 -->\n\n微博正文\n"
        updated, changed = comment_markdown.remove_comment_block(text, "123")
        self.assertFalse(changed)
        self.assertEqual(updated, text)


if __name__ == "__main__":
    unittest.main()
