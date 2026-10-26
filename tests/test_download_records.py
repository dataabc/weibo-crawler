"""媒体下载失败记录的写入/清理测试（无网络；stub requests）。"""

import shutil
import sys
import types
import unittest
from pathlib import Path


JPEG = b"\xFF\xD8\xFF" + b"0" * 100 + b"\xff\xd9"
PNG = b"\x89PNG\r\n\x1A\n" + b"0" * 100 + b"IEND\xaeB`\x82"
HTML = b"<!DOCTYPE html><html><body>blocked</body></html>"


class _Response:
    def __init__(self, chunks, content_type="image/jpeg", content_length=None):
        self._chunks = chunks
        self.headers = {"Content-Type": content_type}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=0):
        return iter(self._chunks)


class _Session:
    mode = "fail"
    get_calls = 0

    def mount(self, *args, **kwargs):
        pass

    def get(self, *args, **kwargs):
        _Session.get_calls += 1
        if _Session.mode == "fail":
            raise sys.modules["requests.exceptions"].RequestException("模拟网络失败")
        if _Session.mode == "png":
            return _Response([PNG], "image/png")
        if _Session.mode == "html":
            return _Response([HTML], "text/html")
        if _Session.mode == "truncated_jpeg":
            return _Response([JPEG[:-2]], "image/jpeg")
        if _Session.mode == "length_mismatch":
            return _Response([JPEG], "image/jpeg", content_length=len(JPEG) + 100)
        return _Response([JPEG])


class _Adapter:
    def __init__(self, *args, **kwargs):
        pass


class _RequestException(Exception):
    pass


def _install_import_stubs() -> None:
    def add(name: str, **attrs):
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        sys.modules[name] = module
        return module

    # 其他测试文件可能已经装过更简单的 stub：这里覆盖成可工作的版本，
    # 保证本文件在 unittest discover 的任意顺序下都能真实走下载逻辑。
    if "requests" not in sys.modules:
        add("requests", Session=_Session, get=lambda *a, **k: None)
    else:
        sys.modules["requests"].Session = _Session
    if "requests.exceptions" not in sys.modules:
        add("requests.exceptions", RequestException=_RequestException)
    if "requests.adapters" not in sys.modules:
        add("requests.adapters", HTTPAdapter=_Adapter)
    else:
        sys.modules["requests.adapters"].HTTPAdapter = _Adapter
    if "json5" not in sys.modules:
        add("json5", loads=lambda text: {})
    if "tqdm" not in sys.modules:
        add("tqdm", tqdm=lambda iterable, **kwargs: iterable)
    if "lxml" not in sys.modules:
        lxml = add("lxml")
        lxml.etree = add("lxml.etree")
    if "piexif" not in sys.modules:
        add("piexif")


_install_import_stubs()

import weibo  # noqa: E402

# 其他测试可能先导入 weibo 并使用更简单的 HTTPAdapter stub；这里覆盖模块全局绑定，
# 保证本文件在 discover 的任意顺序下都能真实走下载逻辑。
weibo.HTTPAdapter = _Adapter

sys.modules.pop("json5", None)


def _make_wb(out_dir: str):
    wb = weibo.Weibo.__new__(weibo.Weibo)
    wb.user = {"screen_name": "测试用户"}
    wb.user_config = {"user_id": "123"}
    wb.user_id_as_folder_name = 0
    wb.output_directory = out_dir
    wb.headers = {}
    wb.write_mode = []
    wb.write_exif_time = lambda *a, **k: None
    wb.set_file_time = lambda *a, **k: None
    wb.not_downloaded_record = 1
    wb.not_downloaded_record_dir = ""
    wb.media_failure_count = 0
    return wb


class DownloadRecordTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rel = "_test_download_records"
        self.root = Path(__file__).resolve().parents[1] / self.rel
        if self.root.exists():
            shutil.rmtree(self.root)
        self.wb = _make_wb(self.rel)
        self._old_sleep = weibo.sleep
        weibo.sleep = lambda s: None
        _Session.mode = "fail"
        _Session.get_calls = 0

    def tearDown(self) -> None:
        weibo.sleep = self._old_sleep
        _Session.mode = "fail"
        _Session.get_calls = 0
        if self.root.exists():
            shutil.rmtree(self.root)

    def _target(self, sub: str = "原创微博视频", name: str = "视频.mp4") -> Path:
        return self.root / "测试用户" / sub / name

    def test_failure_record_written_to_target_dir_utf8(self) -> None:
        """失败记录写到目标文件所在目录（而非固定的 video/ 目录），且为 UTF-8。"""
        target = self._target()
        self.wb.download_one_file(
            "http://example.com/v.mp4", str(target), "video", "777",
            "2024-01-01T00:00:00",
        )
        nd = target.parent / "not_downloaded.txt"
        self.assertTrue(nd.is_file())
        text = nd.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("777:"))
        self.assertIn("http://example.com/v.mp4", text)
        self.assertEqual(self.wb.media_failure_count, 1)
        # 不再写到固定的 用户/video/ 目录
        self.assertFalse(
            (self.root / "测试用户" / "video" / "not_downloaded.txt").exists()
        )

    def test_success_removes_old_record(self) -> None:
        """文件已存在且非空时，清理对应旧失败记录并直接返回。"""
        target = self._target()
        target.parent.mkdir(parents=True)
        target.write_bytes(JPEG)
        nd = target.parent / "not_downloaded.txt"
        nd.write_text(
            "777:old:http://example.com/v.mp4\n888:old:http://example.com/x.mp4\n",
            encoding="utf-8",
        )
        self.wb.download_one_file(
            "http://example.com/v.mp4", str(target), "video", "777",
            "2024-01-01T00:00:00",
        )
        text = nd.read_text(encoding="utf-8")
        self.assertNotIn("777:", text)
        self.assertIn("888:", text)  # 其他微博的记录不受影响

    def test_empty_file_is_redownloaded(self) -> None:
        """文件存在但为空 → 视为未下载，删除后重下。"""
        target = self._target(name="图片.jpg")
        target.parent.mkdir(parents=True)
        target.write_bytes(b"")
        _Session.mode = "success"
        self.wb.download_one_file(
            "http://example.com/a.jpg", str(target), "img", "777",
            "2024-01-01T00:00:00",
        )
        self.assertTrue(target.is_file())
        self.assertGreater(target.stat().st_size, 0)

    def test_success_download_clears_record(self) -> None:
        """本次下载成功后，清理之前的失败记录。"""
        target = self._target(name="图片.jpg")
        target.parent.mkdir(parents=True)
        nd = target.parent / "not_downloaded.txt"
        nd.write_text("777:old:http://example.com/a.jpg\n", encoding="utf-8")
        _Session.mode = "success"
        self.wb.download_one_file(
            "http://example.com/a.jpg", str(target), "img", "777",
            "2024-01-01T00:00:00",
        )
        self.assertTrue(target.is_file())
        self.assertEqual(nd.read_text(encoding="utf-8"), "")

    def test_record_disabled_does_not_write(self) -> None:
        """not_downloaded_record=0：失败时不写记录，但本轮失败计数仍要统计。"""
        self.wb.not_downloaded_record = 0
        target = self._target()
        self.wb.download_one_file(
            "http://example.com/v.mp4", str(target), "video", "777",
            "2024-01-01T00:00:00",
        )
        self.assertFalse((target.parent / "not_downloaded.txt").exists())
        self.assertEqual(self.wb.media_failure_count, 1)

    def test_record_disabled_does_not_clear_old_record(self) -> None:
        """not_downloaded_record=0：下载成功也不改动已有的失败记录文件。"""
        self.wb.not_downloaded_record = 0
        target = self._target(name="图片.jpg")
        target.parent.mkdir(parents=True)
        target.write_bytes(JPEG)
        nd = target.parent / "not_downloaded.txt"
        old = "777:old:http://example.com/a.jpg\n"
        nd.write_text(old, encoding="utf-8")
        self.wb.download_one_file(
            "http://example.com/a.jpg", str(target), "img", "777",
            "2024-01-01T00:00:00",
        )
        self.assertEqual(nd.read_text(encoding="utf-8"), old)

    def test_central_record_dir(self) -> None:
        """not_downloaded_record_dir="report"：失败记录集中到 output_directory/report。"""
        self.wb.not_downloaded_record_dir = "report"
        target = self._target()
        self.wb.download_one_file(
            "http://example.com/v.mp4", str(target), "video", "777",
            "2024-01-01T00:00:00",
        )
        nd = self.root / "report" / "not_downloaded.txt"
        self.assertTrue(nd.is_file())
        self.assertIn("http://example.com/v.mp4", nd.read_text(encoding="utf-8"))
        self.assertFalse((target.parent / "not_downloaded.txt").exists())

    def test_custom_record_dir(self) -> None:
        """not_downloaded_record_dir=自定义目录：记录写到自定义目录。"""
        self.wb.not_downloaded_record_dir = "custom_failures"
        target = self._target()
        self.wb.download_one_file(
            "http://example.com/v.mp4", str(target), "video", "777",
            "2024-01-01T00:00:00",
        )
        nd = self.root / "custom_failures" / "not_downloaded.txt"
        self.assertTrue(nd.is_file())
        self.assertFalse((target.parent / "not_downloaded.txt").exists())

    def test_default_off_when_attr_missing(self) -> None:
        """未配置 not_downloaded_record 时默认不写失败记录。"""
        del self.wb.not_downloaded_record
        target = self._target()
        self.wb.download_one_file(
            "http://example.com/v.mp4", str(target), "video", "777",
            "2024-01-01T00:00:00",
        )
        self.assertFalse((target.parent / "not_downloaded.txt").exists())
        self.assertFalse((self.root / "report" / "not_downloaded.txt").exists())

    def test_media_dir_still_created_when_record_off(self) -> None:
        """记录开关关闭时，媒体文件所在目录仍要创建（否则下载会失败）。"""
        self.wb.not_downloaded_record = 0
        target = self._target(name="图片.jpg")
        _Session.mode = "success"
        self.wb.download_one_file(
            "http://example.com/a.jpg", str(target), "img", "777",
            "2024-01-01T00:00:00",
        )
        self.assertTrue(target.is_file())
        self.assertGreater(target.stat().st_size, 0)

    def test_incomplete_jpeg_rejected(self) -> None:
        """明显不完整的 JPEG 不落盘，并按开关记录失败。"""
        _Session.mode = "truncated_jpeg"
        self.wb.not_downloaded_record = 1
        target = self._target(name="图片.jpg")
        result = self.wb.download_one_file(
            "http://example.com/a.jpg", str(target), "img", "777",
            "2024-01-01T00:00:00",
        )
        self.assertIsNone(result)
        self.assertFalse(target.exists())
        self.assertTrue((target.parent / "not_downloaded.txt").is_file())
        self.assertEqual(self.wb.media_failure_count, 1)

    def test_html_response_rejected(self) -> None:
        """CDN 返回 HTML 错误页时不落盘。"""
        _Session.mode = "html"
        target = self._target()
        result = self.wb.download_one_file(
            "http://example.com/v.mp4", str(target), "video", "777",
            "2024-01-01T00:00:00",
        )
        self.assertIsNone(result)
        self.assertFalse(target.exists())
        self.assertEqual(self.wb.media_failure_count, 1)

    def test_content_length_mismatch_rejected(self) -> None:
        """Content-Length 与实际字节数不符时不落盘。"""
        _Session.mode = "length_mismatch"
        target = self._target(name="图片.jpg")
        result = self.wb.download_one_file(
            "http://example.com/a.jpg", str(target), "img", "777",
            "2024-01-01T00:00:00",
        )
        self.assertIsNone(result)
        self.assertFalse(target.exists())
        self.assertEqual(self.wb.media_failure_count, 1)

    def test_existing_truncated_file_redownloaded(self) -> None:
        """本地已有但明显损坏的文件会被删除并重下。"""
        target = self._target(name="图片.jpg")
        target.parent.mkdir(parents=True)
        target.write_bytes(JPEG[:-2])
        _Session.mode = "success"
        result = self.wb.download_one_file(
            "http://example.com/a.jpg", str(target), "img", "777",
            "2024-01-01T00:00:00",
        )
        self.assertEqual(result, str(target))
        self.assertTrue(target.read_bytes().endswith(b"\xff\xd9"))
        self.assertEqual(_Session.get_calls, 1)

    def test_existing_valid_file_skips_network(self) -> None:
        """本地文件校验通过时不再消耗流量下载。"""
        target = self._target(name="图片.jpg")
        target.parent.mkdir(parents=True)
        target.write_bytes(JPEG)
        _Session.mode = "fail"  # 如果发生请求会直接失败
        result = self.wb.download_one_file(
            "http://example.com/a.jpg", str(target), "img", "777",
            "2024-01-01T00:00:00",
        )
        self.assertEqual(result, str(target))
        self.assertEqual(_Session.get_calls, 0)
        self.assertEqual(target.read_bytes(), JPEG)

    def test_png_extension_mapping(self) -> None:
        """实际保存为 PNG 时，markdown 文件名跟着实际扩展名走。"""
        img_dir = self.root / "测试用户" / "img"
        img_dir.mkdir(parents=True)
        _Session.mode = "png"
        weibo_data = {
            "id": "1",
            "created_at": "2024-01-01T00:00:00",
            "pics": "http://example.com/a",
        }
        self.wb._download_weibo_images(weibo_data, str(img_dir))
        names = self.wb.get_markdown_image_filenames(weibo_data)
        self.assertEqual(names, ["2024-01-01_00-00-00.png"])
        self.assertTrue((img_dir / names[0]).is_file())


if __name__ == "__main__":
    unittest.main()
