'util/comment_retry.py 单元测试。'

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from util.comment import comment_retry  # noqa: E402


class CommentRetryTest(unittest.TestCase):
    def test_retry_path(self):
        self.assertEqual(
            comment_retry.retry_path("/report/用户", "123"),
            os.path.join("/report/用户", "comment_retry_123.json"),
        )

    def test_load_missing_returns_empty(self):
        self.assertEqual(comment_retry.load("/tmp/not-exists.json"), {})

    def test_load_corrupt_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "comment_retry_1.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("not json")
            self.assertEqual(comment_retry.load(path), {})

    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "comment_retry_1.json")
            data = {"999": {"md_path": "/md/1.md", "note": "失败"}}

            def write(p, text):
                with open(p, "w", encoding="utf-8") as handle:
                    handle.write(text)

            self.assertTrue(comment_retry.save(path, data, write))
            self.assertEqual(comment_retry.load(path), data)

    def test_save_empty_removes_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "comment_retry_1.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{}")
            self.assertTrue(comment_retry.save(path, {}, lambda p, t: None))
            self.assertFalse(os.path.exists(path))
