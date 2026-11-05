"""编辑检测、旧版快照与 md 正文更新 单元测试。"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


from tests import _support  # noqa: E402

_support.bootstrap()

import weibo  # noqa: E402
from util.core import archive_state  # noqa: E402

sys.modules.pop("json5", None)

MD = """## 2024-05-03 [测试用户] 微博存档

### 10:00:00

<!-- weibo_id: 100 -->

旧正文

---

### 11:00:00

<!-- weibo_id: 200 -->

另一条微博，不应被改动

---
"""

OLD_EDIT_AT = "2024-06-01 00:00:00"
NEW_EDIT_AT = "2024-06-02 00:00:00"


def _old_fingerprint():
    return archive_state.content_fingerprint(
        text="旧正文",
        pics="",
        video_url="",
        live_photo_url="",
        article_url="",
        topics="",
        edit_at=OLD_EDIT_AT,
        visible="{}",
    )


def _new_weibo(wid=100):
    return {
        "id": wid,
        "created_at": "2024-05-03T10:00:00",
        "text": "新正文",
        "pics": "",
        "video_url": "",
        "live_photo_url": "",
        "article_url": "",
        "topics": "",
        "links": [],
        "edit_at": NEW_EDIT_AT,
        "visible": {},
    }


def _make_wb(tmpdir, weibo_list, state=None):
    wb = weibo.Weibo.__new__(weibo.Weibo)
    wb.user = {"screen_name": "测试用户"}
    wb.user_config = {"user_id": "1234567890"}
    wb.markdown_split_by = "day_by_month"
    wb.markdown_sort_order = "old_to_new"
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
    wb.invisible_weibo_dirs = ()
    wb.archive_state_path = ""
    wb.edit_snapshot_dir = "编辑历史"
    wb.markdown_write_backup = 1
    wb.new_written_weibo = {}
    wb.run_failures = []
    wb.edit_changes = []
    wb.weibo = weibo_list
    wb.get_filepath = lambda _type: tmpdir
    if state is not None:
        with open(
            os.path.join(tmpdir, "archive_state_1234567890.json"),
            "w",
            encoding="utf-8",
        ) as handle:
            json.dump(state, handle, ensure_ascii=False)
    return wb


def _base_state():
    return {
        "version": 1,
        "user_id": "1234567890",
        "updated_at": "",
        "weibos": {
            "100": {
                "created_at": "2024-05-03T10:00:00",
                "md_path": "2024-05/2024-05-03.md",
                "first_seen": "",
                "last_seen": "",
                "last_edit_at": OLD_EDIT_AT,
                "content_hash": _old_fingerprint(),
                "visibility": {},
                "status": "active",
                "missing_rounds": 0,
                "versions": [],
            }
        },
    }


class EditUpdateTest(unittest.TestCase):
    def _tree(self, tmpdir):
        month_dir = os.path.join(tmpdir, "2024-05")
        os.makedirs(month_dir)
        md_path = os.path.join(month_dir, "2024-05-03.md")
        with open(md_path, "w", encoding="utf-8") as handle:
            handle.write(MD)
        return md_path

    def test_edit_detected_updates_md_and_saves_snapshot(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = self._tree(tmpdir)
            wb = _make_wb(tmpdir, [_new_weibo()], state=_base_state())
            with mock.patch("weibo.logger"):
                wb._apply_edit_updates(0)
            self.assertEqual(wb.run_failures, [])
            with open(md_path, encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn("新正文", text)
            self.assertNotIn("旧正文", text)
            self.assertIn(f"<!-- edited_at: {NEW_EDIT_AT} -->", text)
            # 另一条微博不受影响
            self.assertIn("另一条微博，不应被改动", text)
            # 快照文件存在，且内容是旧版正文
            snapshot = os.path.join(
                tmpdir, "编辑历史", "2024-05", "2024-05-03_100.md"
            )
            self.assertTrue(os.path.isfile(snapshot))
            with open(snapshot, encoding="utf-8") as handle:
                snapshot_text = handle.read()
            self.assertIn("旧正文", snapshot_text)
            self.assertIn("版本快照", snapshot_text)
            # 状态索引更新
            with open(
                os.path.join(tmpdir, "archive_state_1234567890.json"),
                encoding="utf-8",
            ) as handle:
                state = json.load(handle)
            item = state["weibos"]["100"]
            self.assertEqual(len(item["versions"]), 1)
            self.assertEqual(item["last_edit_at"], NEW_EDIT_AT)
            self.assertNotEqual(item["content_hash"], _old_fingerprint())
            # edit_changes 记录
            self.assertEqual(len(wb.edit_changes), 1)
            self.assertEqual(wb.edit_changes[0]["weibo_id"], "100")

    def test_second_run_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._tree(tmpdir)
            wb = _make_wb(tmpdir, [_new_weibo()], state=_base_state())
            with mock.patch("weibo.logger"):
                wb._apply_edit_updates(0)
            with open(
                os.path.join(tmpdir, "2024-05", "2024-05-03.md"),
                encoding="utf-8",
            ) as handle:
                first = handle.read()
            with mock.patch("weibo.logger"):
                wb._apply_edit_updates(0)
            with open(
                os.path.join(tmpdir, "2024-05", "2024-05-03.md"),
                encoding="utf-8",
            ) as handle:
                second = handle.read()
            self.assertEqual(first, second)
            snapshots = []
            for root, _dirs, files in os.walk(os.path.join(tmpdir, "编辑历史")):
                for name in files:
                    if name.endswith(".md"):
                        snapshots.append(os.path.join(root, name))
            self.assertEqual(len(snapshots), 1)

    def test_new_weibo_only_registered(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._tree(tmpdir)
            weibo_list = [
                {
                    "id": 300,
                    "created_at": "2024-05-03T12:00:00",
                    "text": "全新微博",
                    "pics": "",
                    "video_url": "",
                    "live_photo_url": "",
                    "article_url": "",
                    "topics": "",
                    "links": [],
                    "edit_at": "",
                    "visible": {},
                }
            ]
            wb = _make_wb(tmpdir, weibo_list)
            with mock.patch("weibo.logger"):
                wb._apply_edit_updates(0)
            with open(
                os.path.join(tmpdir, "archive_state_1234567890.json"),
                encoding="utf-8",
            ) as handle:
                state = json.load(handle)
            self.assertIn("300", state["weibos"])
            # 不应产生快照
            self.assertFalse(os.path.isdir(os.path.join(tmpdir, "编辑历史")))



    def test_render_format_diff_not_edit(self):
        """旧 md 是普通链接、新渲染开启预览：语义内容相同，不应判定编辑。"""
        md = """## 2024-05-03 [测试用户] 微博存档

