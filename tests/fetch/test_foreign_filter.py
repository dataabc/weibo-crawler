"""时间线推荐/广告卡片过滤测试。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests import _support  # noqa: E402

_support.bootstrap()

from util.fetch.fetch_pages import _is_foreign_weibo  # noqa: E402


class ForeignWeiboFilterTest(unittest.TestCase):
    def test_foreign(self):
        self.assertTrue(_is_foreign_weibo({"user_id": "1000000002"}, "1000000001"))
        self.assertTrue(_is_foreign_weibo({"user_id": 1000000002}, "1000000001"))

    def test_same_owner(self):
        self.assertFalse(_is_foreign_weibo({"user_id": "1000000001"}, "1000000001"))

    def test_missing_author_is_skipped(self):
        # 没有作者信息时无法确认归属，按非本人处理（宁可跳过，不污染存档）
        self.assertTrue(_is_foreign_weibo({}, "1000000001"))
        self.assertFalse(_is_foreign_weibo({"user_id": "1"}, ""))


if __name__ == "__main__":
    unittest.main()
