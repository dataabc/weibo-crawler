"""util.comment_markdown 的单元测试（无网络）。"""

import glob
import os
import tempfile
import unittest

from util import comment_markdown
from util.comment_fetch import Comment

MD_TWO_BLOCKS = """## 2026-04-27 [用户A] 微博存档

### 19:47:20
<!-- weibo_id: 111 -->
正文一。

![img](原创微博图片/a.jpg)

---

### 21:33:31
<!-- weibo_id: 222 -->
正文二。

---
"""


def make_root(cid="1", name="用户A", created="2024-05-03 00:22:41", text="评论正文", **kwargs):
    return Comment(id=cid, weibo_id="111", is_root=True, user_name=name, created_at=created, text=text, **kwargs)


def make_reply(cid="2", name="用户B", created="2024-05-03 03:32:25", text="回复正文", **kwargs):
    return Comment(
        id=cid, weibo_id="111", is_root=False, user_name=name, created_at=created, text=text, **kwargs
    )


class RenderTest(unittest.TestCase):
    def test_basic_format(self):
        root = make_root(like_count=5)
        root.replies.append(make_reply(reply_target="用户A"))
        block = comment_markdown.render_comment_block("111", [root], total_number=10344)
        self.assertTrue(
            block.startswith("#### 评论\n\n<!-- comments: 111 total=10344 saved=2 -->\n\n")
        )
        self.assertEqual(
            block.split("\n")[0], "#### 评论"
        )
        self.assertIn("> **用户A** · 2024-05-03 00:22:41", block)
        self.assertIn("> 评论正文", block)
        self.assertIn("> > **用户B** · 2024-05-03 03:32:25 · 回复 用户A", block)
        self.assertIn("> > 回复正文", block)
        self.assertTrue(block.endswith("\n\n<!-- /comments -->"))
        self.assertNotIn("fetched=", block)
        # 默认不显示点赞
        self.assertNotIn("赞 5", block)

    def test_flags(self):
        root = make_root(like_count=5)
        root.replies.append(make_reply(reply_target="用户A"))
        block = comment_markdown.render_comment_block(
            "111",
            [root],
            total_number=2,
            options=comment_markdown.RenderOptions(show_like_count=True, show_reply_target=False),
        )
        self.assertIn("赞 5", block)
        self.assertNotIn("回复 用户A", block)

    def test_pic_line(self):
        root = make_root(pic_file="2024-05-03_00-22-41_1.jpg")
        block = comment_markdown.render_comment_block(
            "111", [root], options=comment_markdown.RenderOptions(image_dir="评论图片")
        )
        self.assertIn("> ![评论图片](评论图片/2024-05-03_00-22-41_1.jpg)", block)

    def test_multiline_and_quote_escape(self):
        root = make_root(text="第一行\n\n第二行\n>引用")
        block = comment_markdown.render_comment_block("111", [root])
        self.assertIn("> 第一行", block)
        self.assertIn(">\n", block)
        self.assertIn("> 第二行", block)
        self.assertIn("> \\>引用", block)

    def test_multiple_roots_separated_by_quote_blank(self):
        block = comment_markdown.render_comment_block(
            "111",
            [make_root(cid="1"), make_root(cid="3", created="2024-05-03 01:10:02", text="另一条")],
        )
        lines = block.split("\n")
        # 第一条正文与第二条之间用单独一个 > 空行分隔
        self.assertIn(">", lines)
        self.assertGreater(lines.count(">"), 0)

    def test_empty_returns_empty_string(self):
        self.assertEqual(comment_markdown.render_comment_block("111", []), "")

    def test_unknown_total_marker(self):
        block = comment_markdown.render_comment_block("111", [make_root()], total_number=0)
        self.assertIn("<!-- comments: 111 total=0 saved=1 -->", block)
        # 不再有统计行
        self.assertNotIn("共 未知 条", block)
        self.assertNotIn("**评论**（", block)

    def test_render_is_stable(self):
        """同一份评论数据每次渲染结果一致（不含抓取时间等易变内容）。"""
        root = make_root()
        first = comment_markdown.render_comment_block("111", [root], total_number=1)
        second = comment_markdown.render_comment_block("111", [root], total_number=1)
        self.assertEqual(first, second)

    def test_repeated_upsert_same_block_no_change(self):
        block = comment_markdown.render_comment_block("111", [make_root()], total_number=1)
        first, changed1 = comment_markdown.upsert_comment_block(MD_TWO_BLOCKS, "111", block)
        self.assertTrue(changed1)
        second, changed2 = comment_markdown.upsert_comment_block(first, "111", block)
        self.assertFalse(changed2)
        self.assertEqual(second, first)


class SpanTest(unittest.TestCase):
    def test_find_weibo_span_middle(self):
        span = comment_markdown.find_weibo_span(MD_TWO_BLOCKS, "111")
        self.assertIsNotNone(span)
        start, end = span
        self.assertTrue(MD_TWO_BLOCKS[start:end].startswith("<!-- weibo_id: 111 -->"))
        self.assertTrue(MD_TWO_BLOCKS[start:end].endswith("---\n\n"))
        self.assertEqual(MD_TWO_BLOCKS[end:].startswith("### 21:33:31"), True)

    def test_find_weibo_span_last(self):
        text = "### 10:00:00\n<!-- weibo_id: 333 -->\n正文\n"
        span = comment_markdown.find_weibo_span(text, "333")
        self.assertIsNotNone(span)
        self.assertEqual(span[1], len(text))

    def test_find_weibo_span_missing(self):
        self.assertIsNone(comment_markdown.find_weibo_span(MD_TWO_BLOCKS, "999"))

    def test_find_comment_span_includes_title(self):
        block = comment_markdown.render_comment_block("111", [make_root()])
        updated, _ = comment_markdown.upsert_comment_block(MD_TWO_BLOCKS, "111", block)
        span = comment_markdown.find_comment_span(updated, "111")
        self.assertIsNotNone(span)
        start, end = span
        self.assertTrue(updated[start:end].startswith("#### 评论"))
        self.assertTrue(updated[start:end].endswith("<!-- /comments -->"))


