"""util/report/deleted_weibos.py：本轮确认已删除微博清单测试（无网络）。"""

import os
import sys
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests import _support  # noqa: E402

_support.bootstrap()

from util.report import deleted_weibos  # noqa: E402


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payloads):
        self.payloads = payloads
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        weibo_id = str((params or {}).get("id"))
        self.calls.append(weibo_id)
        return FakeResponse(self.payloads[weibo_id])


def make_wb(tmpdir, session):
    return SimpleNamespace(
        user={"screen_name": "测试用户"},
        user_config={"user_id": "123"},
        write_mode=["markdown"],
        query="",
        session=session,
        headers={},
        crawl_success={"success": True, "attempted": True},
        archived_entries_before={
            "111": (datetime(2024, 5, 3, 10, 0, 0), "a.md"),
            "222": (datetime(2024, 5, 3, 11, 0, 0), "a.md"),
            "333": (datetime(2024, 5, 3, 12, 0, 0), "a.md"),
            "444": (datetime(2024, 5, 3, 13, 0, 0), "a.md"),  # 本轮抓到，不算候选
        },
        check_start_dt=datetime(2024, 5, 1),
        start_date="2024-05-04T00:00:00",
        crawled_ids_this_run={"444"},
        known_invisible_ids=set(),
        weibo_deleted_report=1,
        get_filepath=lambda _type: tmpdir,
        _archive_exclude_dirs=lambda: (),
        _record_write_failure=lambda *args, **kwargs: None,
    )


class DeletedWeibosTest(unittest.TestCase):
    def test_generate_only_confirmed_deleted(self):
        payloads = {
            "111": {"ok": 0, "msg": "该微博不存在"},
            "222": {"ok": 1, "data": {"visible": {"type": 0}}},
            "333": {"ok": 0, "msg": "暂无查看权限"},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            wb = make_wb(tmpdir, FakeSession(payloads))
            path = deleted_weibos.generate(wb)
            self.assertEqual(path, os.path.join(tmpdir, "已删除微博.txt"))
            text = open(path, encoding="utf-8").read()
            self.assertIn("111\t2024-05-03", text)
            self.assertNotIn("222", text)
            self.assertNotIn("333", text)
            self.assertIn("已删除 1、受限可见 1、仍可见 1、无法判断 0", text)

    def test_append_mode_skips(self):
        import const

        old_mode = const.MODE
        const.MODE = "append"
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                wb = make_wb(tmpdir, FakeSession({}))
                self.assertIsNone(deleted_weibos.generate(wb))
        finally:
            const.MODE = old_mode

    def test_disabled_by_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb = make_wb(tmpdir, FakeSession({}))
            wb.weibo_deleted_report = 0
            self.assertIsNone(deleted_weibos.generate(wb))


if __name__ == "__main__":
    unittest.main()
