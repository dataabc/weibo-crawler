"""评论块按时间排序测试。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests import _support  # noqa: E402

_support.bootstrap()

from util.comment import comment_markdown  # noqa: E402


class SortCommentBlockTest(unittest.TestCase):
    def test_sort_roots_and_replies(self):
        block = (
            "#### 评论\n\n"
            "<!-- comments: 123 total=3 saved=3 -->\n\n"
            "> **用户B** · 2024-01-02 10:00:00 <!-- cid: 2 -->\n"
            "> 第二条\n"
            ">\n"
            "> > **回复B1** · 2024-01-02 12:00:00 <!-- cid: 22 -->\n"
            "> > 回复二\n"
            ">\n"
            "> **用户A** · 2024-01-01 09:00:00 <!-- cid: 1 -->\n"
            "> 第一条\n"
            ">\n"
            "> > **回复A2** · 2024-01-01 11:00:00 <!-- cid: 12 -->\n"
            "> > 回复A2\n"
            ">\n"
            "> > **回复A1** · 2024-01-01 10:00:00 <!-- cid: 11 -->\n"
            "> > 回复A1\n\n"
            "<!-- /comments -->"
        )
        sorted_block = comment_markdown.sort_comment_block(block)
        self.assertLess(sorted_block.index("cid: 1"), sorted_block.index("cid: 2"))
        self.assertLess(sorted_block.index("cid: 11"), sorted_block.index("cid: 12"))
        self.assertIn("<!-- comments: 123 total=3 saved=3 -->", sorted_block)


if __name__ == "__main__":
    unittest.main()
