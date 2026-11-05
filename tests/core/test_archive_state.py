"""存档状态索引单元测试。"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from util.core import archive_state  # noqa: E402


def _writer(path, text):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


class StateIOTest(unittest.TestCase):
    def test_load_missing_returns_empty(self):
        state, warning = archive_state.load_state("/nonexistent/state.json")
        self.assertEqual(state["weibos"], {})
        self.assertEqual(warning, "")

    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "state.json")
            state = archive_state.empty_state("123")
            archive_state.upsert_weibo(
                state,
                "500",
                created_at="2024-05-03T10:00:00",
                md_path="2024-05/2024-05-03.md",
                content_hash="abc",
            )
            archive_state.save_state(state, path, _writer)
            loaded, warning = archive_state.load_state(path)
            self.assertEqual(warning, "")
            self.assertEqual(loaded["user_id"], "123")
            item = archive_state.get_weibo(loaded, "500")
            self.assertEqual(item["created_at"], "2024-05-03T10:00:00")
            self.assertEqual(item["md_path"], "2024-05/2024-05-03.md")

    def test_load_corrupt_returns_warning(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "state.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{bad json")
            state, warning = archive_state.load_state(path)
            self.assertEqual(state["weibos"], {})
            self.assertIn("读取失败", warning)


class StateUpdateTest(unittest.TestCase):
    def test_upsert_creates_and_updates(self):
        state = archive_state.empty_state("123")
        item = archive_state.upsert_weibo(state, "500", last_edit_at="2026-10-01T00:00:00")
        self.assertEqual(item["status"], archive_state.STATUS_ACTIVE)
        self.assertEqual(item["last_edit_at"], "2026-10-01T00:00:00")
        item2 = archive_state.upsert_weibo(state, "500", content_hash="xyz")
        self.assertEqual(item2["last_edit_at"], "2026-10-01T00:00:00")
        self.assertEqual(item2["content_hash"], "xyz")
        self.assertIs(item, item2)

    def test_status_and_missing(self):
        state = archive_state.empty_state("123")
        archive_state.upsert_weibo(state, "500")
        self.assertEqual(archive_state.bump_missing(state, "500"), 1)
        self.assertEqual(archive_state.bump_missing(state, "500"), 2)
        archive_state.set_status(state, "500", archive_state.STATUS_SUSPECT)
        self.assertEqual(
            archive_state.get_weibo(state, "500")["status"],
            archive_state.STATUS_SUSPECT,
        )
        archive_state.set_status(state, "500", archive_state.STATUS_ACTIVE)
        self.assertEqual(archive_state.get_weibo(state, "500")["missing_rounds"], 0)

    def test_record_version_idempotent(self):
        state = archive_state.empty_state("123")
        archive_state.upsert_weibo(state, "500")
        self.assertTrue(
            archive_state.record_version(
                state, "500", fingerprint="h1", saved_at="t1", path="p1"
            )
        )
        self.assertFalse(
            archive_state.record_version(
                state, "500", fingerprint="h1", saved_at="t2", path="p2"
            )
        )
        versions = archive_state.get_weibo(state, "500")["versions"]
        self.assertEqual(len(versions), 1)


class VisibilityTest(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(
            archive_state.normalize_visibility({"type": 6, "list_id": 3}),
            {"type": 6, "list_id": 3},
        )
        self.assertEqual(archive_state.normalize_visibility(None), {})
        self.assertEqual(
            archive_state.normalize_visibility({"type": "0"}), {"type": 0, "list_id": 0}
        )

    def test_label(self):
        self.assertEqual(archive_state.visibility_label({"type": 0}), "公开")
        self.assertEqual(archive_state.visibility_label({"type": 1}), "仅自己可见")
        self.assertIn(
            "分组可见", archive_state.visibility_label({"type": 6, "list_id": 3})
        )
        self.assertEqual(archive_state.visibility_label({}), "未知")


class FingerprintTest(unittest.TestCase):
    def test_same_and_different(self):
        a = archive_state.content_fingerprint(text="正文", pics="a.jpg", visible="v0")
        b = archive_state.content_fingerprint(text="正文", pics="a.jpg", visible="v0")
        c = archive_state.content_fingerprint(text="正文改", pics="a.jpg", visible="v0")
        d = archive_state.content_fingerprint(
            text="正文", pics="a.jpg", visible="v0", edit_at="2026-10-01"
        )
        e = archive_state.content_fingerprint(
            text="正文",
            pics="a.jpg",
            visible="v0",
            video_url="https://f.video.weibocdn.com/o0/x.mp4?Expires=1&ssig=aa",
        )
        f = archive_state.content_fingerprint(
            text="正文",
            pics="a.jpg",
            visible="v0",
            video_url="https://f.video.weibocdn.com/o0/x.mp4?Expires=2&ssig=bb",
        )
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        # edit_at 是接口易变字段，不参与指纹（参数仅为兼容保留）
        self.assertEqual(a, d)
        # 媒体 URL 的时效签名参数不参与指纹，路径变化仍能识别
        self.assertEqual(e, f)
        self.assertNotEqual(
            e,
            archive_state.content_fingerprint(
                text="正文",
                pics="a.jpg",
                visible="v0",
                video_url="https://f.video.weibocdn.com/o0/y.mp4",
            ),
        )

    def test_counts_are_not_included(self):
        # 点赞/评论/转发数不在指纹入参里，参数相同即相同
        a = archive_state.content_fingerprint(text="正文")
        b = archive_state.content_fingerprint(text="正文")
        self.assertEqual(a, b)


class PathTest(unittest.TestCase):
    def test_resolve(self):
        self.assertEqual(
            archive_state.resolve_state_path("/data/u", "123", ""),
            "/data/u/archive_state_123.json",
        )
        self.assertEqual(
            archive_state.resolve_state_path("/data/u", "123", "/abs/s.json"),
            "/abs/s.json",
        )


if __name__ == "__main__":
    unittest.main()
