"""util.comment_fetch 的单元测试（无网络，stub session）。"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from util import comment_fetch

CST = timezone(timedelta(hours=8))


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FakeSession:
    """按顺序返回预设响应；记录每次请求。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        if not self.responses:
            raise AssertionError("unexpected extra request")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, FakeResponse):
            return item
        return FakeResponse(item)


def options(**kwargs):
    defaults = dict(
        max_root=0,
        max_reply=0,
        max_requests=100,
        delay_min=0,
        delay_max=0,
        include_latest=False,
        timeout=1,
        max_retries=0,
        emoji_replace=True,
        sleep=lambda *_: None,
        rand=lambda: 0.0,
    )
    defaults.update(kwargs)
    return comment_fetch.FetchOptions(**defaults)


def raw_comment(
    cid,
    created_at="Fri May 03 00:22:41 +0800 2024",
    text="评论正文",
    user="用户A",
    total_number=0,
    like_count=3,
    pic=None,
):
    return {
        "id": cid,
        "rootid": cid,
        "created_at": created_at,
        "text": text,
        "user": {"id": "u" + str(cid), "screen_name": user},
        "like_count": like_count,
        "total_number": total_number,
        "floor_number": 1,
        "pic": pic or {},
    }


def hotflow_page(items, max_id=0, max_id_type=0, total_number=0):
    return {
        "ok": 1,
        "data": {
            "data": items,
            "max_id": max_id,
            "max_id_type": max_id_type,
            "total_number": total_number,
            "max": 10,
        },
    }


def reply_page(items, max_id=0, max_id_type=0, total_number=0):
    return {
        "ok": 1,
        "data": items,
        "max_id": max_id,
        "max_id_type": max_id_type,
        "total_number": total_number,
        "max": 10,
        "rootComment": [items[0]] if items else [],
    }


def latest_page(items, max_page=2):
    return {"ok": 1, "msg": "", "data": {"data": items, "max": max_page, "total_number": 10}}


class CommentTimeTest(unittest.TestCase):
    def test_english_format(self):
        created, ts = comment_fetch.parse_comment_time("Fri May 03 00:22:41 +0800 2024")
        self.assertEqual(created, "2024-05-03 00:22:41")
        expected = datetime(2024, 5, 3, 0, 22, 41, tzinfo=CST).timestamp()
        self.assertAlmostEqual(ts, expected, places=0)

    def test_relative_and_short_formats(self):
        now = datetime(2024, 10, 24, 12, 0, 0, tzinfo=CST)
        self.assertEqual(comment_fetch.parse_comment_time("3分钟前", now)[0], "2024-10-24 11:57:00")
        self.assertEqual(comment_fetch.parse_comment_time("2小时前", now)[0], "2024-10-24 10:00:00")
        self.assertEqual(comment_fetch.parse_comment_time("昨天 08:30", now)[0], "2024-10-23 08:30:00")
        self.assertEqual(comment_fetch.parse_comment_time("07-26", now)[0], "2024-07-26 00:00:00")
        self.assertEqual(comment_fetch.parse_comment_time("07-26 09:05", now)[0], "2024-07-26 09:05:00")
        self.assertEqual(
            comment_fetch.parse_comment_time("2026-01-02 03:04:05", now)[0],
            "2026-01-02 03:04:05",
        )

    def test_invalid_returns_empty(self):
        self.assertEqual(comment_fetch.parse_comment_time(""), ("", 0.0))
        self.assertEqual(comment_fetch.parse_comment_time("not a date"), ("", 0.0))