class UpsertTest(unittest.TestCase):
    def test_insert_before_separator(self):
        block = comment_markdown.render_comment_block("111", [make_root()])
        updated, changed = comment_markdown.upsert_comment_block(MD_TWO_BLOCKS, "111", block)
        self.assertTrue(changed)
        self.assertEqual(updated.count("<!-- weibo_id: 111 -->"), 1)
        self.assertEqual(updated.count("<!-- comments: 111"), 1)
        self.assertEqual(updated.count("#### 评论"), 1)
        # 评论块在 --- 之前
        block_pos = updated.index("#### 评论")
        sep_pos = updated.index("\n---\n", block_pos)
        self.assertLess(block_pos, sep_pos)
        # 第二个块不受影响
        self.assertIn("### 21:33:31\n<!-- weibo_id: 222 -->\n正文二。\n", updated)
        self.assertEqual(updated.count("<!-- weibo_id:"), 2)

    def test_replace_is_idempotent(self):
        block1 = comment_markdown.render_comment_block("111", [make_root()], total_number=1)
        first, _ = comment_markdown.upsert_comment_block(MD_TWO_BLOCKS, "111", block1)
        block2 = comment_markdown.render_comment_block(
            "111", [make_root(), make_root(cid="9", created="2024-05-04 00:00:00")], total_number=2
        )
        second, changed = comment_markdown.upsert_comment_block(first, "111", block2)
        self.assertTrue(changed)
        self.assertEqual(second.count("<!-- comments: 111"), 1)
        self.assertEqual(second.count("#### 评论"), 1)
        self.assertIn("total=2 saved=2", second)
        self.assertEqual(second.count("<!-- weibo_id:"), 2)

    def test_upsert_missing_weibo(self):
        block = comment_markdown.render_comment_block("999", [make_root()])
        updated, changed = comment_markdown.upsert_comment_block(MD_TWO_BLOCKS, "999", block)
        self.assertFalse(changed)
        self.assertEqual(updated, MD_TWO_BLOCKS)

    def test_empty_block_no_change(self):
        updated, changed = comment_markdown.upsert_comment_block(MD_TWO_BLOCKS, "111", "")
        self.assertFalse(changed)
        self.assertEqual(updated, MD_TWO_BLOCKS)

    def test_last_block_without_separator(self):
        text = "### 10:00:00\n<!-- weibo_id: 333 -->\n正文\n"
        block = comment_markdown.render_comment_block("333", [make_root()])
        updated, changed = comment_markdown.upsert_comment_block(text, "333", block)
        self.assertTrue(changed)
        self.assertIn("正文\n\n#### 评论\n\n<!-- comments: 333", updated)
        self.assertTrue(updated.endswith("<!-- /comments -->\n\n"))


class UpdateFileTest(unittest.TestCase):
    def test_update_and_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "2026-04-27.md")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(MD_TWO_BLOCKS)
            block = comment_markdown.render_comment_block("111", [make_root()])
            changed = comment_markdown.update_markdown_file(path, "111", block)
            self.assertTrue(changed)
            with open(path, encoding="utf-8") as handle:
                content = handle.read()
            self.assertIn("#### 评论", content)
            self.assertIn("<!-- comments: 111", content)
            self.assertEqual(len(glob.glob(path + ".bak.*")), 1)

    def test_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.md")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(MD_TWO_BLOCKS)
            block = comment_markdown.render_comment_block("111", [make_root()])
            changed = comment_markdown.update_markdown_file(path, "111", block, dry_run=True)
            self.assertTrue(changed)
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), MD_TWO_BLOCKS)
            self.assertEqual(glob.glob(path + ".bak.*"), [])

    def test_no_change_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.md")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(MD_TWO_BLOCKS)
            changed = comment_markdown.update_markdown_file(path, "999", "x")
            self.assertFalse(changed)
            self.assertEqual(glob.glob(path + ".bak.*"), [])


if __name__ == "__main__":
    unittest.main()


class BackupStateTest(unittest.TestCase):
    def test_same_file_backed_up_only_once_per_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "2026-04-27.md")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(MD_TWO_BLOCKS)
            state: set = set()
            first = comment_markdown.render_comment_block("111", [make_root()], total_number=1)
            changed1 = comment_markdown.update_markdown_file(
                path, "111", first, backup=True, backup_state=state
            )
            second = comment_markdown.render_comment_block(
                "111", [make_root(), make_root(cid="9", created="2024-05-04 00:00:00")], total_number=2
            )
            changed2 = comment_markdown.update_markdown_file(
                path, "111", second, backup=True, backup_state=state
            )
            self.assertTrue(changed1)
            self.assertTrue(changed2)
            self.assertEqual(len(glob.glob(path + ".bak.*")), 1)

    def test_backup_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.md")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(MD_TWO_BLOCKS)
            block = comment_markdown.render_comment_block("111", [make_root()])
            comment_markdown.update_markdown_file(path, "111", block, backup=False)
            self.assertEqual(glob.glob(path + ".bak.*"), [])
