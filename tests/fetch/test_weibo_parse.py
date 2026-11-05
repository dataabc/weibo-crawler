"""util/fetch/weibo_parse.get_one_weibo 的分支覆盖测试。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests import _support  # noqa: E402

_support.bootstrap()

from util.fetch import weibo_parse  # noqa: E402


class FakeWeibo:
    """最小 Weibo 替身：记录调用、返回预设结果。"""

    def __init__(self, long_weibo=None, parsed=None, date=None):
        self.long_calls = []
        self.parse_calls = []
        self.long_weibo = long_weibo if long_weibo is not None else {}
        self.parsed = parsed if parsed is not None else {"id": "parsed", "text": "正文"}
        self.date = date or ("2024-01-01T00:00:00", "2024-01-01 00:00:00")
        self.crawl_success = {}

    def get_long_weibo(self, weibo_id):
        self.long_calls.append(weibo_id)
        return dict(self.long_weibo) if self.long_weibo else {}

    def parse_weibo(self, info):
        self.parse_calls.append(info)
        return dict(self.parsed)

    def standardize_date(self, raw):
        return self.date


def card(**mblog_overrides):
    """构造一张「接口卡片」：所有关键字都放进 mblog（真实结构 retweeted_status 也在 mblog 内）。"""
    mblog = {
        "id": "5000000000000001",
        "created_at": "Mon Jan 01 00:00:00 +0800 2024",
        "pic_num": 0,
        "text": "正文",
    }
    mblog.update(mblog_overrides)
    return {"mblog": mblog}


def retweet_card(**overrides):
    """转发卡片：retweeted_status 嵌在 mblog 内。"""
    status = {
        "id": "6000000000000002",
        "created_at": "Mon Jan 01 00:00:00 +0800 2024",
        "text": "转发",
    }
    status.update(overrides.pop("retweeted_status", {}))
    return card(retweeted_status=status, **overrides)


class GetOneWeiboOriginalTest(unittest.TestCase):
    """原创微博分支。"""

    def test_short_original_uses_parse_weibo(self):
        wb = FakeWeibo()
        result = weibo_parse.get_one_weibo(wb, card())
        self.assertEqual(len(wb.parse_calls), 1)
        self.assertEqual(wb.long_calls, [])
        self.assertEqual(result["id"], "parsed")
        # created_at / full_created_at 来自 standardize_date
        self.assertEqual(result["created_at"], "2024-01-01T00:00:00")
        self.assertEqual(result["full_created_at"], "2024-01-01 00:00:00")

    def test_long_original_uses_get_long_weibo(self):
        wb = FakeWeibo(long_weibo={"id": "long", "text": "长微博"})
        result = weibo_parse.get_one_weibo(wb, card(isLongText=True))
        self.assertEqual(wb.long_calls, ["5000000000000001"])
        self.assertEqual(wb.parse_calls, [])
        self.assertEqual(result["id"], "long")

    def test_long_original_falls_back_when_get_long_returns_empty(self):
        wb = FakeWeibo(long_weibo={})
        result = weibo_parse.get_one_weibo(wb, card(isLongText=True))
        self.assertEqual(wb.long_calls, ["5000000000000001"])
        self.assertEqual(len(wb.parse_calls), 1)
        self.assertEqual(result["id"], "parsed")

    def test_pic_num_over_nine_is_treated_as_long(self):
        wb = FakeWeibo(long_weibo={"id": "long"})
        weibo_parse.get_one_weibo(wb, card(pic_num=10))
        self.assertEqual(wb.long_calls, ["5000000000000001"])

    def test_retweeted_status_without_id_is_treated_as_original(self):
        wb = FakeWeibo()
        result = weibo_parse.get_one_weibo(
            wb, card(retweeted_status={"id": None, "created_at": "x"})
        )
        # card() 已把 retweeted_status 放进 mblog；无 id 时按原创处理
        self.assertEqual(len(wb.parse_calls), 1)
        self.assertNotIn("retweet", result)


class GetOneWeiboRetweetTest(unittest.TestCase):
    """转发微博分支。"""

    def test_short_retweet_parses_both(self):
        wb = FakeWeibo()
        result = weibo_parse.get_one_weibo(wb, retweet_card())
        self.assertEqual(len(wb.parse_calls), 2)  # 主 + 转发目标
        self.assertIn("retweet", result)
        self.assertEqual(result["retweet"]["created_at"], "2024-01-01T00:00:00")
        self.assertEqual(result["retweet"]["full_created_at"], "2024-01-01 00:00:00")

    def test_long_retweet_main_uses_get_long(self):
        wb = FakeWeibo(long_weibo={"id": "long_main"})
        result = weibo_parse.get_one_weibo(wb, retweet_card(isLongText=True))
        self.assertEqual(wb.long_calls, ["5000000000000001"])
        self.assertIn("retweet", result)

    def test_long_retweet_target_uses_get_long(self):
        wb = FakeWeibo(long_weibo={"id": "long_target"})
        result = weibo_parse.get_one_weibo(
            wb, retweet_card(retweeted_status={"isLongText": True})
        )
        self.assertEqual(wb.long_calls, ["6000000000000002"])
        self.assertEqual(result["retweet"]["id"], "long_target")

    def test_both_long_uses_get_long_twice(self):
        wb = FakeWeibo(long_weibo={"id": "long"})
        weibo_parse.get_one_weibo(
            wb,
            retweet_card(isLongText=True, retweeted_status={"isLongText": True}),
        )
        self.assertEqual(wb.long_calls, ["5000000000000001", "6000000000000002"])

    def test_long_target_falls_back_when_get_long_empty(self):
        wb = FakeWeibo(long_weibo={})
        result = weibo_parse.get_one_weibo(
            wb, retweet_card(retweeted_status={"isLongText": True})
        )
        self.assertEqual(len(wb.parse_calls), 2)
        self.assertIn("retweet", result)


class GetOneWeiboFieldsTest(unittest.TestCase):
    """编辑/可见性/失败计数等字段与兜底。"""

    def test_edit_count_defaults_to_zero_and_not_edited(self):
        result = weibo_parse.get_one_weibo(FakeWeibo(), card())
        self.assertFalse(result["edited"])
        self.assertEqual(result["edit_count"], 0)
        self.assertEqual(result["edit_at"], "")

    def test_edit_count_positive_marks_edited(self):
        result = weibo_parse.get_one_weibo(FakeWeibo(), card(edit_count=3, edit_at="2024-02-02T00:00:00"))
        self.assertTrue(result["edited"])
        self.assertEqual(result["edit_count"], 3)
        self.assertEqual(result["edit_at"], "2024-02-02T00:00:00")

    def test_visible_normalized_to_ints(self):
        result = weibo_parse.get_one_weibo(
            FakeWeibo(), card(visible={"type": "0", "list_id": "42"})
        )
        self.assertEqual(result["visible"], {"type": 0, "list_id": 42})

    def test_visible_missing_or_invalid_is_empty_dict(self):
        self.assertEqual(weibo_parse.get_one_weibo(FakeWeibo(), card())["visible"], {})
        self.assertEqual(
            weibo_parse.get_one_weibo(FakeWeibo(), card(visible="bad"))["visible"], {}
        )

    def test_parse_failure_increments_counter_and_returns_none(self):
        wb = FakeWeibo()
        result = weibo_parse.get_one_weibo(wb, {"no_mblog": True})
        self.assertIsNone(result)
        self.assertEqual(wb.crawl_success["parse_failures"], 1)

    def test_parse_failure_count_accumulates(self):
        wb = FakeWeibo()
        wb.crawl_success = {"parse_failures": 2}
        weibo_parse.get_one_weibo(wb, {"no_mblog": True})
        self.assertEqual(wb.crawl_success["parse_failures"], 3)

    def test_parse_failure_creates_crawl_success_when_absent(self):
        wb = FakeWeibo()
        del wb.crawl_success
        weibo_parse.get_one_weibo(wb, {"no_mblog": True})
        self.assertEqual(wb.crawl_success["parse_failures"], 1)

    def test_pic_num_missing_is_known_fragile_path(self):
        """已知脆弱点：pic_num 缺失时 ``None > 9`` 抛 TypeError，走失败兜底。

        真实接口总会返回 pic_num，所以这里只固化当前行为（计数 + 返回 None），
        不修改生产逻辑；若未来平台去掉该字段，这条测试会提醒我们。
        """
        wb = FakeWeibo()
        info = card()
        info["mblog"].pop("pic_num")
        result = weibo_parse.get_one_weibo(wb, info)
        self.assertIsNone(result)
        self.assertEqual(wb.crawl_success["parse_failures"], 1)


if __name__ == "__main__":
    unittest.main()