class CleanHtmlTest(unittest.TestCase):
    def test_at_link_break_and_emoji(self):
        raw = (
            "回复<a href='/n/张三'>@张三</a>：不错<br />"
            "第二行<img src='https://face.t.sinajs.cn/x.png' class='emoji' />"
        )
        cleaned = comment_fetch.clean_comment_html(raw)
        self.assertIn("回复@张三：不错", cleaned)
        self.assertIn("第二行[表情]", cleaned)
        self.assertNotIn("<", cleaned)

    def test_emoji_keep_mode(self):
        raw = "abc<img src='https://face.t.sinajs.cn/x.png' />"
        cleaned = comment_fetch.clean_comment_html(raw, emoji_replace=False)
        self.assertNotIn("[表情]", cleaned)
        self.assertIn("abc", cleaned)

    def test_blank_lines_collapsed(self):
        cleaned = comment_fetch.clean_comment_html("a<br /><br /><br />b")
        self.assertEqual(cleaned, "a\n\nb")


class ReplyTargetTest(unittest.TestCase):
    def test_from_html(self):
        raw = "回复<a href='/n/李四'>@李四</a>：收到"
        self.assertEqual(comment_fetch.extract_reply_target(raw), "李四")

    def test_from_text(self):
        self.assertEqual(comment_fetch.extract_reply_target("", "回复@王五：好的"), "王五")

    def test_from_leading_anchor(self):
        # 实测楼中楼回复对象的另一种形式：正文直接以 @某人 链接开头
        raw = "<a href='/n/赵六'>@赵六</a> 说得对"
        self.assertEqual(comment_fetch.extract_reply_target(raw), "赵六")

    def test_from_leading_at_text(self):
        self.assertEqual(comment_fetch.extract_reply_target("", "@孙七 不同意"), "孙七")

    def test_none(self):
        self.assertEqual(comment_fetch.extract_reply_target("<span>普通评论</span>", "普通评论"), "")


class FetchRootTest(unittest.TestCase):
    def test_pagination_until_max_id_zero(self):
        session = FakeSession(
            [
                hotflow_page([raw_comment("1"), raw_comment("2")], max_id=111, total_number=5),
                hotflow_page([raw_comment("3")], max_id=0, total_number=5),
            ]
        )
        result = comment_fetch.fetch_comments(session, {}, "500", options())
        self.assertEqual([c.id for c in result.roots], ["1", "2", "3"])
        self.assertEqual(result.total_number, 5)
        self.assertEqual(result.stats.requests, 2)
        self.assertEqual(result.stats.stopped_reason, "end")
        self.assertEqual(len(session.calls), 2)
        self.assertIn("max_id", session.calls[1][1])

    def test_root_limit(self):
        session = FakeSession(
            [hotflow_page([raw_comment("1"), raw_comment("2"), raw_comment("3")], max_id=0)]
        )
        result = comment_fetch.fetch_comments(session, {}, "500", options(max_root=2))
        self.assertEqual(len(result.roots), 2)
        self.assertEqual(result.stats.stopped_reason, "root_limit")

    def test_duplicate_ids_deduped(self):
        session = FakeSession(
            [hotflow_page([raw_comment("1"), raw_comment("1")], max_id=0)]
        )
        result = comment_fetch.fetch_comments(session, {}, "500", options())
        self.assertEqual(len(result.roots), 1)

    def test_error_retry(self):
        session = FakeSession(
            [RuntimeError("boom"), hotflow_page([raw_comment("1")], max_id=0)]
        )
        result = comment_fetch.fetch_comments(session, {}, "500", options(max_retries=1))
        self.assertEqual(len(result.roots), 1)
        self.assertEqual(result.stats.errors, 1)
        self.assertEqual(result.stats.requests, 1)

    def test_request_budget(self):
        session = FakeSession(
            [hotflow_page([raw_comment("1")], max_id=111)]
        )
        result = comment_fetch.fetch_comments(session, {}, "500", options(max_requests=1))
        self.assertEqual(len(result.roots), 1)
        self.assertEqual(result.stats.requests, 1)
        self.assertEqual(result.stats.stopped_reason, "request_budget")

    def test_sort_by_time_ascending(self):
        session = FakeSession(
            [
                hotflow_page(
                    [
                        raw_comment("2", created_at="Fri May 05 00:00:00 +0800 2024"),
                        raw_comment("1", created_at="Fri May 01 00:00:00 +0800 2024"),
                        raw_comment("3", created_at="Fri May 03 00:00:00 +0800 2024"),
                    ],
                    max_id=0,
                )
            ]
        )
        result = comment_fetch.fetch_comments(session, {}, "500", options())
        self.assertEqual([c.id for c in result.roots], ["1", "3", "2"])


