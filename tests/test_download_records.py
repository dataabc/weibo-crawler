"""媒体下载失败记录的写入/清理测试（无网络；stub requests）。"""

import shutil
import sys
import types
import unittest
from pathlib import Path


JPEG = b"\xFF\xD8\xFF" + b"0" * 100 + b"\xff\xd9"


class _Response:
    def __init__(self, chunks, content_type="image/jpeg"):
        self._chunks = chunks
        self.headers = {"Content-Type": content_type}

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=0):
        return iter(self._chunks)


class _Session:
    mode = "fail"

    def mount(self, *args, **kwargs):
        pass

    def get(self, *args, **kwargs):
        if _Session.mode == "fail":
            raise sys.modules["requests.exceptions"].RequestException("模拟网络失败")
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

    def tearDown(self) -> None:
        weibo.sleep = self._old_sleep
        _Session.mode = "fail"
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


if __name__ == "__main__":
    unittest.main()
