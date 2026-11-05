"""评论/转发分页抓取（Cookie 版 + 无 Cookie 回退）测试。

覆盖：max_count 边界、正常分页与回调、JSON 解析失败回退、空数据回退、
page/max_id 终止条件、转发分页。全部用假 session，不联网。"""

import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests import _support  # noqa: E402

_support.bootstrap()

from util.comment import comment_cookie  # noqa: E402


class _Resp:
    def __init__(self, payload=None, raise_json=False):
        self.payload = payload
        self.raise_json = raise_json

    def json(self):
        if self.raise_json:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self.payload


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def _make_wb(responses):
    wb = types.SimpleNamespace()
    wb.session = _Session(responses)
    wb.headers = {"Referer": "https://m.weibo.cn/"}
    wb._get_weibo_comments_cookie = lambda *args: comment_cookie._get_weibo_comments_cookie(
        wb, *args
    )
    wb._get_weibo_comments_nocookie = lambda *args: comment_cookie._get_weibo_comments_nocookie(
        wb, *args
    )
    wb._get_weibo_reposts_cookie = lambda *args: comment_cookie._get_weibo_reposts_cookie(
        wb, *args
    )
    return wb


def _comments_page(comments, max_id=0):
    return {"data": {"data": comments, "max_id": max_id}}


class CommentsCookieTest(unittest.TestCase):
    def test_stops_when_max_count_reached(self):
        wb = _make_wb([])
        comment_cookie._get_weibo_comments_cookie(wb, {"id": "1"}, 5, 5, None, None)
        self.assertEqual(wb.session.calls, [])

    def test_single_page_with_callback(self):
        wb = _make_wb([_Resp(_comments_page([{"text": "a"}, {"text": "b"}]))])
        seen = []
        comment_cookie._get_weibo_comments_cookie(
            wb, {"id": "1"}, 0, 10, None, lambda _w, rows: seen.extend(rows)
        )
        self.assertEqual(len(seen), 2)
        self.assertEqual(len(wb.session.calls), 1)
        url, kwargs = wb.session.calls[0]
        self.assertIn("comments/hotflow", url)
        self.assertEqual(kwargs["params"], {"mid": "1"})

    def test_max_id_pagination_recurses(self):
        wb = _make_wb(
            [
                _Resp(_comments_page([{"text": "a"}], max_id=123)),
                _Resp(_comments_page([{"text": "b"}], max_id=0)),
            ]
        )
        seen = []
        comment_cookie._get_weibo_comments_cookie(
            wb, {"id": "1"}, 0, 10, None, lambda _w, rows: seen.extend(rows)
        )
        self.assertEqual([row["text"] for row in seen], ["a", "b"])
        self.assertEqual(len(wb.session.calls), 2)
        self.assertEqual(
            wb.session.calls[1][1]["params"], {"mid": "1", "max_id": 123}
        )

    def test_json_failure_falls_back_to_nocookie(self):
        wb = _make_wb([_Resp(raise_json=True)])
        fallback = mock.Mock()
        wb._get_weibo_comments_nocookie = fallback
        comment_cookie._get_weibo_comments_cookie(
            wb, {"id": "1"}, 0, 10, None, None
        )
        fallback.assert_called_once()
        # 回退入口从 0 开始、第 1 页
        args = fallback.call_args[0]
        self.assertEqual(args[1], 0)
        self.assertEqual(args[3], 1)

    def test_empty_data_falls_back_to_nocookie(self):
        wb = _make_wb([_Resp({"data": None})])
        fallback = mock.Mock()
        wb._get_weibo_comments_nocookie = fallback
        comment_cookie._get_weibo_comments_cookie(
            wb, {"id": "1"}, 0, 10, None, None
        )
        fallback.assert_called_once()

    def test_zero_comments_does_not_call_callback(self):
        wb = _make_wb([_Resp(_comments_page([]))])
        seen = []
        comment_cookie._get_weibo_comments_cookie(
            wb, {"id": "1"}, 0, 10, None, lambda _w, rows: seen.extend(rows)
        )
        self.assertEqual(seen, [])


class CommentsNoCookieTest(unittest.TestCase):
    def setUp(self):
        self._sleep = mock.patch.object(comment_cookie, "sleep", lambda _s: None)
        self._sleep.start()

    def tearDown(self):
        self._sleep.stop()

    def test_stops_when_max_count_reached(self):
        wb = _make_wb([])
        comment_cookie._get_weibo_comments_nocookie(wb, {"id": "1"}, 5, 5, 1, None)
        self.assertEqual(wb.session.calls, [])

    def test_single_page_stops_when_page_exceeds_max(self):
        wb = _make_wb([_Resp(_comments_page([{"text": "a"}], max_id=0))])
        # data 里的 max 表示总页数；用 max=1 让第一页后停止
        wb.session.responses[0].payload["data"]["max"] = 1
        seen = []
        comment_cookie._get_weibo_comments_nocookie(
            wb, {"id": "1"}, 0, 10, 1, lambda _w, rows: seen.extend(rows)
        )
        self.assertEqual(len(seen), 1)
        self.assertEqual(len(wb.session.calls), 1)

    def test_json_failure_returns_without_raising(self):
        wb = _make_wb([_Resp(raise_json=True)])
        comment_cookie._get_weibo_comments_nocookie(wb, {"id": "1"}, 0, 10, 1, None)


class RepostsCookieTest(unittest.TestCase):
    def setUp(self):
        self._sleep = mock.patch.object(comment_cookie, "sleep", lambda _s: None)
        self._sleep.start()

    def tearDown(self):
        self._sleep.stop()

    def test_single_page_with_callback(self):
        payload = {"data": {"data": [{"text": "转发1"}, {"text": "转发2"}], "max": 1}}
        wb = _make_wb([_Resp(payload)])
        seen = []
        comment_cookie._get_weibo_reposts_cookie(
            wb, {"id": "1"}, 0, 10, 1, lambda _w, rows: seen.extend(rows)
        )
        self.assertEqual(len(seen), 2)
        self.assertIn("repostTimeline", wb.session.calls[0][0])

    def test_json_failure_returns_without_raising(self):
        wb = _make_wb([_Resp(raise_json=True)])
        comment_cookie._get_weibo_reposts_cookie(wb, {"id": "1"}, 0, 10, 1, None)

    def test_stops_when_max_count_reached(self):
        wb = _make_wb([])
        comment_cookie._get_weibo_reposts_cookie(wb, {"id": "1"}, 5, 5, 1, None)
        self.assertEqual(wb.session.calls, [])


if __name__ == "__main__":
    unittest.main()
