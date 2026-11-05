"""comment_worker.run 的 scope 分支测试。"""

import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests import _support  # noqa: E402

_support.bootstrap()

from util.comment import comment_worker  # noqa: E402


def _fake_wb(scope: str, ensure_state=None):
    wb = types.SimpleNamespace()
    wb.download_comment = 1
    wb.write_mode = ["markdown"]
    wb.new_written_weibo = {}
    wb.comment_refresh_scope = scope
    wb.comment_summary_enabled = 0
    wb._load_comment_retry = lambda: {}
    wb._save_comment_retry = lambda mapping: None
    wb._ensure_archive_state = ensure_state or (lambda: {"weibos": {}})
    wb._locate_block_file = lambda *a, **k: None
    wb._atomic_write_text = lambda *a, **k: None
    wb._state_path = ""
    return wb


class CommentRefScopeTest(unittest.TestCase):
    def test_scope_off_does_not_raise(self):
        """scope=off：不进入已存档刷新分支，但函数尾部仍能正常结束。"""
        wb = _fake_wb("off")
        comment_worker.run(wb)

    def test_scope_all_with_empty_state(self):
        wb = _fake_wb("all")
        comment_worker.run(wb)

    def test_scope_recent_with_empty_state(self):
        wb = _fake_wb("recent")
        comment_worker.run(wb)

    def test_scope_all_tolerates_state_failure(self):
        def _boom():
            raise OSError("状态索引不可用")

        wb = _fake_wb("all", ensure_state=_boom)
        comment_worker.run(wb)


if __name__ == "__main__":
    unittest.main()
