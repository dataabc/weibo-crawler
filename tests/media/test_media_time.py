'util/media_time.py 单元测试。'

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from util.media import media_time  # noqa: E402


class MediaTimeTest(unittest.TestCase):
    def test_set_file_time(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "a.jpg")
            with open(path, "wb") as handle:
                handle.write(b"x")
            self.assertTrue(
                media_time.set_file_time(path, "2024-05-03T10:00:00")
            )
            expected = time.mktime(time.strptime("2024-05-03 10:00:00", "%Y-%m-%d %H:%M:%S"))
            self.assertAlmostEqual(os.path.getmtime(path), expected, delta=2)

    def test_disabled_returns_false(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "a.jpg")
            with open(path, "wb") as handle:
                handle.write(b"x")
            self.assertFalse(media_time.set_file_time(path, "2024-05-03T10:00:00", False))
            self.assertFalse(media_time.write_exif_time(path, "2024-05-03T10:00:00", False))

    def test_bad_time_does_not_raise(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "a.jpg")
            with open(path, "wb") as handle:
                handle.write(b"x")
            self.assertFalse(media_time.set_file_time(path, "not-a-time"))
