"""覆盖：
- Live Photo 扩展名漂移：QuickTime 内容不再被改成 .mp4，链接与落盘一致；
- JSON 原子写 / 损坏恢复 / write_data 后端隔离；
- 分页空页（ok:0 无 data）不再 sys.exit，按「无更多数据」处理。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

from util.media import media_check


QT_MOV = b"\x00\x00\x00\x14ftypqt  " + b"\x00" * 256
ISO_MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 256


class _Response:
    def __init__(self, chunks, content_type="video/quicktime"):
        self._chunks = chunks
        self.headers = {"Content-Type": content_type}
        self.status_code = 200

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=0):
        return iter(self._chunks)

    def json(self):
        return json.loads(b"".join(self._chunks).decode("utf-8"))


class _Session:
    mode = "mov"
    calls = 0

    def mount(self, *args, **kwargs):
        pass

    def get(self, *args, **kwargs):
        _Session.calls += 1
        if _Session.mode == "mov":
            return _Response([QT_MOV])
        if _Session.mode == "mp4":
            return _Response([ISO_MP4], "video/mp4")
        if _Session.mode == "empty_page":
            return _Response([b'{"ok": 0, "msg": "\xe8\xbf\x99\xe9\x87\x8c\xe8\xbf\x98\xe6\xb2\xa1\xe6\x9c\x89\xe5\x86\x85\xe5\xae\xb9"}'], "application/json")
        if _Session.mode == "captcha":
            return _Response([b'{"ok": 0, "url": "https://example.com/captcha"}'], "application/json")
        return _Response([QT_MOV])


class _Adapter:
    def __init__(self, *args, **kwargs):
        pass


class _RequestException(Exception):
    pass


from tests import _support  # noqa: E402

_support.bootstrap()

import weibo  # noqa: E402

weibo.HTTPAdapter = _Adapter
sys.modules.pop("json5", None)


class QuickTimeKindTest(unittest.TestCase):
    """QuickTime 容器必须识别为 mov，而不是一律 mp4。"""

    def test_detect_kind_quicktime_is_mov(self) -> None:
        self.assertEqual(media_check.detect_kind(QT_MOV), "mov")

    def test_detect_kind_isom_is_still_mp4(self) -> None:
        self.assertEqual(media_check.detect_kind(ISO_MP4), "mp4")

    def test_validate_bytes_quicktime_keeps_mov_extension(self) -> None:
        ok, reason, kind, detected = media_check.validate_bytes(
            QT_MOV, "https://example.com/a.mov", "video/quicktime", ".mov"
        )
        self.assertTrue(ok, reason)
        self.assertEqual(kind, "mov")
        self.assertEqual(detected, ".mov")

    def test_validate_bytes_mp4_unchanged(self) -> None:
        ok, reason, kind, detected = media_check.validate_bytes(
            ISO_MP4, "https://example.com/a.mp4", "video/mp4", ".mp4"
        )
        self.assertTrue(ok, reason)
        self.assertEqual(kind, "mp4")
        self.assertEqual(detected, ".mp4")


class LivePhotoExtensionTest(unittest.TestCase):
    """.mov 下载后保留 .mov（不再改名 .mp4，避免 md 链接死链）。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.wb = weibo.Weibo.__new__(weibo.Weibo)
        self.wb.headers = {}
        self.wb.write_mode = []
        self.wb.not_downloaded_record = 0
        self.wb.media_failure_count = 0
        self.wb.change_file_time = 0
        self.wb.write_time_in_exif = 0
        # 本测试运行时临时接管 requests.Session，结束后恢复，
        # 避免污染其他测试文件（discover 导入期已把它们装好的 stub 留在 sys.modules）。
        self._saved_session = sys.modules["requests"].Session
        sys.modules["requests"].Session = _Session
        _Session.mode = "mov"
        _Session.calls = 0

    def tearDown(self) -> None:
        sys.modules["requests"].Session = self._saved_session
        self.tmp.cleanup()

    def test_live_photo_mov_not_renamed_to_mp4(self) -> None:
        target = self.root / "测试用户" / "live_photo" / "2024-01-01_00-00-00.mov"
        result = self.wb.download_one_file(
            "https://example.com/livephoto.mov",
            str(target),
            "live_photo",
            "1",
            "2024-01-01T00:00:00",
        )
        self.assertEqual(result, str(target))
        self.assertTrue(target.is_file())
        self.assertEqual(target.read_bytes()[:12], QT_MOV[:12])
        # 不应把内容改写成 .mp4 后落盘
        mp4_peer = target.with_suffix(".mp4")
        self.assertFalse(mp4_peer.exists())

    def test_video_mov_content_keeps_url_extension(self) -> None:
        target = self.root / "测试用户" / "video" / "2024-01-01_00-00-00.mov"
        result = self.wb.download_one_file(
            "https://example.com/v.mov",
            str(target),
            "video",
            "2",
            "2024-01-01T00:00:00",
        )
        self.assertEqual(result, str(target))
        self.assertTrue(target.is_file())


