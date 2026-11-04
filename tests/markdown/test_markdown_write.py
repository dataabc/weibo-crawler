"""Markdown 写入改造单元测试。"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


from tests import _support  # noqa: E402

_support.bootstrap()

import weibo  # noqa: E402

sys.modules.pop("json5", None)


def _make_wb(tmpdir, order="old_to_new"):
    wb = weibo.Weibo.__new__(weibo.Weibo)
    wb.user = {"screen_name": "测试用户"}
    wb.user_config = {"user_id": "1234567890"}
    wb.markdown_split_by = "day"
    wb.markdown_sort_order = order
    wb.markdown_image_order = "live_photo_first"
    wb.markdown_file_dir = {
        "original_image_dir": "img",
        "retweet_image_dir": "img",
        "original_video_dir": "原创微博视频",
        "retweet_video_dir": "转发微博视频",
        "original_live_photo_dir": "原创微博Live Photo视频",
        "retweet_live_photo_dir": "转发微博Live Photo视频",
        "comment_image_dir": "评论图片",
    }
    wb.markdown_preview = {}
    wb.only_crawl_original = 1
    wb.original_pic_download = 0
    wb.original_video_download = 0
    wb.original_live_photo_download = 0
    wb.retweet_pic_download = 0
    wb.retweet_video_download = 0
    wb.retweet_live_photo_download = 0
    wb.weibo = []
    wb.new_written_weibo = {}
    wb.run_failures = []
    wb.markdown_write_backup = 1
    wb.get_filepath = lambda _type: tmpdir
    return wb


def _weibo_item(wid, created_at, text="正文"):
    return {
        "id": wid,
        "created_at": created_at,
        "text": text,
        "pics": "",
        "links": [],
    }


EXISTING = """## 2024-05-03 [测试用户] 微博存档

### 08:00:00

<!-- weibo_id: 100 -->

旧微博一

---

### 12:00:00

<!-- weibo_id: 200 -->

旧微博二

