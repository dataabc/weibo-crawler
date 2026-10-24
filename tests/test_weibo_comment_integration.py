"""weibo.py 评论写入 Markdown 的集成测试（无网络、临时目录）。

weibo.py 依赖若干第三方库；测试环境未安装时用轻量 stub 顶替。
"""

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path


def _install_import_stubs() -> None:
    if "requests" in sys.modules:
        return

    def add(name: str, **attrs):
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        sys.modules[name] = module
        return module

    add("requests", Session=object, get=lambda *a, **k: None)
    add("requests.exceptions", RequestException=Exception)
    add("requests.adapters", HTTPAdapter=object)
    add("json5", loads=lambda text: {})
    add("tqdm", tqdm=lambda iterable, **kwargs: iterable)
    lxml = add("lxml")
    lxml.etree = add("lxml.etree")
    add("piexif")


_install_import_stubs()

import weibo  # noqa: E402
from util import comment_fetch  # noqa: E402

sys.modules.pop("json5", None)

MD_TEMPLATE = (
    "## 2024-05-03 [测试用户] 微博存档\n\n"
    "### 10:00:00\n<!-- weibo_id: 5001 -->\n正文\n\n---\n"
)


class WriteMarkdownCommentsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.md_path = self.root / "2024-05" / "2024-05-03.md"
        self.md_path.parent.mkdir(parents=True)
        self.md_path.write_text(MD_TEMPLATE, encoding="utf-8")

        self.wb = weibo.Weibo.__new__(weibo.Weibo)
        self.wb.download_comment = 1
        self.wb.write_mode = ["markdown"]
        self.wb.new_written_weibo = {"5001": str(self.md_path)}
        self.wb.comment_max_download_count = 0
        self.wb.comment_reply_max_download_count = 0
        self.wb.comment_request_delay_min = 0
        self.wb.comment_request_delay_max = 0
        self.wb.comment_include_latest_stream = 0
        self.wb.comment_emoji_replace = 1
        self.wb.comment_show_like_count = 0
        self.wb.comment_show_reply_target = 1
        self.wb.markdown_file_dir = {"comment_image_dir": "评论图片"}
        self.wb.comment_pic_download = 0
        self.wb.comment_summary_enabled = 0
        self.wb.comment_summary_dir = "report"
        self.wb.output_directory = str(self.root)
        self.wb.session = object()
        self.wb.headers = {}
        self.wb.user_config = {"user_id": "123"}
        self.wb.user = {"screen_name": "测试用户"}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _install_fake_fetch(self):
        root = comment_fetch.Comment(
            id="9001",
            weibo_id="5001",
            is_root=True,
            user_name="甲",
            created_at="2024-05-03 10:00:00",
            text="一级评论",
        )
        reply = comment_fetch.Comment(
            id="9002",
            weibo_id="5001",
            is_root=False,
            user_name="乙",
            created_at="2024-05-03 10:01:00",
            text="回复正文",
            reply_target="甲",
        )
        root.replies.append(reply)
        result = comment_fetch.FetchResult(
            weibo_id="5001",
            total_number=2,
            roots=[root],
            stats=comment_fetch.FetchStats(
                roots_fetched=1, replies_fetched=1, requests=2, stopped_reason="end"
            ),
        )
        original = comment_fetch.fetch_comments
        comment_fetch.fetch_comments = lambda session, headers, wid, options=None: result
        self.addCleanup(setattr, comment_fetch, "fetch_comments", original)

    def _install_failing_fetch(self):
        original = comment_fetch.fetch_comments

        def boom(*args, **kwargs):
            raise RuntimeError("network down")

        comment_fetch.fetch_comments = boom
        self.addCleanup(setattr, comment_fetch, "fetch_comments", original)

    def test_writes_comment_block(self) -> None:
        self._install_fake_fetch()
        self.wb.write_markdown_comments_for_new_weibo()
        text = self.md_path.read_text(encoding="utf-8")
        self.assertIn("#### 评论", text)
        self.assertIn("<!-- comments: 5001 total=2 saved=2 -->", text)
        self.assertIn("> **甲** · 2024-05-03 10:00:00", text)
        self.assertIn("> 一级评论", text)
        self.assertIn("> > **乙** · 2024-05-03 10:01:00 · 回复 甲", text)
        self.assertIn("> > 回复正文", text)
        # 评论块在 --- 之前
        self.assertLess(text.index("#### 评论"), text.index("\n---\n"))

    def test_disabled_skips(self) -> None:
        self.wb.download_comment = 0
        self.wb.write_markdown_comments_for_new_weibo()
        self.assertNotIn("<!-- comments:", self.md_path.read_text(encoding="utf-8"))

    def test_failure_does_not_raise_and_keeps_file(self) -> None:
        self._install_failing_fetch()
        self.wb.write_markdown_comments_for_new_weibo()
        text = self.md_path.read_text(encoding="utf-8")
        self.assertNotIn("<!-- comments:", text)
        self.assertEqual(text, MD_TEMPLATE)

    def test_user_filter_keeps_only_allowed_root(self) -> None:
        # 只保留评论者「甲」：乙的回复被过滤；甲作为一级评论保留
        self.wb.comment_user_filter = {"123": ["甲"]}
        self._install_fake_fetch()
        self.wb.write_markdown_comments_for_new_weibo()
        text = self.md_path.read_text(encoding="utf-8")
        self.assertIn("> **甲**", text)
        self.assertNotIn("> > **乙**", text)
        self.assertIn("saved=1", text)

    def test_user_filter_keeps_context_root_for_allowed_reply(self) -> None:
        # 只保留评论者「乙」：甲的一级评论作为上下文保留，乙的回复保留
        self.wb.comment_user_filter = {"123": ["乙"]}
        self._install_fake_fetch()
        self.wb.write_markdown_comments_for_new_weibo()
        text = self.md_path.read_text(encoding="utf-8")
        self.assertIn("> **甲**", text)
        self.assertIn("> > **乙**", text)
        self.assertIn("saved=2", text)

    def test_user_filter_without_match_writes_nothing(self) -> None:
        self.wb.comment_user_filter = {"123": ["不存在的人"]}
        self._install_fake_fetch()
        self.wb.write_markdown_comments_for_new_weibo()
        text = self.md_path.read_text(encoding="utf-8")
        self.assertNotIn("<!-- comments:", text)

    def test_user_filter_for_other_account_does_not_apply(self) -> None:
        # 配置的是其他账号，当前账号 123 不应被过滤
        self.wb.comment_user_filter = {"999": ["不存在的人"]}
        self._install_fake_fetch()
        self.wb.write_markdown_comments_for_new_weibo()
        text = self.md_path.read_text(encoding="utf-8")
        self.assertIn("> > **乙**", text)


    def test_user_filter_from_file(self) -> None:
        # 外部 txt 优先于 config 内联配置
        filter_file = self.root / "comment_user_filter.txt"
        filter_file.write_text("123 甲\n", encoding="utf-8")
        self.wb.comment_user_filter_file = str(filter_file)
        self.wb.comment_user_filter = {"123": ["乙"]}
        self._install_fake_fetch()
        self.wb.write_markdown_comments_for_new_weibo()
        text = self.md_path.read_text(encoding="utf-8")
        self.assertIn("> **甲**", text)
        self.assertNotIn("> > **乙**", text)

    def test_user_filter_falls_back_to_inline_when_file_missing(self) -> None:
        # 文件不存在时应回退到 config 内联配置：白名单无匹配 → 不写评论块
        self.wb.comment_user_filter_file = str(self.root / "not-exist-filter.txt")
        self.wb.comment_user_filter = {"123": ["不存在的人"]}
        self._install_fake_fetch()
        self.wb.write_markdown_comments_for_new_weibo()
        text = self.md_path.read_text(encoding="utf-8")
        self.assertNotIn("<!-- comments:", text)

    def test_summary_report_generated(self) -> None:
        self.wb.comment_summary_enabled = 1
        self._install_fake_fetch()
        self.wb.write_markdown_comments_for_new_weibo()
        reports = list((self.root / "report").glob("weibo_comment_summary_*.txt"))
        self.assertEqual(len(reports), 1)
        content = reports[0].read_text(encoding="utf-8")
        self.assertIn("评论下载情况报告", content)
        self.assertIn("已存档评论: 1 条一级 + 1 条二级回复", content)
        self.assertIn("已写入评论块的微博数: 1", content)


if __name__ == "__main__":
    unittest.main()
