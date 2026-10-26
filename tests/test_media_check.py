"""util.media_check 的轻量校验测试（纯函数、无网络）。"""

import os
import tempfile
import unittest
from pathlib import Path

from util import media_check

JPEG = b"\xFF\xD8\xFF" + b"0" * 100 + b"\xff\xd9"
PNG = b"\x89PNG\r\n\x1A\n" + b"0" * 100 + b"IEND\xaeB`\x82"
GIF = b"GIF89a" + b"0" * 100 + b";"
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"0" * 100
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"0" * 100
WEBM = b"\x1aE\xdf\xa3" + b"0" * 100
HTML = b"<!DOCTYPE html><html><body>blocked</body></html>"


class ValidateBytesTest(unittest.TestCase):
    def test_jpeg_good(self) -> None:
        ok, reason, kind, ext = media_check.validate_bytes(JPEG, "http://e/a.jpg", "image/jpeg")
        self.assertTrue(ok, reason)
        self.assertEqual((kind, ext), ("jpeg", ".jpg"))

    def test_jpeg_incomplete(self) -> None:
        ok, reason, kind, _ext = media_check.validate_bytes(JPEG[:-2], "http://e/a.jpg")
        self.assertFalse(ok)
        self.assertEqual(kind, "jpeg")
        self.assertIn("不完整", reason)

    def test_png_good_and_incomplete(self) -> None:
        ok, _reason, kind, ext = media_check.validate_bytes(PNG, "http://e/a.png")
        self.assertTrue(ok)
        self.assertEqual((kind, ext), ("png", ".png"))
        ok, reason, _kind, _ext = media_check.validate_bytes(PNG[:-4], "http://e/a.png")
        self.assertFalse(ok)
        self.assertIn("不完整", reason)

    def test_gif_webp_mp4_webm(self) -> None:
        for data, kind, url in (
            (GIF, "gif", "http://e/a.gif"),
            (WEBP, "webp", "http://e/a.webp"),
            (MP4, "mp4", "http://e/a.mp4"),
            (WEBM, "webm", "http://e/a.webm"),
        ):
            with self.subTest(kind=kind):
                ok, reason, detected, _ext = media_check.validate_bytes(data, url)
                self.assertTrue(ok, reason)
                self.assertEqual(detected, kind)

    def test_html_rejected(self) -> None:
        ok, reason, kind, _ext = media_check.validate_bytes(HTML, "http://e/a.mp4")
        self.assertFalse(ok)
        self.assertEqual(kind, "html")
        self.assertIn("HTML", reason)

    def test_unknown_media_hint_rejected(self) -> None:
        ok, reason, _kind, _ext = media_check.validate_bytes(
            b"not a media file", "http://e/a.jpg"
        )
        self.assertFalse(ok)
        self.assertIn("无法识别", reason)

    def test_unknown_without_hint_accepted(self) -> None:
        ok, _reason, kind, _ext = media_check.validate_bytes(
            b"plain text", "http://e/a.txt"
        )
        self.assertTrue(ok)
        self.assertEqual(kind, "unknown")

    def test_content_length(self) -> None:
        self.assertTrue(media_check.check_content_length(None, 10)[0])
        self.assertTrue(media_check.check_content_length("10", 10)[0])
        ok, reason = media_check.check_content_length("100", 10)
        self.assertFalse(ok)
        self.assertIn("100", reason)
        self.assertIn("10", reason)


class ValidateFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write(self, name: str, data: bytes) -> str:
        path = os.path.join(self.tmp.name, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def test_good_files(self) -> None:
        for name, data in (
            ("a.jpg", JPEG),
            ("a.png", PNG),
            ("a.gif", GIF),
            ("a.mp4", MP4),
        ):
            with self.subTest(name=name):
                ok, reason = media_check.validate_file(self._write(name, data))
                self.assertTrue(ok, reason)

    def test_bad_files(self) -> None:
        cases = {
            "empty.jpg": b"",
            "broken.jpg": JPEG[:-2],
            "broken.png": PNG[:-4],
            "html.mp4": HTML,
        }
        for name, data in cases.items():
            with self.subTest(name=name):
                ok, _reason = media_check.validate_file(self._write(name, data))
                self.assertFalse(ok)

    def test_missing_file(self) -> None:
        ok, _reason = media_check.validate_file(str(Path(self.tmp.name) / "missing.jpg"))
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