class FetchReplyTest(unittest.TestCase):
    def test_replies_fetched_and_sorted(self):
        root = raw_comment("100", total_number=2)
        session = FakeSession(
            [
                hotflow_page([root], max_id=0),
                reply_page(
                    [
                        raw_comment("102", created_at="Fri May 03 10:00:00 +0800 2024", text="第二条"),
                        raw_comment(
                            "101",
                            created_at="Fri May 03 09:00:00 +0800 2024",
                            text="回复<a href='/n/用户A'>@用户A</a>：第一条",
                        ),
                    ],
                    max_id=0,
                    total_number=2,
                ),
            ]
        )
        result = comment_fetch.fetch_comments(session, {}, "500", options())
        self.assertEqual(len(result.roots), 1)
        replies = result.roots[0].replies
        self.assertEqual([r.id for r in replies], ["101", "102"])
        self.assertEqual(replies[0].reply_target, "用户A")
        self.assertEqual(result.stats.replies_fetched, 2)
        self.assertEqual(session.calls[1][1]["cid"], "100")

    def test_reply_pagination(self):
        root = raw_comment("100", total_number=3)
        session = FakeSession(
            [
                hotflow_page([root], max_id=0),
                reply_page([raw_comment("101")], max_id=222, total_number=3),
                reply_page([raw_comment("102")], max_id=0, total_number=3),
            ]
        )
        result = comment_fetch.fetch_comments(session, {}, "500", options())
        self.assertEqual([r.id for r in result.roots[0].replies], ["101", "102"])

    def test_reply_limit(self):
        root = raw_comment("100", total_number=5)
        session = FakeSession(
            [
                hotflow_page([root], max_id=0),
                reply_page([raw_comment("101"), raw_comment("102")], max_id=222, total_number=5),
            ]
        )
        result = comment_fetch.fetch_comments(session, {}, "500", options(max_reply=1))
        self.assertEqual(len(result.roots[0].replies), 1)

    def test_root_without_replies_skipped(self):
        root = raw_comment("100", total_number=0)
        session = FakeSession([hotflow_page([root], max_id=0)])
        result = comment_fetch.fetch_comments(session, {}, "500", options())
        self.assertEqual(len(result.roots), 1)
        self.assertEqual(len(session.calls), 1)


class FetchLatestTest(unittest.TestCase):
    def test_latest_stream_merges_and_dedupes(self):
        session = FakeSession(
            [
                hotflow_page([raw_comment("1")], max_id=0),
                latest_page([raw_comment("1", created_at="07-26"), raw_comment("2", created_at="07-27")]),
                latest_page([], max_page=2),
            ]
        )
        result = comment_fetch.fetch_comments(
            session, {}, "500", options(include_latest=True)
        )
        self.assertEqual([c.id for c in result.roots], ["1", "2"])
        self.assertEqual(result.stats.latest_fetched, 1)


class ParseCommentTest(unittest.TestCase):
    def test_pic_and_fields(self):
        raw = raw_comment(
            "1",
            pic={"large": {"url": "https://wx1.sinaimg.cn/large/x.jpg"}},
        )
        comment = comment_fetch.parse_comment(raw, "500", is_root=True)
        self.assertEqual(comment.pic_url, "https://wx1.sinaimg.cn/large/x.jpg")
        self.assertEqual(comment.like_count, 3)
        self.assertEqual(comment.user_name, "用户A")
        self.assertEqual(comment.created_at, "2024-05-03 00:22:41")

    def test_invalid_comment(self):
        self.assertIsNone(comment_fetch.parse_comment({}, "500"))
        self.assertIsNone(comment_fetch.parse_comment("bad", "500"))


