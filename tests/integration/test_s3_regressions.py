"""覆盖：
- ：微博块边界只认时间标题，正文里行首 ``### `` 不再把块截断；
- ：``update_markdown_file_status`` 三态，块未找到不再被当成「未变化」；
- ：回填断点记录配置指纹，配置变化可被发现。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path


from tests import _support  # noqa: E402

_support.bootstrap()

from util.comment import comment_markdown  # noqa: E402

_SCRIPTS_DIR = str(Path(__file__).resolve().parents[2] / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
import weibo_comment_backfill as backfill  # noqa: E402

sys.modules.pop("json5", None)

BLOCK = (
    "#### 评论\n"
    "<!-- comments: 101 total=1 saved=1 -->\n"
    "> **甲** · 10:00:00\n"
    "> 评论内容\n"
    "<!-- /comments -->\n"
)


class HeadingBoundaryTest(unittest.TestCase):
    """正文里以 ### 开头的行不应截断微博块。"""

    def test_hash_line_in_body_does_not_truncate_block(self) -> None:
        md = (
            "## 2024-05-03 [用户A] 微博存档\n\n"
            "### 10:00:00\n"
            "<!-- weibo_id: 101 -->\n"
            "第一行正文\n"
            "### 这是正文里的井号\n"
            "第二行正文\n\n"
            "---\n"
        )
        updated, changed = comment_markdown.upsert_comment_block(md, "101", BLOCK)
        self.assertTrue(changed)
        # 评论块必须插在 --- 分隔线之前、且正文两行都还在
        self.assertIn("第一行正文", updated)
        self.assertIn("### 这是正文里的井号", updated)
        self.assertIn("第二行正文", updated)
        block_pos = updated.index("#### 评论")
        self.assertLess(updated.index("第二行正文"), block_pos)
        self.assertLess(block_pos, updated.rindex("---"))

    def test_real_next_heading_still_bounds_block(self) -> None:
        """真正的下一条微博标题仍然要作为块边界。"""
        md = (
            "### 10:00:00\n<!-- weibo_id: 101 -->\n微博A正文\n\n---\n\n"
            "### 11:00:00\n<!-- weibo_id: 102 -->\n微博B正文\n\n---\n"
        )
        updated, _ = comment_markdown.upsert_comment_block(md, "101", BLOCK)
        block_pos = updated.index("#### 评论")
        self.assertLess(block_pos, updated.index("微博B正文"))
        self.assertLess(block_pos, updated.index("### 11:00:00"))

    def test_time_heading_like_body_line_does_not_truncate_block(self) -> None:
        """正文行「### 12:00:00 备注」不应被当作块边界截断微博块。"""
        md = (
            "### 10:00:00\n"
            "<!-- weibo_id: 101 -->\n"
            "第一行正文\n"
            "### 12:00:00 备注\n"
            "第二行正文\n\n"
            "---\n"
        )
        updated, changed = comment_markdown.upsert_comment_block(md, "101", BLOCK)
        self.assertTrue(changed)
        block_pos = updated.index("#### 评论")
        self.assertLess(updated.index("第二行正文"), block_pos)
        self.assertLess(block_pos, updated.rindex("---"))


class UpdateStatusTest(unittest.TestCase):
    """写入状态三态。"""

    def _write_md(self, tmp: str, content: str) -> str:
        path = Path(tmp) / "2024-05-03.md"
        path.write_text(content, encoding="utf-8")
        return str(path)

    def test_missing_block_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_md(
                tmp,
                "### 10:00:00\n<!-- weibo_id: 999 -->\n别的微博\n\n---\n",
            )
            changed, status = comment_markdown.update_markdown_file_status(
                path, "101", BLOCK, backup=False
            )
            self.assertFalse(changed)
            self.assertEqual(status, comment_markdown.UPDATE_MISSING)

    def test_written_block_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_md(
                tmp, "### 10:00:00\n<!-- weibo_id: 101 -->\n正文\n\n---\n"
            )
            changed, status = comment_markdown.update_markdown_file_status(
                path, "101", BLOCK, backup=False
            )
            self.assertTrue(changed)
            self.assertEqual(status, comment_markdown.UPDATE_WRITTEN)
            self.assertIn("#### 评论", Path(path).read_text(encoding="utf-8"))

    def test_unchanged_block_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_md(
                tmp, "### 10:00:00\n<!-- weibo_id: 101 -->\n正文\n\n---\n"
            )
            comment_markdown.update_markdown_file_status(
                path, "101", BLOCK, backup=False
            )
            changed, status = comment_markdown.update_markdown_file_status(
                path, "101", BLOCK, backup=False
            )
            self.assertFalse(changed)
            self.assertEqual(status, comment_markdown.UPDATE_UNCHANGED)


class ConfigFingerprintTest(unittest.TestCase):
    """断点记录配置指纹，配置变化可被发现。"""

    def test_fingerprint_differs_when_limits_change(self) -> None:
        base = {"comment_max_download_count": 100}
        changed = {"comment_max_download_count": 200}
        self.assertNotEqual(
            backfill._config_fingerprint(base),
            backfill._config_fingerprint(changed),
        )
        self.assertEqual(
            backfill._config_fingerprint(base),
            backfill._config_fingerprint({"comment_max_download_count": 100}),
        )

    def test_save_progress_stores_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "progress.json")
            fingerprint = backfill._config_fingerprint({"comment_max_download_count": 0})
            backfill.save_progress(path, {"101"}, {"written": 1}, fingerprint)
            data = backfill.load_progress(path)
            self.assertEqual(data["config"], fingerprint)

    def test_save_progress_without_fingerprint_keeps_compat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "progress.json")
            backfill.save_progress(path, {"101"}, {"written": 1})
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            self.assertNotIn("config", data)


if __name__ == "__main__":
    unittest.main()
