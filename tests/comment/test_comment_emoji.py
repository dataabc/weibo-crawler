"""评论表情本地化（util.comment.comment_emoji）单元测试。"""

import os
import sys
import tempfile
import unittest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from tests import _support  # noqa: E402

_support.bootstrap()

from util.comment import comment_emoji  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 50 + b"IEND\xaeB`\x82"
PNG2 = b"\x89PNG\r\n\x1a\n" + b"9" * 50 + b"IEND\xaeB`\x82"
EMOJI_URL = (
    "https://face.t.sinajs.cn/t4/appstyle/expression/ext/normal/83/"
    "2021_yingniunai_mobile.png"
)


class EmojiNameTest(unittest.TestCase):
    def test_version_segment(self):
        self.assertEqual(comment_emoji.emoji_version(EMOJI_URL), "t4")
        self.assertEqual(
            comment_emoji.emoji_version("https://face.t.sinajs.cn/x/y/smile.png"), ""
        )

    def test_relative_path_clean_name(self):
        self.assertEqual(
            comment_emoji.emoji_relative_path("[泪]", EMOJI_URL), "t4/泪.png"
        )
        self.assertEqual(
            comment_emoji.emoji_relative_path(
                "[泪]", "https://face.t.sinajs.cn/x/y/smile.gif"
            ),
            "泪.gif",
        )
        self.assertEqual(
            comment_emoji.emoji_relative_path(
                "", "https://face.t.sinajs.cn/x/y/smile.png", "-2"
            ),
            "表情-2.png",
        )

    def test_is_emoji_img(self):
        self.assertTrue(comment_emoji.is_emoji_img(EMOJI_URL))
        self.assertTrue(comment_emoji.is_emoji_img("https://x/y.png", "surl-emoji"))
        self.assertFalse(
            comment_emoji.is_emoji_img(
                "https://h5.sinaimg.cn/upload/2015/09/25/3/timeline_card_small_web_default.png"
            )
        )


