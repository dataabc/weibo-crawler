"""块级 Markdown 解析器单元测试。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from util.core import markdown_blocks  # noqa: E402

SAMPLE = """## 2026-10-25 [示例用户] 微博存档

### 21:05:40

<!-- weibo_id: 5000000000000004 -->
<!-- edited_at: 2026-10-24 18:20:05 -->

今晚的月亮很好看。

![img](原创微博图片/2026-10-25_21-05-40_1.jpg)

#### 评论

<!-- comments: 5000000000000004 total=2 saved=2 updated_at=2026-10-25T21:30:00 -->

> **评论者A** · 2026-10-25 21:10:00 <!-- cid: 6000000000000010 -->
> 好圆的月亮。

> **评论者B** · 2026-10-25 21:12:00 <!-- cid: 6000000000000011 -->
> 拍得真好。

<!-- /comments -->

---

### 08:00:00

<!-- weibo_id: 5000000000000003 -->

转发了一条微博：

> 转发: 被转发的原始正文内容。
>
> ![img](转发微博图片/2026-10-25_08-00-00_1.jpg)

---

### 07:30:00

<!-- weibo_id: 5000000000000005 -->

旧版编辑记录块：

#### 编辑记录

<!-- edits: 5000000000000005 -->

- 2026-10-01 12:00:00 · 编辑