if __name__ == "__main__":
    unittest.main()


class FilterCommentsTest(unittest.TestCase):
    def _result(self):
        root_a = comment_fetch.Comment(
            id="1", weibo_id="500", is_root=True, user_id="u1", user_name="甲", text="甲的一级评论"
        )
        reply_b = comment_fetch.Comment(
            id="2", weibo_id="500", is_root=False, user_id="u2", user_name="乙",
            text="乙的回复", reply_target="甲",
        )
        reply_d = comment_fetch.Comment(
            id="3", weibo_id="500", is_root=False, user_id="u4", user_name="丁", text="丁的回复"
        )
        root_a.replies = [reply_b, reply_d]
        root_c = comment_fetch.Comment(
            id="4", weibo_id="500", is_root=True, user_id="u3", user_name="丙", text="丙的一级评论"
        )
        stats = comment_fetch.FetchStats(roots_fetched=2, replies_fetched=2, total_number=10)
        return comment_fetch.FetchResult(
            weibo_id="500", total_number=10, roots=[root_a, root_c], stats=stats
        )

    def test_no_filter_returns_original(self):
        result = self._result()
        filtered = comment_fetch.filter_comments(result, None)
        self.assertEqual(len(filtered.roots), 2)
        self.assertIs(filtered, result)

    def test_keeps_allowed_replies_with_context_root(self):
        # 白名单只有「乙」：甲的一级评论作为上下文保留，丙的一级评论丢弃
        filtered = comment_fetch.filter_comments(self._result(), ["乙"])
        self.assertEqual([root.id for root in filtered.roots], ["1"])
        self.assertEqual([reply.id for reply in filtered.roots[0].replies], ["2"])
        self.assertEqual(filtered.stats.roots_fetched, 1)
        self.assertEqual(filtered.stats.replies_fetched, 1)

    def test_keeps_allowed_root_drops_other_replies(self):
        filtered = comment_fetch.filter_comments(self._result(), ["甲"])
        self.assertEqual([root.id for root in filtered.roots], ["1"])
        self.assertEqual(filtered.roots[0].replies, [])

    def test_match_by_user_id(self):
        filtered = comment_fetch.filter_comments(self._result(), ["u3"])
        self.assertEqual([root.id for root in filtered.roots], ["4"])

    def test_drops_root_without_allowed_content(self):
        filtered = comment_fetch.filter_comments(self._result(), ["不存在的人"])
        self.assertEqual(filtered.roots, [])
        self.assertEqual(filtered.stats.roots_fetched, 0)
        self.assertEqual(filtered.stats.replies_fetched, 0)

    def test_original_result_not_mutated(self):
        result = self._result()
        comment_fetch.filter_comments(result, ["乙"])
        self.assertEqual(len(result.roots), 2)
        self.assertEqual(len(result.roots[0].replies), 2)
        self.assertEqual(result.stats.replies_fetched, 2)


class ParseUserFilterFileTest(unittest.TestCase):
    def _write(self, tmp, text):
        path = os.path.join(tmp, "comment_user_filter.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def test_parses_comments_blank_lines_and_multiple_users(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(
                tmp,
                "# 注释行\n\n123 甲 乙\n456\t丙\n789 甲\n",
            )
            mapping = comment_fetch.parse_user_filter_file(path)
            self.assertEqual(mapping["123"], {"甲", "乙"})
            self.assertEqual(mapping["456"], {"丙"})
            self.assertEqual(mapping["789"], {"甲"})

    def test_ignores_line_without_commenter(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "123\n456 甲\n")
            mapping = comment_fetch.parse_user_filter_file(path)
            self.assertEqual(mapping, {"456": {"甲"}})

    def test_missing_file_returns_empty(self):
        self.assertEqual(comment_fetch.parse_user_filter_file("/tmp/not-exist-filter.txt"), {})
