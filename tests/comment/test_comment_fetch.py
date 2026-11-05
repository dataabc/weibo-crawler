"""util.comment_fetch 的单元测试（无网络，stub session）。"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from util.comment import comment_fetch

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
        link_preview=False,
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


EMOJI_SRC = (
    "https://face.t.sinajs.cn/t4/appstyle/expression/ext/normal/83/"
    "2021_yingniunai_mobile.png"
)
EMOJI_HTML = (
    "早上好咘咘<span class=\"url-icon\"><img alt=\"[赢牛奶]\" "
    f"src=\"{EMOJI_SRC}\" style=\"width:1em; height:1em;\" /></span>"
    " 今天晚上有红月哦!！"
)
CARD_URL = "https://m.weibo.cn/1000000003/4833225920747305"
CARD_HTML = (
    "看了！！！<a  href=\"https://m.weibo.cn/1000000003/4833225920747305\" "
    "data-hide=\"\"><span class='url-icon'><img style='width: 1rem;height: 1rem' "
    "src='https://h5.sinaimg.cn/upload/2015/09/25/3/timeline_card_small_web_default.png'>"
    "</span><span class=\"surl-text\">网页链接</span></a>"
)
CARD_ICON = "https://h5.sinaimg.cn/upload/2015/09/25/3/timeline_card_small_web_default.png"


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
        # 默认 1：无 alt 时回退为可点击外链，不再输出裸 [表情]
        self.assertIn("第二行[表情](https://face.t.sinajs.cn/x.png)", cleaned)
        self.assertNotIn("<", cleaned)

    def test_emoji_keep_mode(self):
        raw = "abc<img src='https://face.t.sinajs.cn/x.png' />"
        cleaned = comment_fetch.clean_comment_html(raw, emoji_replace=False)
        # 0 = 保留外链图片
        self.assertEqual(cleaned, "abc![表情](https://face.t.sinajs.cn/x.png)")

    def test_blank_lines_collapsed(self):
        cleaned = comment_fetch.clean_comment_html("a<br /><br /><br />b")
        self.assertEqual(cleaned, "a\n\nb")


class CleanHtmlEmojiTest(unittest.TestCase):
    """comment_emoji_replace 的 0/1 两种语义。"""

    SRC = "https://face.t.sinajs.cn/x.png"

    def test_zero_mode_keeps_image_with_default_name(self):
        # 0 模式：有 src、无 alt → ![表情](src)
        cleaned = comment_fetch.clean_comment_html(
            f"abc<img src='{self.SRC}' />", emoji_replace=0
        )
        self.assertEqual(cleaned, f"abc![表情]({self.SRC})")

    def test_zero_mode_uses_alt_as_image_name(self):
        # 0 模式：alt="[微笑]" → 取方括号内文字作表情名
        cleaned = comment_fetch.clean_comment_html(
            f'<img src="{self.SRC}" alt="[微笑]" class="emoji"/>', emoji_replace=0
        )
        self.assertEqual(cleaned, f"![微笑]({self.SRC})")

    def test_zero_mode_plain_start_tag_with_single_quotes(self):
        # 非自闭合普通开始标签、单引号属性
        cleaned = comment_fetch.clean_comment_html(
            f"<img src='{self.SRC}' alt='[微笑]'>", emoji_replace=0
        )
        self.assertEqual(cleaned, f"![微笑]({self.SRC})")

    def test_zero_mode_alt_without_brackets_kept(self):
        cleaned = comment_fetch.clean_comment_html(
            f'<img src="{self.SRC}" alt="微笑" />', emoji_replace=0
        )
        self.assertEqual(cleaned, f"![微笑]({self.SRC})")

    def test_zero_mode_empty_alt_uses_default_name(self):
        cleaned = comment_fetch.clean_comment_html(
            f'<img src="{self.SRC}" alt="">', emoji_replace=0
        )
        self.assertEqual(cleaned, f"![表情]({self.SRC})")

    def test_zero_mode_alt_without_src_keeps_alt_text(self):
        # 无 src 但有表情语义 class → 仍按表情处理，输出 alt 原文
        cleaned = comment_fetch.clean_comment_html(
            '<img alt="[微笑]" class="emoji" />', emoji_replace=0
        )
        self.assertEqual(cleaned, "[微笑]")

    def test_zero_mode_without_src_and_alt_uses_fallback(self):
        self.assertEqual(
            comment_fetch.clean_comment_html('<img class="emoji" />', emoji_replace=0),
            "[表情]",
        )

    def test_one_mode_prefers_alt_text(self):
        # 1 模式：有 alt 时输出 alt 原文，不被 [表情] 抢先
        cleaned = comment_fetch.clean_comment_html(
            f'<img src="{self.SRC}" alt="[微笑]" class="emoji"/>', emoji_replace=1
        )
        self.assertEqual(cleaned, "[微笑]")

    def test_one_mode_falls_back_to_link_without_alt(self):
        cleaned = comment_fetch.clean_comment_html(
            f"abc<img src='{self.SRC}' />", emoji_replace=1
        )
        self.assertEqual(cleaned, f"abc[表情]({self.SRC})")

    def test_one_mode_alt_without_src_keeps_alt_text(self):
        # 无 src 但有表情语义 class → 1 模式同样输出 alt 原文
        self.assertEqual(
            comment_fetch.clean_comment_html('<img alt="[微笑]" class="emoji" />'),
            "[微笑]",
        )

    def test_one_mode_without_src_and_alt_uses_fallback(self):
        self.assertEqual(
            comment_fetch.clean_comment_html('<img class="emoji" />'), "[表情]"
        )

    def test_default_is_one_mode(self):
        cleaned = comment_fetch.clean_comment_html(
            f'<img src="{self.SRC}" alt="[微笑]" />'
        )
        self.assertEqual(cleaned, "[微笑]")


class CleanHtmlEmojiClassificationTest(unittest.TestCase):
    """只有表情 CDN / 表情语义 class 的 img 按表情处理，其余 img 丢弃。"""

    FACE_SRC = "https://face.t.sinajs.cn/t4/appstyle/expression/ext/normal/83/x.png"

    def test_real_emoji_html_keep_both_modes(self):
        self.assertEqual(
            comment_fetch.clean_comment_html(EMOJI_HTML),
            "早上好咘咘[赢牛奶] 今天晚上有红月哦!！",
        )
        # 0 = 保留外链图片
        self.assertEqual(
            comment_fetch.clean_comment_html(EMOJI_HTML, emoji_replace=0),
            f"早上好咘咘![赢牛奶]({EMOJI_SRC}) 今天晚上有红月哦!！",
        )

    def test_face_cdn_subdomain_is_emoji(self):
        src = "https://img.face.t.sinajs.cn/t4/expression/x.png"
        self.assertEqual(
            comment_fetch.clean_comment_html(f'<img src="{src}" alt="[微笑]" />'),
            "[微笑]",
        )
        self.assertEqual(
            comment_fetch.clean_comment_html(
                f'<img src="{src}" alt="[微笑]" />', emoji_replace=0
            ),
            f"![微笑]({src})",
        )

    def test_face_cdn_host_is_case_insensitive(self):
        src = "https://FACE.T.SINAJS.CN/t4/x.png"
        self.assertEqual(
            comment_fetch.clean_comment_html(f'<img src="{src}" alt="[微笑]" />'),
            "[微笑]",
        )

    def test_protocol_relative_face_cdn_is_emoji(self):
        src = "//face.t.sinajs.cn/t4/x.png"
        self.assertEqual(
            comment_fetch.clean_comment_html(f'<img src="{src}" alt="[微笑]" />'),
            "[微笑]",
        )
        self.assertEqual(
            comment_fetch.clean_comment_html(
                f'<img src="{src}" alt="[微笑]" />', emoji_replace=0
            ),
            f"![微笑]({src})",
        )

    def test_face_lookalike_host_is_not_emoji(self):
        for src in (
            "https://face.t.sinajs.cn.evil.com/x.png",
            "https://evilface.t.sinajs.cn/x.png",
        ):
            self.assertEqual(
                comment_fetch.clean_comment_html(f'<img src="{src}" alt="[微笑]" />'),
                "",
                src,
            )

    def test_account_icon_is_dropped_in_both_modes(self):
        # 网页链接卡片默认小图标：不是表情，任何模式都不输出
        raw = f'<img src="{CARD_ICON}">'
        self.assertEqual(comment_fetch.clean_comment_html(raw), "")
        self.assertEqual(comment_fetch.clean_comment_html(raw, emoji_replace=0), "")
        self.assertEqual(
            comment_fetch.clean_comment_html(f'<img src="{CARD_ICON}" alt="[微笑]" />'),
            "",
        )

    def test_unknown_ui_icon_is_dropped(self):
        raw = '<img src="https://example.com/icon.png" alt="图标">'
        self.assertEqual(comment_fetch.clean_comment_html(raw), "")
        self.assertEqual(comment_fetch.clean_comment_html(raw, emoji_replace=0), "")

    def test_class_hint_marks_emoji_outside_face_cdn(self):
        src = "https://example.com/ui.png"
        for class_name in ("emoji", "t-face", "Expression", "EMOJI"):
            raw = f'<img class="{class_name}" src="{src}" alt="[微笑]" />'
            with self.subTest(class_name=class_name):
                self.assertEqual(comment_fetch.clean_comment_html(raw), "[微笑]")
                self.assertEqual(
                    comment_fetch.clean_comment_html(raw, emoji_replace=0),
                    f"![微笑]({src})",
                )

    def test_class_hint_without_src_and_alt(self):
        self.assertEqual(
            comment_fetch.clean_comment_html('<img class="emoji" />'), "[表情]"
        )

    def test_empty_alt_self_closing_and_quote_styles(self):
        cases = [
            f'<img src="{self.FACE_SRC}" alt="" />',
            f"<img src='{self.FACE_SRC}' alt='' />",
            f"<img class='emoji' src='{self.FACE_SRC}' alt=''>",
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                self.assertEqual(
                    comment_fetch.clean_comment_html(raw, emoji_replace=0),
                    f"![表情]({self.FACE_SRC})",
                )
                self.assertEqual(
                    comment_fetch.clean_comment_html(raw, emoji_replace=1),
                    f"[表情]({self.FACE_SRC})",
                )

    def test_bare_img_without_emoji_marker_is_dropped(self):
        self.assertEqual(comment_fetch.clean_comment_html("<img />"), "")
        self.assertEqual(
            comment_fetch.clean_comment_html("<img />", emoji_replace=0), ""
        )
        self.assertEqual(
            comment_fetch.clean_comment_html('<img alt="[微笑]" />', emoji_replace=0), ""
        )


class LinkPreviewTest(unittest.TestCase):
    """网页链接卡片：按 link_preview 输出 Markdown 链接，图标丢弃。"""

    def test_card_default_hidden_preview(self):
        cleaned = comment_fetch.clean_comment_html(CARD_HTML)
        self.assertEqual(cleaned, f"看了！！！[网页链接]({CARD_URL})")
        self.assertNotIn("h5.sinaimg.cn", cleaned)
        self.assertNotIn("表情", cleaned)

    def test_card_preview_enabled(self):
        cleaned = comment_fetch.clean_comment_html(CARD_HTML, link_preview=True)
        self.assertEqual(cleaned, f"看了！！！![网页链接]({CARD_URL})")
        self.assertNotIn("h5.sinaimg.cn", cleaned)
        self.assertNotIn("表情", cleaned)

    def test_card_preview_independent_of_emoji_mode(self):
        for emoji_replace in (0, 1):
            for link_preview in (False, True):
                with self.subTest(emoji_replace=emoji_replace, link_preview=link_preview):
                    cleaned = comment_fetch.clean_comment_html(
                        CARD_HTML,
                        emoji_replace=emoji_replace,
                        link_preview=link_preview,
                    )
                    expected = (
                        f"看了！！！![网页链接]({CARD_URL})"
                        if link_preview
                        else f"看了！！！[网页链接]({CARD_URL})"
                    )
                    self.assertEqual(cleaned, expected)

    def test_card_text_with_whitespace_and_nested_tags(self):
        raw = (
            '<a href="https://example.com/a"> <span>网页</span>\n'
            "<span>链接</span> </a>"
        )
        self.assertEqual(
            comment_fetch.clean_comment_html(raw), "[网页链接](https://example.com/a)"
        )

    def test_card_icon_with_alt_text_is_dropped(self):
        raw = (
            '<a href="https://m.weibo.cn/x">'
            f'<img src="{CARD_ICON}" alt="[微笑]" class="url-icon">'
            "网页链接</a>"
        )
        cleaned = comment_fetch.clean_comment_html(raw)
        self.assertEqual(cleaned, "[网页链接](https://m.weibo.cn/x)")
        self.assertNotIn("微笑", cleaned)

    def test_non_preview_http_anchor_keeps_text_only(self):
        cleaned = comment_fetch.clean_comment_html(
            '<a href="https://example.com/x">普通链接</a>'
        )
        self.assertEqual(cleaned, "普通链接")
        self.assertNotIn("example.com", cleaned)

    def test_relative_href_card_text_keeps_text_only(self):
        # 只有 http(s) 链接才按卡片处理；相对链接保持原行为
        cleaned = comment_fetch.clean_comment_html('<a href="/n/网页链接">网页链接</a>')
        self.assertEqual(cleaned, "网页链接")

    def test_at_mention_anchor_unchanged(self):
        raw = "回复<a href='/n/示例用户'>@示例用户</a>:"
        self.assertEqual(comment_fetch.clean_comment_html(raw), "回复@示例用户:")
        self.assertEqual(
            comment_fetch.clean_comment_html(raw, link_preview=True),
            "回复@示例用户:",
        )

    def test_leading_at_mention_anchor_unchanged(self):
        raw = "<a href='/n/示例用户'>@示例用户</a> 说得对"
        self.assertEqual(comment_fetch.clean_comment_html(raw), "@示例用户 说得对")

    def test_unclosed_http_anchor_keeps_text(self):
        # 畸形 HTML：<a> 没闭合时不能吞掉后文，原样输出文本
        cleaned = comment_fetch.clean_comment_html(
            '<a href="https://example.com/x">普通文字'
        )
        self.assertEqual(cleaned, "普通文字")


class LinkPreviewParseCommentTest(unittest.TestCase):
    """parse_comment / fetch_comments 的 link_preview 透传。"""

    def test_parse_comment_default(self):
        comment = comment_fetch.parse_comment(
            raw_comment("1", text=CARD_HTML), "500", is_root=True
        )
        self.assertEqual(comment.text, f"看了！！！[网页链接]({CARD_URL})")

    def test_parse_comment_preview_enabled(self):
        comment = comment_fetch.parse_comment(
            raw_comment("1", text=CARD_HTML), "500", is_root=True, link_preview=True
        )
        self.assertEqual(comment.text, f"看了！！！![网页链接]({CARD_URL})")

    def test_parse_comment_emoji_semantics_kept(self):
        comment = comment_fetch.parse_comment(
            raw_comment("1", text=EMOJI_HTML), "500", is_root=True, emoji_replace=0
        )
        self.assertEqual(
            comment.text,
            f"早上好咘咘![赢牛奶]({EMOJI_SRC}) 今天晚上有红月哦!！",
        )

    def test_fetch_options_link_preview_default_off(self):
        self.assertFalse(comment_fetch.FetchOptions().link_preview)

    def test_fetch_comments_passes_link_preview(self):
        session = FakeSession(
            [hotflow_page([raw_comment("1", text=CARD_HTML)], max_id=0)]
        )
        result = comment_fetch.fetch_comments(
            session, {}, "500", options(link_preview=True)
        )
        self.assertEqual(result.roots[0].text, f"看了！！！![网页链接]({CARD_URL})")

    def test_fetch_comments_default_keeps_link_preview_off(self):
        session = FakeSession(
            [hotflow_page([raw_comment("1", text=CARD_HTML)], max_id=0)]
        )
        result = comment_fetch.fetch_comments(session, {}, "500", options())
        self.assertEqual(result.roots[0].text, f"看了！！！[网页链接]({CARD_URL})")


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


class NoContentHintTest(unittest.TestCase):
    """接口 ok=0 的业务提示：识别为「无评论」而不是抓取失败。"""

    def test_known_hints(self):
        cases = [
            "还没有内容",
            "还没有人评论哦~快来抢沙发！",
            "暂无评论",
            "快来抢沙发",
            "快来发表你的评论吧",
            "已过滤部分评论",
            "博主已开启防火墙，部分内容暂不展示",
        ]
        for message in cases:
            self.assertTrue(
                comment_fetch._is_no_content_message(message), message
            )

    def test_unknown_hint_is_not_no_content(self):
        self.assertFalse(
            comment_fetch._is_no_content_message("请求过于频繁，请稍后再试")
        )
        self.assertFalse(comment_fetch._is_no_content_message(""))

    def test_empty_data_message_is_no_content(self):
        # 旧微博评论被删/不可见：接口固定返回「暂无数据」，按无评论处理
        self.assertTrue(comment_fetch._is_no_content_message("暂无数据"))
        self.assertTrue(comment_fetch._is_no_content_message("暂无内容"))


class CommentPictureCardTest(unittest.TestCase):
    """评论区「评论配图」链接卡片：识别为图片，不输出占位文字。"""

    IMAGE_URL = "https://example.com/comment-image.jpg"
    HTML = (
        "回复 <a href='/n/示例评论者'>@示例评论者</a>:能看到吗 "
        '<a  href="https://weibo.cn/sinaurl?u=https%3A%2F%2Fexample.com%2F'
        'comment-image.jpg" data-hide="">'
        "<span class='url-icon'><img style='width: 1rem;height: 1rem' "
        "src='https://h5.sinaimg.cn/upload/2015/01/21/20/"
        "timeline_card_small_web_default.png'></span>"
        '<span class="surl-text">评论配图</span></a>'
    )

    def test_picture_card_text_and_picture(self):
        pictures = []
        cleaned = comment_fetch.clean_comment_html(self.HTML, pictures=pictures)
        self.assertEqual(cleaned, "回复 @示例评论者:能看到吗")
        self.assertEqual(pictures, [self.IMAGE_URL])

    def test_parse_comment_sets_pic_url(self):
        raw = {
            "id": "1000000012",
            "text": self.HTML,
            "user": {"id": "1", "screen_name": "甲"},
            "created_at": "2026-10-30 11:49:48",
        }
        comment = comment_fetch.parse_comment(raw, "1000000011")
        self.assertEqual(comment.pic_url, self.IMAGE_URL)
        self.assertNotIn("评论配图", comment.text)

    def test_direct_image_url_card(self):
        html = (
            '<a href="https://wx1.sinaimg.cn/large/a.jpg">'
            '<span class="surl-text">评论配图</span></a>'
        )
        pictures = []
        cleaned = comment_fetch.clean_comment_html(html, pictures=pictures)
        self.assertEqual(cleaned, "")
        self.assertEqual(pictures, ["https://wx1.sinaimg.cn/large/a.jpg"])


class EmptyDataHintTest(unittest.TestCase):
    """「暂无数据」类空响应：不算失败，但需记录提示供待复核清单使用。"""

    def test_empty_data_hint_not_failure(self):
        session = FakeSession([{"ok": 0, "msg": "暂无数据"}])
        result = comment_fetch.fetch_comments(session, {}, "500", options())
        self.assertFalse(result.failed)
        self.assertEqual(result.roots, [])
        self.assertEqual(result.stats.empty_hint, "暂无数据")
        self.assertTrue(comment_fetch.is_ambiguous_empty_hint("暂无数据"))
        self.assertTrue(comment_fetch.is_ambiguous_empty_hint("暂无内容"))

    def test_plain_empty_message_not_ambiguous(self):
        session = FakeSession([{"ok": 0, "msg": "还没有人评论"}])
        result = comment_fetch.fetch_comments(session, {}, "500", options())
        self.assertFalse(result.failed)
        self.assertEqual(result.stats.empty_hint, "还没有人评论")
        self.assertFalse(
            comment_fetch.is_ambiguous_empty_hint(result.stats.empty_hint)
        )
        self.assertFalse(
            comment_fetch.is_ambiguous_empty_hint("请求过于频繁，请稍后再试")
        )

    def test_ok_zero_without_message_is_ambiguous(self):
        # 实测旧微博评论被删/不可见时接口直接返回 {"ok": 0}（无 msg）
        session = FakeSession([{"ok": 0}])
        result = comment_fetch.fetch_comments(session, {}, "500", options())
        self.assertFalse(result.failed)
        self.assertEqual(result.roots, [])
        self.assertTrue(
            comment_fetch.is_ambiguous_empty_hint(result.stats.empty_hint)
        )

    def test_check_weibo_visibility(self):
        class StatusSession:
            def __init__(self, payload):
                self.payload = payload

            def get(self, url, params=None, headers=None, timeout=None):
                if isinstance(self.payload, Exception):
                    raise self.payload
                return FakeResponse(self.payload)

        self.assertEqual(
            comment_fetch.check_weibo_visibility(
                StatusSession({"ok": 1, "data": {"visible": {"type": 0}}}),
                {},
                "1",
            )[0],
            "visible",
        )
        # visible.type 是分享范围，不是无权限；粉丝/好友/仅自己均视为可访问
        for scope_type in (1, 6, 7, 10):
            self.assertEqual(
                comment_fetch.check_weibo_visibility(
                    StatusSession({"ok": 1, "data": {"visible": {"type": scope_type}}}),
                    {},
                    "1",
                )[0],
                "visible",
            )
        self.assertEqual(
            comment_fetch.check_weibo_visibility(
                StatusSession({"ok": 0, "msg": "该微博不存在"}), {}, "1"
            )[0],
            "deleted",
        )
        self.assertEqual(
            comment_fetch.check_weibo_visibility(
                StatusSession({"ok": 0, "msg": "暂无查看权限"}), {}, "1"
            )[0],
            "restricted",
        )
        self.assertEqual(
            comment_fetch.check_weibo_visibility(
                StatusSession({"ok": 0, "errno": "20112", "msg": "其他提示"}), {}, "1"
            )[0],
            "restricted",
        )
        self.assertEqual(
            comment_fetch.check_weibo_visibility(
                StatusSession({"ok": 0, "msg": "接口升级中，请稍后"}), {}, "1"
            ),
            ("unknown", "接口升级中，请稍后"),
        )
        self.assertEqual(
            comment_fetch.check_weibo_visibility(
                StatusSession({"ok": 0, "msg": "隐私设置"}), {}, "1"
            )[0],
            "unknown",
        )
        self.assertEqual(
            comment_fetch.check_weibo_visibility(
                StatusSession(RuntimeError("timeout")), {}, "1"
            )[0],
            "unknown",
        )