---
"""


class ParseTest(unittest.TestCase):
    def test_parse_blocks(self):
        doc = markdown_blocks.parse_markdown(SAMPLE, fallback_date="2026-10-25")
        self.assertEqual(len(doc.blocks), 3)
        first = doc.blocks[0]
        self.assertEqual(first.weibo_id, "5000000000000004")
        self.assertEqual(first.edited_at, "2026-10-24 18:20:05")
        self.assertEqual(first.created_at, "2026-10-25T21:05:40")
        self.assertEqual(len(first.comments), 2)
        self.assertEqual(first.comments[0].cid, "6000000000000010")
        self.assertEqual(first.comments[1].cid, "6000000000000011")

        second = doc.blocks[1]
        self.assertEqual(second.weibo_id, "5000000000000003")
        self.assertIsNone(second.edited_at)
        self.assertFalse(second.comments)

        third = doc.blocks[2]
        self.assertIn("#### 编辑记录", third.body)

    def test_render_idempotent(self):
        doc = markdown_blocks.parse_markdown(SAMPLE, fallback_date="2026-10-25")
        self.assertEqual(doc.render(), SAMPLE)

    def test_full_heading(self):
        text = "## 标题\n\n### 2024-05-03 10:00:00\n\n<!-- weibo_id: 1 -->\n\n正文\n\n---\n"
        doc = markdown_blocks.parse_markdown(text)
        self.assertEqual(doc.blocks[0].created_at, "2024-05-03T10:00:00")

    def test_no_heading(self):
        doc = markdown_blocks.parse_markdown("没有微博块的文件\n")
        self.assertEqual(doc.blocks, [])
        self.assertEqual(doc.render(), "没有微博块的文件\n")

    def test_unparsed_block_preserved(self):
        text = (
            "## 标题\n\n"
            "### 10:00:00\n\n<!-- weibo_id: 1 -->\n\n正文\n\n---\n\n"
            "### 09:00:00\n\n人工添加的段落，没有微博 ID。\n\n---\n"
        )
        doc = markdown_blocks.parse_markdown(text, fallback_date="2024-05-03")
        self.assertEqual(len(doc.blocks), 2)
        self.assertEqual(doc.blocks[1].weibo_id, None)
        self.assertFalse(doc.blocks[1].sortable)
        self.assertEqual(doc.render(), text)


class SortInsertTest(unittest.TestCase):
    def _text(self, times):
        parts = ["## 2024-05-03 [用户] 微博存档\n\n"]
        for i, hhmmss in enumerate(times):
            parts.append(
                f"### {hhmmss}\n\n<!-- weibo_id: {100 + i} -->\n\n正文{i}\n\n---\n\n"
            )
        return "".join(parts)

    def test_sort_old_to_new(self):
        text = self._text(["12:00:00", "08:00:00", "10:00:00"])
        doc = markdown_blocks.parse_markdown(text, fallback_date="2024-05-03")
        markdown_blocks.sort_blocks(doc, "old_to_new")
        got = [b.heading for b in doc.blocks]
        self.assertEqual(
            got, ["### 08:00:00", "### 10:00:00", "### 12:00:00"]
        )

    def test_sort_new_to_old(self):
        text = self._text(["12:00:00", "08:00:00", "10:00:00"])
        doc = markdown_blocks.parse_markdown(text, fallback_date="2024-05-03")
        markdown_blocks.sort_blocks(doc, "new_to_old")
        got = [b.heading for b in doc.blocks]
        self.assertEqual(
            got, ["### 12:00:00", "### 10:00:00", "### 08:00:00"]
        )

    def test_sort_keeps_unparsed_in_place(self):
        text = (
            "## 标题\n\n"
            "### 10:00:00\n\n<!-- weibo_id: 1 -->\n\n正文\n\n---\n\n"
            "### 09:00:00\n\n人工段落\n\n---\n\n"
            "### 08:00:00\n\n<!-- weibo_id: 2 -->\n\n正文\n\n---\n"
        )
        doc = markdown_blocks.parse_markdown(text, fallback_date="2024-05-03")
        markdown_blocks.sort_blocks(doc, "old_to_new")
        # 不可排序块保持索引 1；可排序块按时间回填索引 0/2
        self.assertEqual(doc.blocks[0].weibo_id, "2")
        self.assertIsNone(doc.blocks[1].weibo_id)
        self.assertEqual(doc.blocks[2].weibo_id, "1")

    def test_insert_block_in_position(self):
        text = self._text(["08:00:00", "12:00:00"])
        doc = markdown_blocks.parse_markdown(text, fallback_date="2024-05-03")
        new_raw = "### 10:00:00\n\n<!-- weibo_id: 999 -->\n\n新微博\n\n---\n\n"
        block = markdown_blocks._parse_block(new_raw, "2024-05-03")
        markdown_blocks.insert_block(doc, block, "old_to_new")
        self.assertEqual(
            [b.heading for b in doc.blocks],
            ["### 08:00:00", "### 10:00:00", "### 12:00:00"],
        )


class CommentMergeTest(unittest.TestCase):
    def test_merge_by_cid(self):
        existing = [
            markdown_blocks.CommentItem(cid="1", raw="> **A** · t1\n> 旧正文"),
            markdown_blocks.CommentItem(cid="2", raw="> **B** · t2\n> 保留"),
        ]
        incoming = [
            markdown_blocks.CommentItem(cid="2", raw="> **B** · t2\n> 更新正文"),
            markdown_blocks.CommentItem(cid="3", raw="> **C** · t3\n> 新评论"),
        ]
        merged = markdown_blocks.merge_comment_groups(existing, incoming)
        self.assertEqual([c.cid for c in merged], ["1", "2", "3"])
        self.assertIn("更新正文", merged[1].raw)
        self.assertIn("新评论", merged[2].raw)

    def test_render_comments_block(self):
        groups = [
            markdown_blocks.CommentItem(cid="1", raw="> **A** · t1\n> 正文"),
            markdown_blocks.CommentItem(cid="2", raw="> **B** · t2\n> 正文2"),
        ]
        text = markdown_blocks.render_comments_block(
            "<!-- comments: 500 total=2 saved=2 -->", groups
        )
        self.assertIn("#### 评论", text)
        self.assertIn("<!-- /comments -->", text)
        self.assertIn("> **B**", text)



    def test_merge_upgrades_cidless_group_without_duplicate(self):
        """旧块无 cid、新块带 cid：同一条评论应替换而不是追加（防重复）。"""
        existing = [
            markdown_blocks.CommentItem(cid=None, raw="> **A** · t1\n> 正文")
        ]
        incoming = [
            markdown_blocks.CommentItem(
                cid="1", raw="> **A** · t1 <!-- cid: 1 -->\n> 正文"
            )
        ]
        merged = markdown_blocks.merge_comment_groups(existing, incoming)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].cid, "1")

    def test_merge_dedupes_existing_duplicates(self):
        ""
        existing = [
            markdown_blocks.CommentItem(cid=None, raw="> **A** · t1\n> 正文"),
            markdown_blocks.CommentItem(
                cid="1", raw="> **A** · t1 <!-- cid: 1 -->\n> 正文"
            ),
        ]
        merged = markdown_blocks.merge_comment_groups(existing, [])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].cid, "1")

class EditedAtTest(unittest.TestCase):
    def test_rebuild_block_text_roundtrip(self):
        doc = markdown_blocks.parse_markdown(SAMPLE, fallback_date="2026-10-25")
        block = doc.blocks[0]
        rebuilt = markdown_blocks.rebuild_block_text(block)
        # 重建后的块文本再解析一次，内容应一致
        doc2 = markdown_blocks.parse_markdown(
            "## 标题\n\n" + rebuilt, fallback_date="2026-10-25"
        )
        self.assertEqual(doc2.blocks[0].weibo_id, block.weibo_id)
        self.assertEqual(
            [c.cid for c in doc2.blocks[0].comments],
            ["6000000000000010", "6000000000000011"],
        )


if __name__ == "__main__":
    unittest.main()
