'Weibo.parse_weibo 的字段映射测试。'

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests import _support  # noqa: E402

_support.bootstrap()

import weibo  # noqa: E402


class _FakeSelector:
    """最小 selector：按 XPath 表达式返回预置结果。"""

    def __init__(self, texts=None):
        self._texts = texts or []

    def xpath(self, expression):
        if expression == "//text()":
            return list(self._texts)
        return []


def _mblog(**overrides):
    info = {
        "user": {"id": 1234567890, "screen_name": "测试用户"},
        "id": "5000000000000001",
        "bid": "AbCdEf",
        "text": "示例正文",
        "created_at": "Mon Jan 01 00:00:00 +0800 2024",
        "source": "iPhone 15",
        "attitudes_count": "10",
        "comments_count": "5",
        "reposts_count": "2",
        "pic_num": 0,
    }
    info.update(overrides)
    return info


class ParseWeiboTest(unittest.TestCase):
    def setUp(self):
        self.wb = weibo.Weibo.__new__(weibo.Weibo)
        self.wb.remove_html_tag = 0
        self.wb.llm_analyzer = None
        # 隔离所有依赖 XPath / 接口结构的辅助方法，只验证字段映射
        self.wb.get_article_url = lambda selector: ""
        self.wb.get_pics = lambda info: []
        self.wb.get_video_url = lambda info: ""
        self.wb.get_live_photo_url = lambda info: {"url": "", "indices": []}
        self.wb.get_link_urls = lambda selector: []
        self.wb.get_location = lambda selector: ""
        self.wb.get_topics = lambda selector: ["话题"]
        self.wb.get_at_users = lambda selector: ["某人"]
        self.wb.standardize_info = lambda item: item

    def _parse(self, info, texts=None):
        with mock.patch.object(
            weibo.etree, "HTML", lambda html: _FakeSelector(texts), create=True
        ):
            return self.wb.parse_weibo(info)

    def test_core_field_mapping(self):
        result = self._parse(_mblog())
        self.assertEqual(result["user_id"], 1234567890)
        self.assertEqual(result["screen_name"], "测试用户")
        self.assertEqual(result["id"], 5000000000000001)
        self.assertIsInstance(result["id"], int)
        self.assertEqual(result["bid"], "AbCdEf")
        self.assertEqual(result["text"], "示例正文")
        self.assertEqual(result["created_at"], "Mon Jan 01 00:00:00 +0800 2024")
        self.assertEqual(result["source"], "iPhone 15")
        self.assertEqual(result["article_url"], "")
        self.assertEqual(result["pics"], [])
        self.assertEqual(result["video_url"], "")
        self.assertEqual(result["live_photo_url"], "")
        self.assertEqual(result["live_photo_indices"], [])
        self.assertEqual(result["links"], [])
        self.assertEqual(result["location"], "")
        self.assertEqual(result["topics"], ["话题"])
        self.assertEqual(result["at_users"], ["某人"])

    def test_counters_are_converted_to_int(self):
        result = self._parse(_mblog())
        self.assertEqual(result["attitudes_count"], 10)
        self.assertEqual(result["comments_count"], 5)
        self.assertEqual(result["reposts_count"], 2)
        self.assertIsInstance(result["attitudes_count"], int)

    def test_empty_user_is_allowed(self):
        result = self._parse(_mblog(user=None))
        self.assertEqual(result["user_id"], "")
        self.assertEqual(result["screen_name"], "")

    def test_remove_html_tag_false_keeps_raw_text(self):
        raw = "<p>保留 <b>HTML</b></p>"
        result = self._parse(_mblog(text=raw))
        self.assertEqual(result["text"], raw)

    def test_remove_html_tag_true_joins_xpath_texts(self):
        self.wb.remove_html_tag = 1
        result = self._parse(_mblog(text="<p>甲</p><p>乙</p>"), texts=["甲", "乙"])
        self.assertEqual(result["text"], "甲\n乙")

    def test_missing_counters_default_to_zero(self):
        info = _mblog()
        for key in ("attitudes_count", "comments_count", "reposts_count"):
            info.pop(key)
        result = self._parse(info)
        self.assertEqual(result["attitudes_count"], 0)
        self.assertEqual(result["comments_count"], 0)
        self.assertEqual(result["reposts_count"], 0)


if __name__ == "__main__":
    unittest.main()
