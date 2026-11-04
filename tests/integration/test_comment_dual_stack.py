"""评论块两套实现的一致性对照测试。

两条链串在**同一条流水线**上：``comment_worker`` 用前者渲染新块，``comment_backfill``
本文件把「当前一致」与「当前不一致」都固化为基线：

- ✅ 一致：渲染 → 对方解析 → 重渲染（单条/带回复时逐字一致；多条一级评论时
  组间分隔符从 ``>`` 规范化为空行，内容不变）；定位的区间终点相同；marker 格式互认。
  1. ~~分组粒度~~：``markdown_blocks`` 原按**空行**分组，把 ``comment_markdown``
     用 ``>`` 分隔的多条一级评论并成 1 组；现改为按**一级评论首行**切分
     （带 cid 时精确、旧块按 meta 行格式）；
  2. ~~合并重复~~：旧块 {A} + 新块 {A,B} 合并后不再重复。

> 行为变化提示：合并后写回的评论块，组间分隔符会从「单独一行 ``>``」规范化为
> 空行（markdown_blocks 的渲染格式）；评论内容与顺序不变。"""

import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests import _support  # noqa: E402

_support.bootstrap()

from util.comment import comment_markdown  # noqa: E402
from util.core import markdown_blocks  # noqa: E402

OPTIONS = comment_markdown.RenderOptions()


def make_comment(cid, text, user="甲", replies=()):
    # 渲染端读 ``id``（写进 meta 行的 <!-- cid: N -->），部分解析逻辑读 ``cid``
    return SimpleNamespace(
        id=cid,
        cid=cid,
        text=text,
        user={"screen_name": user},
        replies=list(replies),
        pic="",
    )


def md_with_blocks(*weibo_ids):
    parts = ["## 2024-06-01\n\n"]
    for index, wid in enumerate(weibo_ids):
        parts.append(
            f"### 1{index}:00:00\n<!-- weibo_id: {wid} -->\n正文{wid}\n\n---\n\n"
        )
    return "".join(parts)


class RenderRoundTripTest(unittest.TestCase):
    """``comment_markdown`` 渲染 → ``markdown_blocks`` 解析 → 重渲染逐字一致。"""

    def _roundtrip(self, roots, total=0, expect_exact=True):
        rendered = comment_markdown.render_comment_block(
            "100", roots, total_number=total, options=OPTIONS
        )
        marker, groups = markdown_blocks.parse_comments_block(rendered)
        self.assertTrue(groups, "对方解析不出任何评论组")
        rebuilt = markdown_blocks.render_comments_block(marker, groups)
        if expect_exact:
            self.assertEqual(rebuilt, rendered)
        else:
            # 多条一级评论：组间分隔符 `>` 会规范化为空行，内容必须完整
            self.assertEqual(len(groups), len(roots))
            for root in roots:
                self.assertIn(root.text, rebuilt)
        return rendered, marker, groups

    def test_single_comment_roundtrip(self):
        _, marker, groups = self._roundtrip([make_comment("c1", "正文")], total=1)
        self.assertIn("<!-- comments: 100", marker)
        self.assertEqual(len(groups), 1)

    def test_comment_with_reply_roundtrip(self):
        root = make_comment("c1", "一级", replies=[make_comment("c2", "二级", user="乙")])
        _, _, groups = self._roundtrip([root], total=2)
        self.assertTrue(groups[0].has_replies)
        self.assertIn("二级", groups[0].raw)

    def test_two_root_comments_roundtrip(self):
        _, _, groups = self._roundtrip(
            [make_comment("1", "评论A"), make_comment("2", "评论B", user="乙")],
            total=2,
            expect_exact=False,
        )
        self.assertEqual(len(groups), 2)
        self.assertIn("评论A", groups[0].raw)
        self.assertIn("评论B", groups[1].raw)


