'util/not_downloaded.py 单元测试。'

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from util.media import not_downloaded  # noqa: E402


class NotDownloadedTest(unittest.TestCase):
    def test_parse_line(self):
        parsed = not_downloaded.parse_line(
            "123:/data/u/2024-05/原创微博图片/a.jpg:https://cdn.example/a.jpg"
        )
        self.assertEqual(
            parsed,
            ("123", "/data/u/2024-05/原创微博图片/a.jpg", "https://cdn.example/a.jpg"),
        )
        self.assertIsNone(not_downloaded.parse_line("bad-line"))
        self.assertIsNone(not_downloaded.parse_line(""))

    def test_record_path_media_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "2024-05", "原创微博图片", "a.jpg")
            path = not_downloaded.record_path(target, "")
            self.assertEqual(path, os.path.join(os.path.dirname(target), "not_downloaded.txt"))

    def test_record_path_report_mode_uses_resolver(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = not_downloaded.record_path(
                "/tmp/a.jpg", "report", lambda configured: os.path.join(tmpdir, "report")
            )
            self.assertEqual(path, os.path.join(tmpdir, "report", "not_downloaded.txt"))

    def test_clear_record(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            error_file = os.path.join(tmpdir, "not_downloaded.txt")
            with open(error_file, "w", encoding="utf-8") as handle:
                handle.write("1:/a.jpg:https://x/a.jpg\n1:/b.jpg:https://x/b.jpg\n")
            self.assertTrue(not_downloaded.clear_record(error_file, "1", "https://x/a.jpg"))
            with open(error_file, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "1:/b.jpg:https://x/b.jpg\n")
            self.assertFalse(not_downloaded.clear_record(error_file, "9", "https://x/c.jpg"))

    def test_load_map(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            user_dir = os.path.join(tmpdir, "用户A")
            os.makedirs(user_dir)
            report_dir = os.path.join(tmpdir, "report")
            os.makedirs(report_dir)
            with open(
                os.path.join(user_dir, "not_downloaded.txt"), "w", encoding="utf-8"
            ) as handle:
                handle.write("1:/tmp/x/a.jpg:https://x/a.jpg\n")
            with open(
                os.path.join(report_dir, "not_downloaded.txt"), "w", encoding="utf-8"
            ) as handle:
                handle.write("2:/tmp/x/b.jpg:https://x/b.jpg\n")
            result = not_downloaded.load_map(user_dir, report_dir)
            self.assertIn("/tmp/x/a.jpg", result)
            self.assertIn("/tmp/x/b.jpg", result)
            self.assertEqual(result["/tmp/x/b.jpg"][0], "2")


if __name__ == "__main__":
    unittest.main()