class JsonAtomicWriteTest(unittest.TestCase):
    """JSON 原子写 + 损坏恢复。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.json_path = self.dir / "123.json"
        self.wb = weibo.Weibo.__new__(weibo.Weibo)
        self.wb.user = {"id": "123", "screen_name": "测试用户"}
        self.wb.user_config = {"user_id": "123"}
        self.wb.weibo = []
        self.wb.got_count = 1
        self.wb.get_filepath = lambda _type: str(self.json_path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_corrupt_json_backed_up_and_rebuilt(self) -> None:
        broken = '{"user": {"id": "123"}, "weibo": [{"id": "1"}, {"id":'
        self.json_path.write_text(broken, encoding="utf-8")
        self.wb.weibo = [{"id": "9001", "text": "新微博"}]

        self.wb.write_json(0)

        data = json.loads(self.json_path.read_text(encoding="utf-8"))
        self.assertEqual([w["id"] for w in data["weibo"]], ["9001"])
        backups = list(self.dir.glob("123.json.corrupt.*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), broken)
        # 不残留临时文件
        self.assertFalse(Path(str(self.json_path) + ".tmp").exists())

    def test_normal_append_keeps_existing(self) -> None:
        self.json_path.write_text(
            json.dumps({"user": {"id": "123"}, "weibo": [{"id": "old"}]}),
            encoding="utf-8",
        )
        self.wb.weibo = [{"id": "new"}]

        self.wb.write_json(0)

        data = json.loads(self.json_path.read_text(encoding="utf-8"))
        self.assertEqual([w["id"] for w in data["weibo"]], ["old", "new"])

    def test_write_creates_tmp_then_replaces(self) -> None:
        self.wb.weibo = [{"id": "1"}]
        self.wb.write_json(0)
        self.assertTrue(self.json_path.is_file())
        self.assertEqual(
            sorted(p.name for p in self.dir.iterdir()), ["123.json"]
        )


class WriteDataIsolationTest(unittest.TestCase):
    """一个后端失败不能阻断其余后端（尤其 markdown）。"""

    def test_json_failure_does_not_block_markdown(self) -> None:
        wb = weibo.Weibo.__new__(weibo.Weibo)
        wb.write_mode = ["json", "markdown"]
        wb.got_count = 2
        wb.original_pic_download = 0
        wb.original_video_download = 0
        wb.original_live_photo_download = 0
        wb.only_crawl_original = 1
        calls = []

        def broken_json(*args, **kwargs):
            raise RuntimeError("模拟 JSON 写入失败")

        wb.write_json = broken_json
        wb.write_csv = lambda wrote: calls.append("csv")
        wb.write_markdown = lambda wrote: calls.append(("markdown", wrote))

        wb.write_data(0)

        self.assertEqual(calls, [("markdown", 0)])


class EmptyPageTest(unittest.TestCase):
    """接口返回无 data 的 ok:0 时不再 sys.exit。"""

    def _make_wb(self):
        wb = weibo.Weibo.__new__(weibo.Weibo)
        wb.user_config = {"user_id": "123"}
        wb.query = ""
        wb.page_weibo_count = 20
        wb.headers = {}
        wb.session = _Session()
        wb.get_random_headers = lambda: {}
        wb.calculate_dynamic_delay = lambda: 0
        wb.update_crawl_stats = lambda **kwargs: None
        wb.handle_captcha = lambda js: False
        wb.crawl_success = {"attempted": True, "success": False, "page_errors": []}
        return wb

    def test_empty_page_returns_none(self) -> None:
        _Session.mode = "empty_page"
        wb = self._make_wb()
        self.assertIsNone(wb.get_weibo_json(3))

    def test_captcha_failure_returns_failure_marker(self) -> None:
        _Session.mode = "captcha"
        wb = self._make_wb()
        # 验证码失败不再 sys.exit；返回 {} 让上层记为页失败
        self.assertEqual(wb.get_weibo_json(3), {})

    def test_get_one_page_none_marks_api_ended(self) -> None:
        wb = self._make_wb()
        wb.get_weibo_json = lambda page: None
        self.assertTrue(wb.get_one_page(3))
        self.assertTrue(wb.crawl_success["api_ended"])

    def test_get_one_page_failure_records_page_error(self) -> None:
        wb = self._make_wb()
        wb.get_weibo_json = lambda page: {}
        wb.get_one_page(7)
        self.assertIn(7, wb.crawl_success["page_errors"])
        self.assertFalse(wb.crawl_success["success"])


class RunLockTest(unittest.TestCase):
    """主爬的抓取/回填互斥锁。"""

    def test_acquire_run_lock_blocks_when_held(self) -> None:
        from util.core import file_lock

        with tempfile.TemporaryDirectory() as tmp:
            wb = weibo.Weibo.__new__(weibo.Weibo)
            wb.output_directory = tmp  # 绝对路径
            path = file_lock.lock_path_for_output(tmp)
            holder = file_lock.FileLock(path)
            holder.acquire()
            try:
                self.assertIsNone(wb._acquire_run_lock())
            finally:
                holder.release()
            lock = wb._acquire_run_lock()
            self.assertIsNotNone(lock)
            lock.release()

    def test_relative_output_directory_resolves_under_project(self) -> None:
        wb = weibo.Weibo.__new__(weibo.Weibo)
        wb.output_directory = "_test_run_lock_rel"
        root = wb._output_root_dir()
        self.assertTrue(root.endswith("_test_run_lock_rel"))
        self.assertTrue(root.startswith(str(Path(weibo.__file__).resolve().parent)))


if __name__ == "__main__":
    unittest.main()