class SpanConsistencyTest(unittest.TestCase):
    """``find_weibo_span`` 与 ``parse_markdown`` 的块区间关系。"""

    def test_span_end_matches_block_end_without_comments(self):
        text = md_with_blocks("100", "200")
        span = comment_markdown.find_weibo_span(text, "100")
        block = markdown_blocks.parse_markdown(text).find("100")
        self.assertIsNotNone(span)
        self.assertIsNotNone(block)
        # 两套的结束位置一致；起点不同：find_weibo_span 从 marker 起，块从标题起
        self.assertEqual(span[1], block.end)
        self.assertGreaterEqual(span[0], block.start)
        self.assertTrue(block.raw.startswith("### "))

    def test_span_end_matches_block_end_with_comments(self):
        text = md_with_blocks("100", "200").replace(
            "正文100\n\n---",
            "正文100\n\n#### 评论\n\n<!-- comments: 100 total=1 saved=1 -->\n\n"
            "> **甲** · 2024-01-01 00:00:00\n> 评论\n\n<!-- /comments -->\n\n---",
        )
        span = comment_markdown.find_weibo_span(text, "100")
        block = markdown_blocks.parse_markdown(text).find("100")
        self.assertEqual(span[1], block.end)
        self.assertTrue(block.comments)

    def test_comment_span_inside_weibo_span(self):
        text = md_with_blocks("100").replace(
            "正文100\n\n---",
            "正文100\n\n#### 评论\n\n<!-- comments: 100 total=1 saved=1 -->\n\n"
            "> **甲** · 2024\n> 评论\n\n<!-- /comments -->\n\n---",
        )
        weibo_span = comment_markdown.find_weibo_span(text, "100")
        body = text[weibo_span[0]:weibo_span[1]]
        comment_span = comment_markdown.find_comment_span(body, "100")
        self.assertIsNotNone(comment_span)
        self.assertIn("#### 评论", body[comment_span[0]:comment_span[1]])

    def test_missing_id_returns_none_or_no_block(self):
        text = md_with_blocks("100")
        self.assertIsNone(comment_markdown.find_weibo_span(text, "999"))
        self.assertIsNone(markdown_blocks.parse_markdown(text).find("999"))


class PrimaryCommentGroupingTest(unittest.TestCase):
    """发现的问题已修复：按「一级评论首行」分组 + 按 cid 合并。"""

    def test_root_comments_grouped_per_comment(self):
        block = comment_markdown.render_comment_block(
            "100",
            [make_comment("1", "评论A"), make_comment("2", "评论B", user="乙")],
            total_number=2,
            options=OPTIONS,
        )
        _, groups = markdown_blocks.parse_comments_block(block)
        self.assertEqual(len(groups), 2)
        self.assertEqual([item.cid for item in groups], ["1", "2"])

    def test_merge_does_not_duplicate_when_set_grows(self):
        """旧块 {A} + 新块 {A, B} → A 只出现一次、B 追加一次。"""
        old_block = comment_markdown.render_comment_block(
            "100", [make_comment("1", "评论A")], total_number=1, options=OPTIONS
        )
        new_block = comment_markdown.render_comment_block(
            "100",
            [make_comment("1", "评论A"), make_comment("2", "评论B", user="乙")],
            total_number=2,
            options=OPTIONS,
        )
        old_marker, old_groups = markdown_blocks.parse_comments_block(old_block)
        new_marker, new_groups = markdown_blocks.parse_comments_block(new_block)
        merged = markdown_blocks.merge_comment_groups(old_groups, new_groups)
        rendered = markdown_blocks.render_comments_block(
            new_marker or old_marker, merged
        )
        self.assertEqual(rendered.count("评论A"), 1)
        self.assertEqual(rendered.count("评论B"), 1)
        self.assertEqual(len(merged), 2)

    def test_merge_by_cid_when_comment_text_changes(self):
        """同一 cid 正文更新：按 cid 匹配覆盖旧内容，不重复。"""
        old_block = comment_markdown.render_comment_block(
            "100", [make_comment("1", "旧正文")], total_number=1, options=OPTIONS
        )
        new_block = comment_markdown.render_comment_block(
            "100", [make_comment("1", "新正文")], total_number=1, options=OPTIONS
        )
        old_marker, old_groups = markdown_blocks.parse_comments_block(old_block)
        new_marker, new_groups = markdown_blocks.parse_comments_block(new_block)
        merged = markdown_blocks.merge_comment_groups(old_groups, new_groups)
        rendered = markdown_blocks.render_comments_block(
            new_marker or old_marker, merged
        )
        self.assertIn("新正文", rendered)
        self.assertNotIn("旧正文", rendered)
        self.assertEqual(len(merged), 1)

    def test_legacy_block_without_cid_still_grouped(self):
        """旧块（meta 行无 cid）也按一级评论首行正确分组。"""
        legacy = (
            "#### 评论\n\n<!-- comments: 100 total=2 saved=2 -->\n\n"
            "> **甲** · 2024-01-01 00:00:00\n> 评论A\n"
            ">\n"
            "> **乙** · 2024-01-01 00:00:00\n> 评论B\n\n"
            "<!-- /comments -->"
        )
        _, groups = markdown_blocks.parse_comments_block(legacy)
        self.assertEqual(len(groups), 2)
        self.assertIn("评论A", groups[0].raw)
        self.assertIn("评论B", groups[1].raw)
        self.assertIsNone(groups[0].cid)

    def test_manual_block_without_meta_falls_back_to_blank_split(self):
        """认不出一级评论首行的人工块：仍按空行分组（兼容路径，不丢内容）。"""
        manual = (
            "#### 评论\n\n<!-- comments: 100 total=2 saved=2 -->\n\n"
            "> 人工写的评论一\n\n"
            "> 人工写的评论二\n\n"
            "<!-- /comments -->"
        )
        _, groups = markdown_blocks.parse_comments_block(manual)
        self.assertEqual(len(groups), 2)
        self.assertIn("人工写的评论一", groups[0].raw)
        self.assertIn("人工写的评论二", groups[1].raw)

    def test_single_comment_roundtrip_still_exact(self):
        """单条评论（无组间分隔）仍逐字往返。"""
        block = comment_markdown.render_comment_block(
            "100", [make_comment("1", "评论A")], total_number=1, options=OPTIONS
        )
        marker, groups = markdown_blocks.parse_comments_block(block)
        rebuilt = markdown_blocks.render_comments_block(marker, groups)
        self.assertEqual(rebuilt, block)