class EmojiStoreTest(unittest.TestCase):
    URL = EMOJI_URL

    def _block(self, src=URL):
        return f"> 早上好![赢牛奶]({src}) 今天有红月"

    def _store(self, tmpdir, downloader):
        return comment_emoji.EmojiStore(os.path.join(tmpdir, "emoji"), downloader)

    def test_localize_downloads_and_rewrites_relative_link(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = os.path.join(tmpdir, "weibo_data", "用户", "2026-05", "2026-05-01.md")
            os.makedirs(os.path.dirname(md_path))
            calls = []

            def download(url, dest):
                calls.append((url, dest))
                with open(dest, "wb") as handle:
                    handle.write(PNG)
                return True

            store = self._store(tmpdir, download)
            out = store.localize_block(self._block(), md_path)
            self.assertNotIn("https://face.t.sinajs.cn", out)
            self.assertIn("![赢牛奶](../../../emoji/t4/赢牛奶.png)", out)
            self.assertTrue(
                os.path.isfile(os.path.join(tmpdir, "emoji", "t4", "赢牛奶.png"))
            )
            self.assertEqual(len(calls), 1)
            self.assertEqual(store.downloaded, 1)
            self.assertTrue(
                os.path.isfile(os.path.join(tmpdir, "emoji", "emoji_index.json"))
            )

    def test_same_url_reused_without_download(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = os.path.join(tmpdir, "2026-05-01.md")
            calls = []

            def download(url, dest):
                calls.append(url)
                with open(dest, "wb") as handle:
                    handle.write(PNG)
                return True

            store = self._store(tmpdir, download)
            store.localize_block(self._block(), md_path)
            store2 = self._store(tmpdir, download)
            out2 = store2.localize_block(self._block(), md_path)
            self.assertNotIn("https://face.t.sinajs.cn", out2)
            self.assertEqual(len(calls), 1)

    def test_same_alt_different_content_kept_apart(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = os.path.join(tmpdir, "2026-05-01.md")
            urls = {
                "https://face.t.sinajs.cn/t4/a/smile.png": PNG,
                "https://face.t.sinajs.cn/t4/b/smile_new.png": PNG2,
            }

            def download(url, dest):
                with open(dest, "wb") as handle:
                    handle.write(urls[url])
                return True

            store = self._store(tmpdir, download)
            text = (
                "![微笑](https://face.t.sinajs.cn/t4/a/smile.png)"
                "![微笑](https://face.t.sinajs.cn/t4/b/smile_new.png)"
            )
            out = store.localize_block(text, md_path)
            self.assertIn("![微笑](emoji/t4/微笑.png)", out)
            self.assertIn("![微笑](emoji/t4/微笑-2.png)", out)
            self.assertTrue(
                os.path.isfile(os.path.join(tmpdir, "emoji", "t4", "微笑.png"))
            )
            self.assertTrue(
                os.path.isfile(os.path.join(tmpdir, "emoji", "t4", "微笑-2.png"))
            )

    def test_same_content_different_url_reuses_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = os.path.join(tmpdir, "2026-05-01.md")

            def download(url, dest):
                with open(dest, "wb") as handle:
                    handle.write(PNG)
                return True

            store = self._store(tmpdir, download)
            text = (
                "![微笑](https://face.t.sinajs.cn/t4/a/smile.png)"
                "![微笑](https://face.t.sinajs.cn/t4/b/smile.png)"
            )
            out = store.localize_block(text, md_path)
            self.assertEqual(out.count("![微笑](emoji/t4/微笑.png)"), 2)
            files = [
                n
                for n in os.listdir(os.path.join(tmpdir, "emoji", "t4"))
                if n.startswith("微笑")
            ]
            self.assertEqual(files, ["微笑.png"])

    def test_no_version_url_goes_to_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = os.path.join(tmpdir, "2026-05-01.md")

            def download(url, dest):
                with open(dest, "wb") as handle:
                    handle.write(PNG)
                return True

            store = self._store(tmpdir, download)
            out = store.localize_block(
                self._block("https://face.t.sinajs.cn/x/y/smile.png"), md_path
            )
            self.assertIn("![赢牛奶](emoji/赢牛奶.png)", out)

    def test_inline_size_renders_html_img(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = os.path.join(tmpdir, "2026-05-01.md")

            def download(url, dest):
                with open(dest, "wb") as handle:
                    handle.write(PNG)
                return True

            store = comment_emoji.EmojiStore(
                os.path.join(tmpdir, "emoji"), download, inline_size=18
            )
            out = store.localize_block(self._block(), md_path)
            self.assertIn(
                '<img src="emoji/t4/赢牛奶.png" width="18" height="18" '
                'alt="赢牛奶">',
                out,
            )
            self.assertNotIn("![赢牛奶]", out)

    def test_inline_size_zero_keeps_markdown(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = os.path.join(tmpdir, "2026-05-01.md")

            def download(url, dest):
                with open(dest, "wb") as handle:
                    handle.write(PNG)
                return True

            store = comment_emoji.EmojiStore(
                os.path.join(tmpdir, "emoji"), download, inline_size=0
            )
            out = store.localize_block(self._block(), md_path)
            self.assertIn("![赢牛奶](emoji/t4/赢牛奶.png)", out)

    def test_download_failure_keeps_external_link(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self._store(tmpdir, lambda url, dest: False)
            out = store.localize_block(self._block(), os.path.join(tmpdir, "x.md"))
            self.assertIn("https://face.t.sinajs.cn", out)
            self.assertEqual(store.failed, 1)

    def test_non_emoji_links_untouched(self):
        text = "> 见 [网页链接](https://m.weibo.cn/1/x)"
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self._store(tmpdir, lambda url, dest: True)
            self.assertEqual(
                store.localize_block(text, os.path.join(tmpdir, "x.md")), text
            )


if __name__ == "__main__":
    unittest.main()