---
"""


class InsertPositionTest(unittest.TestCase):
    def test_insert_in_middle(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "2024-05-03.md")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(EXISTING)
            wb = _make_wb(tmpdir)
            new = [_weibo_item(150, "2024-05-03T10:00:00", "中间新微博")]
            with mock.patch("weibo.logger"):
                wb.generate_markdown_file("2024-05-03", new)
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            idx_08 = text.index("### 08:00:00")
            idx_10 = text.index("### 10:00:00")
            idx_12 = text.index("### 12:00:00")
            self.assertTrue(idx_08 < idx_10 < idx_12)
            self.assertIn("中间新微博", text)

    def test_insert_at_end_when_newest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "2024-05-03.md")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(EXISTING)
            wb = _make_wb(tmpdir)
            new = [_weibo_item(300, "2024-05-03T23:00:00", "最新微博")]
            with mock.patch("weibo.logger"):
                wb.generate_markdown_file("2024-05-03", new)
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            self.assertEqual(
                text.rindex("### 08:00:00") < text.rindex("### 23:00:00"), True
            )

    def test_new_block_format(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "2024-05-03.md")
            wb = _make_wb(tmpdir)
            new = [_weibo_item(150, "2024-05-03T10:00:00", "正文")]
            with mock.patch("weibo.logger"):
                wb.generate_markdown_file("2024-05-03", new)
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            # 块头后空行、marker 后空行（用户定稿格式）
            self.assertIn(
                "### 10:00:00\n\n<!-- weibo_id: 150 -->\n\n正文", text
            )

    def test_duplicate_id_not_written_twice(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "2024-05-03.md")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(EXISTING)
            wb = _make_wb(tmpdir)
            new = [_weibo_item(100, "2024-05-03T08:00:00", "重复")]
            with mock.patch("weibo.logger"):
                wb.generate_markdown_file("2024-05-03", new)
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            self.assertEqual(text.count("<!-- weibo_id: 100 -->"), 1)

    def test_backup_same_day_keeps_multiple(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "2024-05-03.md")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(EXISTING)
            wb = _make_wb(tmpdir)
            with mock.patch("weibo.logger"):
                wb.generate_markdown_file(
                    "2024-05-03", [_weibo_item(150, "2024-05-03T10:00:00", "一")]
                )
                wb.generate_markdown_file(
                    "2024-05-03", [_weibo_item(160, "2024-05-03T11:00:00", "二")]
                )
            backups = [f for f in os.listdir(tmpdir) if ".bak." in f]
            # ：同一天两次写入保留 2 份备份（第一份 + _2 编号）
            self.assertEqual(len(backups), 2)
            self.assertTrue(any("_2" in name for name in backups))

    def test_unparsed_block_preserved(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "2024-05-03.md")
            text = (
                "## 2024-05-03 [测试用户] 微博存档\n\n"
                "### 08:00:00\n\n<!-- weibo_id: 100 -->\n\n旧\n\n---\n\n"
                "### 07:00:00\n\n人工添加的段落（无微博 ID）\n\n---\n"
            )
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
            wb = _make_wb(tmpdir)
            with mock.patch("weibo.logger"):
                wb.generate_markdown_file(
                    "2024-05-03", [_weibo_item(150, "2024-05-03T10:00:00", "新")]
                )
            with open(path, encoding="utf-8") as handle:
                result = handle.read()
            self.assertIn("人工添加的段落（无微博 ID）", result)
            self.assertIn("<!-- weibo_id: 150 -->", result)


class RetweetRenderTest(unittest.TestCase):
    '收敛前先锁定输出特征，防止合并把转发正文顺序/引用格式改掉。'

    def test_retweet_block_renders_original_then_quoted_retweet(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "2024-05-03.md")
            wb = _make_wb(tmpdir)
            wb.only_crawl_original = 0
            item = {
                "id": 150,
                "created_at": "2024-05-03T10:00:00",
                "text": "原创部分正文",
                "pics": "",
                "links": [],
                "retweet": {
                    "id": "151",
                    "created_at": "2024-05-03T09:00:00",
                    "text": "转发部分正文",
                    "pics": "",
                    "links": [],
                    "user_id": "999",
                    "screen_name": "原作者",
                },
            }
            with mock.patch("weibo.logger"):
                wb.generate_markdown_file("2024-05-03", [item])
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn("原创部分正文", text)
            self.assertIn("转发部分正文", text)
            # 原创正文在前、引用转发在后
            self.assertLess(text.index("原创部分正文"), text.index("转发部分正文"))
            # 转发部分应以引用块形式呈现
            self.assertIn("> 转发: 转发部分正文", text)


class LocationLinkRenderTest(unittest.TestCase):
    """「位置」类网页链接（weibo.com/p/100101…）默认不写进 Markdown。"""

    def _render(self, show):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "2024-05-03.md")
            wb = _make_wb(tmpdir)
            wb.markdown_show_location_link = show
            item = _weibo_item(210, "2024-05-03T10:00:00")
            item["links"] = [
                "http://weibo.com/p/100101B2094655D06CA0FB409F",
                "https://example.com/a",
            ]
            with mock.patch("weibo.logger"):
                wb.generate_markdown_file("2024-05-03", [item])
            with open(path, encoding="utf-8") as handle:
                return handle.read()

    def test_location_link_hidden_by_default(self):
        text = self._render(show=0)
        self.assertNotIn("100101", text)
        self.assertIn("[网页链接](https://example.com/a)", text)

    def test_location_link_rendered_when_enabled(self):
        text = self._render(show=1)
        self.assertIn(
            "[网页链接](http://weibo.com/p/100101B2094655D06CA0FB409F)", text
        )


class LocationTextPositionTest(unittest.TestCase):
    """定位文字（如「测试城市·测试地点」）移到微博块最后、媒体之后。"""

    def _render(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "2019-12-27.md")
            wb = _make_wb(tmpdir)
            wb.original_pic_download = 1
            wb.get_markdown_image_filenames = lambda w, created: ["1.jpg", "2.jpg"]
            item = {
                "id": "1000000013",
                "created_at": "2019-12-27T23:59:23",
                "text": "测试正文示例\n \n测试城市·测试地点\n ",
                "location": "测试城市·测试地点",
                "pics": "x",
                "links": [],
            }
            with mock.patch("weibo.logger"):
                wb.generate_markdown_file("2019-12-27", [item])
            with open(path, encoding="utf-8") as handle:
                return handle.read()

    def test_location_text_after_media(self):
        text = self._render()
        first_img = text.index("![img]")
        last_img = text.rindex("![img]")
        loc = text.index("测试城市·测试地点")
        self.assertGreater(loc, last_img)
        self.assertGreater(loc, first_img)
        self.assertLess(loc, text.index("---"))
        self.assertEqual(text.count("测试城市·测试地点"), 1)
        self.assertIn("📍 测试城市·测试地点", text)
        self.assertNotIn(" \n", text)

    def test_split_location_text(self):
        from util.markdown import markdown_render

        self.assertEqual(
            markdown_render._split_location_text(
                "正文\n测试城市·测试地点", "测试城市·测试地点"
            ),
            ("正文", "测试城市·测试地点"),
        )
        self.assertEqual(
            markdown_render._split_location_text(
                "正文 测试城市·测试地点", "测试城市·测试地点"
            ),
            ("正文", "测试城市·测试地点"),
        )
        self.assertEqual(
            markdown_render._split_location_text("正文", ""), ("正文", "")
        )


class LinkTitleGroupTest(unittest.TestCase):
    """含信息的网页链接：最后一行标题与链接紧挨，前面用空行隔开正文。"""

    URL = "http://music.163.com/#/song?id=1970790061"

    def _render(self, text):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "2026-10-30.md")
            wb = _make_wb(tmpdir)
            item = _weibo_item(300, "2026-10-30T00:43:23", text=text)
            item["links"] = [self.URL]
            with mock.patch("weibo.logger"):
                wb.generate_markdown_file("2026-10-30", [item])
            with open(path, encoding="utf-8") as handle:
                return handle.read()

    def test_last_line_becomes_link_title(self):
        text = self._render(
            "这又何尝不是从一道缝隙里看黄昏\n但至于是黄昏还是朝阳，谁知道呢\n从一道缝隙看黄昏"
        )
        self.assertIn(
            "但至于是黄昏还是朝阳，谁知道呢\n\n从一道缝隙看黄昏\n"
            f"[网页链接]({self.URL})",
            text,
        )

    def test_single_line_text_keeps_blank_before_link(self):
        text = self._render("分享一首歌")
        self.assertIn(f"分享一首歌\n\n[网页链接]({self.URL})", text)


if __name__ == "__main__":
    unittest.main()