class WritePathParityTest(unittest.TestCase):
    """真实写入流水线：``merge_comment_block``（旧块合并）+ ``upsert``（写回）的配合。"""

    def _text_without_comments(self):
        return md_with_blocks("100", "200")

    def test_upsert_places_comment_before_separator(self):
        """架构不变量：评论块必须在 ``---`` 之前（两套渲染共同遵守）。"""
        text = self._text_without_comments()
        block_text = comment_markdown.render_comment_block(
            "100", [make_comment("c1", "评论A")], total_number=1, options=OPTIONS
        )
        updated, changed = comment_markdown.upsert_comment_block(text, "100", block_text)
        self.assertTrue(changed)
        block_start = updated.index("<!-- weibo_id: 100 -->")
        comment_pos = updated.index("#### 评论", block_start)
        separator_pos = updated.index("\n---", block_start)
        self.assertLess(comment_pos, separator_pos)

    def test_merge_then_upsert_roundtrip_keeps_single_block(self):
        """merge（旧块无评论时直出新块）+ upsert 写回 → 文件只有一个评论块。"""
        from util.comment import comment_backfill
        import tempfile

        text = self._text_without_comments()
        new_block = comment_markdown.render_comment_block(
            "100", [make_comment("c1", "评论A")], total_number=1, options=OPTIONS
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "2024-06-01.md")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
            merged = comment_backfill.merge_comment_block(path, "100", new_block)
            self.assertEqual(merged, new_block)
            updated, changed = comment_markdown.upsert_comment_block(text, "100", merged)
            self.assertTrue(changed)
            self.assertEqual(updated.count("#### 评论"), 1)
            self.assertIn("<!-- /comments -->", updated)

    def test_upsert_replaces_existing_comment_block(self):
        text = self._text_without_comments()
        first = comment_markdown.render_comment_block(
            "100", [make_comment("c1", "评论A")], total_number=1, options=OPTIONS
        )
        text_with_comments, _ = comment_markdown.upsert_comment_block(text, "100", first)
        second = comment_markdown.render_comment_block(
            "100", [make_comment("c1", "评论A改")], total_number=1, options=OPTIONS
        )
        updated, changed = comment_markdown.upsert_comment_block(
            text_with_comments, "100", second
        )
        self.assertTrue(changed)
        self.assertEqual(updated.count("#### 评论"), 1)
        self.assertIn("评论A改", updated)
        self.assertNotIn("评论A\n", updated)


if __name__ == "__main__":
    unittest.main()
