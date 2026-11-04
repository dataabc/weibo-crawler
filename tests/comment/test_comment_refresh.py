"""评论刷新与合并 单元测试。"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


from tests import _support  # noqa: E402

_support.bootstrap()

import weibo  # noqa: E402
from util.comment import comment_fetch  # noqa: E402
from util.comment import comment_markdown  # noqa: E402

sys.modules.pop("json5", None)

MD = """## 2024-05-03 [测试用户] 微博存档

### 10:00:00

<!-- weibo_id: 100 -->

正文

#### 评论

<!-- comments: 100 total=2 saved=2 -->

> **评论者A** · 2024-05-03 10:01:00 <!-- cid: 1 -->
> 旧评论一

> **评论者B** · 2024-05-03 10:02:00 <!-- cid: 2 -->
> 旧评论二

<!-- /comments -->

---
"""

NEW_BLOCK = """#### 评论

<!-- comments: 100 total=3 saved=3 -->

> **评论者B** · 2024-05-03 10:02:00 <!-- cid: 2 -->
> 旧评论二（更新）

> **评论者C** · 2024-05-03 10:03:00 <!-- cid: 3 -->
> 新评论

<!-- /comments -->"""


def _make_wb():
    return weibo.Weibo.__new__(weibo.Weibo)


class MergeCommentBlockTest(unittest.TestCase):
    def test_merge_keeps_old_and_adds_new(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "2024-05-03.md")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(MD)
            wb = _make_wb()
            merged = wb._merge_comment_block(path, "100", NEW_BLOCK)
            self.assertIn("<!-- cid: 1 -->", merged)
            self.assertIn("旧评论一", merged)
            self.assertIn("<!-- cid: 2 -->", merged)
            self.assertIn("旧评论二（更新）", merged)
            self.assertNotIn("> 旧评论二\n", merged)
            self.assertIn("<!-- cid: 3 -->", merged)
            self.assertIn("新评论", merged)

    def test_merge_without_old_comments_returns_new_block(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "2024-05-03.md")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(
                    "## 标题\n\n### 10:00:00\n\n<!-- weibo_id: 100 -->\n\n正文\n\n---\n"
                )
            wb = _make_wb()
            merged = wb._merge_comment_block(path, "100", NEW_BLOCK)
            self.assertIn("新评论", merged)

    def test_merge_missing_file_returns_new_block(self):
        wb = _make_wb()
        merged = wb._merge_comment_block("/nonexistent/x.md", "100", NEW_BLOCK)
        self.assertEqual(merged, NEW_BLOCK)


class RenderCidTest(unittest.TestCase):
    def test_render_comment_block_contains_cid(self):
        comment = comment_fetch.Comment(
            id="6000000000000001",
            weibo_id="100",
            is_root=True,
            user_name="评论者A",
            created_at="2024-05-03 10:01:00",
            text="好评论",
        )
        block = comment_markdown.render_comment_block(
            "100", [comment], total_number=1, options=comment_markdown.RenderOptions()
        )
        self.assertIn("<!-- cid: 6000000000000001 -->", block)


class RefreshScopeTest(unittest.TestCase):
    def _build_wb(self, md_path, state):
        wb = _make_wb()
        wb.download_comment = 1
        wb.write_mode = ["markdown"]
        wb.new_written_weibo = {}
        wb.weibo = [{"id": 100, "created_at": "2024-05-03T10:00:00"}]
        wb.comment_refresh_scope = "all"
        wb.comment_refresh_recent_days = 30
        wb.comment_refresh_limit = 300
        wb.comment_pic_download = 0
        wb.comment_summary_enabled = 0
        wb.markdown_file_dir = {"comment_image_dir": "评论图片"}
        wb.session = object()
        wb.headers = {}
        wb._load_comment_retry = lambda: {}
        wb._comment_allowed_users = lambda: []
        wb._save_comment_retry = lambda mapping: None
        wb._atomic_write_text = lambda path, text: open(
            path, "w", encoding="utf-8"
        ).write(text)
        wb._ensure_archive_state = lambda: state
        wb._state_path = ""
        wb._locate_block_file = lambda wid, item, created_at: (md_path, "rel.md")
        wb._merge_comment_block = (
            lambda md, wid, block, original_text=None: block
        )
        return wb

    def test_archived_weibo_included_in_refresh(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = os.path.join(tmpdir, "2024-05-03.md")
            with open(md_path, "w", encoding="utf-8") as handle:
                handle.write(MD)
            state = {
                "weibos": {"100": {"comment_refreshed_at": "", "visibility": {}}}
            }
            wb = self._build_wb(md_path, state)
            root = comment_fetch.Comment(
                id="1",
                weibo_id="100",
                is_root=True,
                user_name="评论者A",
                created_at="2024-05-03 10:01:00",
                text="旧评论一",
            )
            fetch_result = comment_fetch.FetchResult(
                weibo_id="100",
                total_number=1,
                roots=[root],
                stats=comment_fetch.FetchStats(),
            )
            with mock.patch(
                "weibo.comment_fetch.fetch_comments", return_value=fetch_result
            ) as fetch_mock, mock.patch("weibo.logger"):
                wb.write_markdown_comments_for_new_weibo()
            self.assertEqual(fetch_mock.call_count, 1)
            with open(md_path, encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn("<!-- cid: 1 -->", text)
            # 状态记录刷新时间
            self.assertTrue(state["weibos"]["100"]["comment_refreshed_at"])


    def test_no_comment_marks_refreshed(self):
        """无评论的微博也记录 comment_refreshed_at，避免每轮重复请求。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = os.path.join(tmpdir, "2024-05-03.md")
            with open(md_path, "w", encoding="utf-8") as handle:
                handle.write(MD)
            state = {"weibos": {"100": {"comment_refreshed_at": ""}}}
            wb = self._build_wb(md_path, state)
            empty_result = comment_fetch.FetchResult(
                weibo_id="100",
                total_number=0,
                roots=[],
                stats=comment_fetch.FetchStats(),
            )
            with mock.patch(
                "weibo.comment_fetch.fetch_comments", return_value=empty_result
            ), mock.patch("weibo.logger"):
                wb.write_markdown_comments_for_new_weibo()
            self.assertTrue(state["weibos"]["100"]["comment_refreshed_at"])

    def test_limit_caps_archived_refresh(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = os.path.join(tmpdir, "2024-05-03.md")
            with open(md_path, "w", encoding="utf-8") as handle:
                handle.write(MD)
            state = {"weibos": {}}
            wb = self._build_wb(md_path, state)
            wb.weibo = [
                {"id": 100, "created_at": "2024-05-03T10:00:00"},
                {"id": 101, "created_at": "2024-05-03T11:00:00"},
                {"id": 102, "created_at": "2024-05-03T12:00:00"},
            ]
            wb.comment_refresh_limit = 1
            wb._locate_block_file = lambda wid, item, created_at: (md_path, "rel.md")
            fetch_result = comment_fetch.FetchResult(
                weibo_id="100",
                total_number=0,
                roots=[],
                stats=comment_fetch.FetchStats(),
            )
            with mock.patch(
                "weibo.comment_fetch.fetch_comments", return_value=fetch_result
            ) as fetch_mock, mock.patch("weibo.logger"):
                wb.write_markdown_comments_for_new_weibo()
            self.assertEqual(fetch_mock.call_count, 1)


if __name__ == "__main__":
    unittest.main()