### 10:00:00

<!-- weibo_id: 100 -->

正文

[网页链接](https://example.com/a)

---
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            month_dir = os.path.join(tmpdir, "2024-05")
            os.makedirs(month_dir)
            md_path = os.path.join(month_dir, "2024-05-03.md")
            with open(md_path, "w", encoding="utf-8") as handle:
                handle.write(md)
            w = _new_weibo()
            w["text"] = "正文"
            w["links"] = ["https://example.com/a"]
            wb = _make_wb(tmpdir, [w])
            wb.markdown_preview = {"link_preview": 1}
            with mock.patch("weibo.logger"):
                wb._apply_edit_updates(0)
            with open(md_path, encoding="utf-8") as handle:
                text = handle.read()
            self.assertEqual(text, md)  # 未被改写
            self.assertFalse(os.path.isdir(os.path.join(tmpdir, "编辑历史")))
            self.assertEqual(wb.edit_changes, [])



    def test_edit_at_fetched_from_detail(self):
        """列表接口没有 edit_at 时，命中编辑后补查详情接口并写入平台时间。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = self._tree(tmpdir)
            w = _new_weibo()
            w["edit_at"] = ""
            wb = _make_wb(tmpdir, [w], state=_base_state())
            wb._fetch_edit_at = lambda _wid: "2026-10-20 14:30:12"
            with mock.patch("weibo.logger"):
                wb._apply_edit_updates(0)
            with open(md_path, encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn("<!-- edited_at: 2026-10-20 14:30:12 -->", text)

    def test_edit_at_detail_unavailable_marked(self):
        """详情接口拿不到时，md 对应位置标注接口变动，而不是假装一个时间。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = self._tree(tmpdir)
            w = _new_weibo()
            w["edit_at"] = ""
            wb = _make_wb(tmpdir, [w], state=_base_state())
            wb._fetch_edit_at = lambda _wid: None
            with mock.patch("weibo.logger"):
                wb._apply_edit_updates(0)
            with open(md_path, encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn("平台未提供（详情接口变动）", text)

    def test_snapshot_rewrites_media_links(self):
        ''
        md = """## 2024-05-12 [测试用户] 微博存档

### 10:00:00

<!-- weibo_id: 100 -->

旧正文

![img](img/2024-05-12_10-00-00_1.jpg)

---
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            month_dir = os.path.join(tmpdir, "2024-05")
            os.makedirs(month_dir)
            md_path = os.path.join(month_dir, "2024-05-12.md")
            with open(md_path, "w", encoding="utf-8") as handle:
                handle.write(md)
            state = {
                "weibos": {
                    "100": {
                        "created_at": "2024-05-12T10:00:00",
                        "md_path": "2024-05/2024-05-12.md",
                        "last_edit_at": OLD_EDIT_AT,
                        "content_hash": "old-fingerprint",
                        "visibility": {},
                        "status": "active",
                        "missing_rounds": 0,
                        "versions": [],
                    }
                }
            }
            w = _new_weibo()
            w["text"] = "新正文"
            wb = _make_wb(tmpdir, [w], state=state)
            with mock.patch("weibo.logger"):
                wb._apply_edit_updates(0)
            snapshots = []
            for root, _dirs, files in os.walk(os.path.join(tmpdir, "编辑历史")):
                for name in files:
                    if name.endswith(".md"):
                        snapshots.append(os.path.join(root, name))
            self.assertEqual(len(snapshots), 1)
            with open(snapshots[0], encoding="utf-8") as handle:
                snapshot_text = handle.read()
            self.assertIn(
                "../../2024-05/img/2024-05-12_10-00-00_1.jpg",
                snapshot_text,
            )

    def test_first_seen_compares_md_body_and_updates(self):
        """状态索引不存在（首次全量）：md 当前正文当旧版本，内容不同即检测为编辑。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = self._tree(tmpdir)
            wb = _make_wb(tmpdir, [_new_weibo()])
            with mock.patch("weibo.logger"):
                wb._apply_edit_updates(0)
            with open(md_path, encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn("新正文", text)
            self.assertNotIn("旧正文", text)
            self.assertIn(f"<!-- edited_at: {NEW_EDIT_AT} -->", text)
            snapshots = []
            for root, _dirs, files in os.walk(os.path.join(tmpdir, "编辑历史")):
                for name in files:
                    if name.endswith(".md"):
                        snapshots.append(os.path.join(root, name))
            self.assertEqual(len(snapshots), 1)
            with open(snapshots[0], encoding="utf-8") as handle:
                self.assertIn("旧正文", handle.read())
            self.assertEqual(len(wb.edit_changes), 1)
            self.assertEqual(wb.edit_changes[0]["weibo_id"], "100")
            self.assertEqual(wb.edit_changes[0]["old_edit_at"], "")
            with open(
                os.path.join(tmpdir, "archive_state_1234567890.json"),
                encoding="utf-8",
            ) as handle:
                state = json.load(handle)
            self.assertTrue(state["weibos"]["100"]["content_hash"])

    def test_first_seen_same_body_no_edit(self):
        """首次见到但 md 正文与最新内容一致：只登记，不产生快照。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = self._tree(tmpdir)
            same = {
                "id": 100,
                "created_at": "2024-05-03T10:00:00",
                "text": "旧正文",
                "pics": "",
                "video_url": "",
                "live_photo_url": "",
                "article_url": "",
                "topics": "",
                "links": [],
                "edit_at": "",
                "visible": {},
            }
            wb = _make_wb(tmpdir, [same])
            with mock.patch("weibo.logger"):
                wb._apply_edit_updates(0)
            with open(md_path, encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn("旧正文", text)
            self.assertFalse(os.path.isdir(os.path.join(tmpdir, "编辑历史")))
            self.assertEqual(wb.edit_changes, [])
            # 没有编辑时不应空写回（不生成 .bak）
            self.assertEqual(
                [
                    name
                    for name in os.listdir(os.path.dirname(md_path))
                    if ".bak." in name
                ],
                [],
            )

    def test_missing_block_records_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb = _make_wb(tmpdir, [_new_weibo()], state=_base_state())
            with mock.patch("weibo.logger"):
                wb._apply_edit_updates(0)
            self.assertTrue(wb.run_failures)
            self.assertIn("找不到对应 md 文件", wb.run_failures[0]["error"])



    def test_signature_handles_quote_blocks(self):
        """转发引用块里的媒体/链接格式差异不应影响语义签名。"""
        wb = _make_wb("/tmp", [])
        a = wb._content_signature(
            "> 转发正文\n> [网页链接](https://example.com/a)\n> ![img](转发微博图片/x.jpg)"
        )
        b = wb._content_signature(
            "> 转发正文\n> ![网页链接](https://example.com/a)\n> [img](转发微博图片/x.jpg)"
        )
        self.assertEqual(a, b)

    def test_fingerprint_drift_but_same_content_no_rewrite(self):
        """指纹变化只来自易变字段：二次确认正文/媒体/链接一致 → 不重写、不备份。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = self._tree(tmpdir)
            state = _base_state()
            state["weibos"]["100"]["content_hash"] = "stale-fingerprint"
            w = _new_weibo()
            w["text"] = "旧正文"
            wb = _make_wb(tmpdir, [w], state=state)
            with mock.patch("weibo.logger"):
                wb._apply_edit_updates(0)
            with open(md_path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), MD)
            self.assertEqual(wb.edit_changes, [])
            self.assertFalse(os.path.isdir(os.path.join(tmpdir, "编辑历史")))
            self.assertEqual(
                [
                    name
                    for name in os.listdir(os.path.join(tmpdir, "2024-05"))
                    if ".bak." in name
                ],
                [],
            )
            with open(
                os.path.join(tmpdir, "archive_state_1234567890.json"),
                encoding="utf-8",
            ) as handle:
                state_after = json.load(handle)
            self.assertNotEqual(
                state_after["weibos"]["100"]["content_hash"], "stale-fingerprint"
            )

    def test_long_text_failed_keeps_archive(self):
        """长文取全文失败：不拿截断正文覆盖旧存档，并记录到长文失败清单。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = self._tree(tmpdir)
            w = _new_weibo()
            w["text"] = "只有截断的前半段…全文"
            w["long_text_failed"] = True
            wb = _make_wb(tmpdir, [w], state=_base_state())
            with mock.patch("weibo.logger"):
                wb._apply_edit_updates(0)
            with open(md_path, encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn("旧正文", text)
            self.assertNotIn("截断", text)
            self.assertNotIn("edited_at", text)
            self.assertEqual(wb.edit_changes, [])
            self.assertEqual(len(wb.long_text_failures), 1)
            self.assertTrue(wb.long_text_failures[0]["archived"])
            self.assertFalse(os.path.isdir(os.path.join(tmpdir, "编辑历史")))

    def test_long_text_failed_new_weibo_reported(self):
        """新微博长文失败也记录（标记为新微博仅保存截断正文）。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "2024-05"))
            w = {
                "id": 300,
                "created_at": "2024-05-03T12:00:00",
                "text": "截断正文…全文",
                "pics": "",
                "video_url": "",
                "live_photo_url": "",
                "article_url": "",
                "topics": "",
                "links": [],
                "edit_at": "",
                "visible": {},
                "long_text_failed": True,
            }
            wb = _make_wb(tmpdir, [w])
            with mock.patch("weibo.logger"):
                wb._apply_edit_updates(0)
            self.assertEqual(len(wb.long_text_failures), 1)
            self.assertFalse(wb.long_text_failures[0]["archived"])


class FingerprintStabilityTest(unittest.TestCase):
    def test_edit_at_not_in_fingerprint(self):
        a = archive_state.content_fingerprint(
            text="正文", edit_at="2024-01-01 00:00:00"
        )
        b = archive_state.content_fingerprint(text="正文", edit_at="")
        self.assertEqual(a, b)

    def test_signed_media_url_query_normalized(self):
        a = archive_state.content_fingerprint(
            text="正文",
            video_url="https://f.video.weibocdn.com/o0/a.mp4?Expires=1&ssig=x",
        )
        b = archive_state.content_fingerprint(
            text="正文",
            video_url="https://f.video.weibocdn.com/o0/a.mp4?Expires=2&ssig=y",
        )
        self.assertEqual(a, b)

    def test_media_path_change_still_detected(self):
        a = archive_state.content_fingerprint(
            text="正文", pics="https://wx1.sinaimg.cn/a.jpg"
        )
        b = archive_state.content_fingerprint(
            text="正文", pics="https://wx1.sinaimg.cn/b.jpg"
        )
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()
