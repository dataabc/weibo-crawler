"""隔离区审核主流程测试。

覆盖：增量轮跳过、首次未抓到只记轮次、达到阈值复制到隔离区、复制失败转可疑、
恢复可见删除副本、仍隔离计数、状态保存失败不抛出。"""

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests import _support  # noqa: E402

_support.bootstrap()

from util.report import invisible_archive  # noqa: E402


def _state(items):
    """构造最小状态索引：items = {weibo_id: {created_at, status, missing_rounds}}。"""
    return {"weibos": dict(items)}


def _make_wb(state, *, captured=(), copy_ok=True, window_start="2020-01-01"):
    wb = types.SimpleNamespace()
    wb.invisible_archive_enabled = 1
    wb.invisible_archive_dir = "已不可见"
    wb.invisible_confirm_rounds = 2
    wb.user_config = {}
    wb.crawl_success = {
        "attempted": True,
        "success": True,
        "window_start": window_start,
    }
    wb.weibo = [{"id": wid} for wid in captured]
    wb._state_path = ""
    wb._ensure_archive_state = lambda: state
    wb.get_filepath = lambda _type: wb._tmpdir
    wb.invisible_review = None

    calls = {"copied": [], "removed": [], "saved": 0, "backfill": 0}

    def _copy(archive_root, wid, item, user_dir):
        calls["copied"].append(wid)
        return copy_ok

    def _remove(archive_root, wid):
        calls["removed"].append(wid)

    def _save(path, text):
        calls["saved"] += 1
        Path(path).write_text(text, encoding="utf-8")

    wb._copy_to_isolated = _copy
    wb._remove_isolated_copy = _remove
    wb._atomic_write_text = _save

    def _backfill(archive_root):
        calls["backfill"] += 1
        return {}

    wb._backfill_isolated_media = _backfill
    wb._calls = calls
    return wb


class InvisibleArchiveReviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _wb(self, state, **kwargs):
        wb = _make_wb(state, **kwargs)
        wb._tmpdir = str(self.tmpdir)
        wb._state_path = str(self.tmpdir / "archive_state.json")
        return wb

    def test_incremental_round_skips_judgement(self):
        state = _state(
            {"100": {"created_at": "2024-06-01 10:00:00", "status": "active"}}
        )
        # window_start 晚于存档最早日期 => 增量轮，不做消失判定
        wb = self._wb(state, window_start="2024-06-02T00:00:00")
        invisible_archive.review_invisible_archive(wb)
        self.assertIsNone(wb.invisible_review)
        self.assertEqual(wb._calls["copied"], [])

    def test_first_miss_only_increments_rounds(self):
        state = _state(
            {"100": {"created_at": "2024-06-01 10:00:00", "status": "active"}}
        )
        wb = self._wb(state)
        invisible_archive.review_invisible_archive(wb)
        self.assertEqual(state["weibos"]["100"]["missing_rounds"], 1)
        self.assertEqual(state["weibos"]["100"]["status"], "active")
        self.assertEqual(wb._calls["copied"], [])
        self.assertEqual(wb._calls["saved"], 1)

    def test_reaches_threshold_and_isolates(self):
        state = _state(
            {
                "100": {
                    "created_at": "2024-06-01 10:00:00",
                    "status": "active",
                    "missing_rounds": 1,
                }
            }
        )
        wb = self._wb(state)
        invisible_archive.review_invisible_archive(wb)
        self.assertEqual(state["weibos"]["100"]["status"], "isolated")
        self.assertEqual(wb._calls["copied"], ["100"])
        self.assertEqual(len(wb.invisible_review["new_isolated"]), 1)

    def test_copy_failure_marks_suspect(self):
        state = _state(
            {
                "100": {
                    "created_at": "2024-06-01 10:00:00",
                    "status": "active",
                    "missing_rounds": 1,
                }
            }
        )
        wb = self._wb(state, copy_ok=False)
        invisible_archive.review_invisible_archive(wb)
        self.assertEqual(state["weibos"]["100"]["status"], "suspect_invisible")
        self.assertEqual(wb.invisible_review["new_isolated"], [])

    def test_restored_visible_removes_copy(self):
        state = _state(
            {
                "100": {
                    "created_at": "2024-06-01 10:00:00",
                    "status": "isolated",
                    "missing_rounds": 2,
                }
            }
        )
        wb = self._wb(state, captured=["100"])
        invisible_archive.review_invisible_archive(wb)
        self.assertEqual(state["weibos"]["100"]["status"], "active")
        self.assertEqual(wb._calls["removed"], ["100"])
        self.assertEqual(len(wb.invisible_review["restored"]), 1)

    def test_still_isolated_is_counted(self):
        state = _state(
            {
                "100": {
                    "created_at": "2024-06-01 10:00:00",
                    "status": "isolated",
                    "missing_rounds": 3,
                }
            }
        )
        wb = self._wb(state)
        invisible_archive.review_invisible_archive(wb)
        self.assertEqual(wb.invisible_review["kept"], 1)
        self.assertEqual(wb._calls["copied"], [])

    def test_state_save_failure_does_not_raise(self):
        state = _state(
            {"100": {"created_at": "2024-06-01 10:00:00", "status": "active"}}
        )
        wb = self._wb(state)
        wb._atomic_write_text = lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
        invisible_archive.review_invisible_archive(wb)  # 不应抛出
        self.assertEqual(wb._calls["backfill"], 1)


if __name__ == "__main__":
    unittest.main()
