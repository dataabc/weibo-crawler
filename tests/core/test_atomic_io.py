'util/atomic_io.py 单元测试。'

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from util.core import atomic_io  # noqa: E402


class AtomicIoTest(unittest.TestCase):
    def test_atomic_write_text(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "a.txt")
            atomic_io.atomic_write_text(path, "内容")
            self.assertEqual(open(path, encoding="utf-8").read(), "内容")
            self.assertFalse(os.path.exists(path + ".tmp"))

    def test_atomic_write_bytes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "a.bin")
            atomic_io.atomic_write_bytes(path, b"abc")
            self.assertEqual(open(path, "rb").read(), b"abc")

    def test_backup_file_numbering(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "a.txt")
            atomic_io.atomic_write_text(path, "v1")
            first = atomic_io.backup_file(path)
            self.assertIsNotNone(first)
            atomic_io.atomic_write_text(path, "v2")
            second = atomic_io.backup_file(path)
            self.assertNotEqual(first, second)
            self.assertIn(".bak.", second)
