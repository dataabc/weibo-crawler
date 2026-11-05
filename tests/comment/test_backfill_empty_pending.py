"""回填脚本「暂无数据待复核清单」辅助函数测试（无网络）。"""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from tests import _support  # noqa: E402

_support.bootstrap()

import weibo_comment_backfill as backfill  # noqa: E402


class EmptyPendingTest(unittest.TestCase):
    def _record(self, weibo_id="123", hint="暂无数据"):
        return {
            "user": "测试用户",
            "weibo_id": weibo_id,
            "created_at": "2024-05-03T10:00:00",
            "md_path": "/tmp/2024-05-03.md",
            "empty_hint": hint,
        }

    def test_upsert_load_clear_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "comment_empty_data_pending.json")
            items = backfill.load_empty_pending(path)
            self.assertEqual(items, {})

            entry = backfill.upsert_empty_pending(items, self._record())
            self.assertEqual(entry["attempts"], 1)
            self.assertEqual(entry["hint"], "暂无数据")
            backfill.save_empty_pending(path, items)

            loaded = backfill.load_empty_pending(path)
            self.assertEqual(set(loaded), {"123"})
            self.assertEqual(loaded["123"]["user"], "测试用户")

            # 再次 upsert：attempts+1，first_seen 保留
            first_seen = loaded["123"]["first_seen"]
            backfill.upsert_empty_pending(loaded, self._record())
            self.assertEqual(loaded["123"]["attempts"], 2)
            self.assertEqual(loaded["123"]["first_seen"], first_seen)

            self.assertTrue(backfill.clear_empty_pending(loaded, "123"))
            self.assertFalse(backfill.clear_empty_pending(loaded, "123"))
            self.assertEqual(loaded, {})

    def test_bad_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "bad.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("not json")
            self.assertEqual(backfill.load_empty_pending(path), {})


if __name__ == "__main__":
    unittest.main()
