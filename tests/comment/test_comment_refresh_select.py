'util/comment_refresh.py 单元测试。'

import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from util.comment import comment_refresh  # noqa: E402


def _locate(weibo_id, item, created_at):
    return f"/md/{weibo_id}.md", ""


class CommentRefreshSelectTest(unittest.TestCase):
    def test_rotation_oldest_first_and_limit(self):
        state = {
            "weibos": {
                "1": {"comment_refreshed_at": "2026-10-01 00:00:00"},
                "2": {"comment_refreshed_at": ""},
                "3": {"comment_refreshed_at": "2026-10-20 00:00:00"},
            }
        }
        weibos = [
            {"id": "1", "created_at": "2026-01-01T00:00:00"},
            {"id": "2", "created_at": "2026-02-01T00:00:00"},
            {"id": "3", "created_at": "2026-03-01T00:00:00"},
        ]
        result = comment_refresh.select_archive_refresh_candidates(
            weibos, state, _locate, limit=2
        )
        # 从未刷新（空）的最优先，其次是最早刷新的 1；3 被预算截掉
        self.assertEqual([wid for wid, _ in result], ["2", "1"])

    def test_skip_ids(self):
        state = {"weibos": {}}
        weibos = [{"id": "1", "created_at": "2026-01-01T00:00:00"}]
        result = comment_refresh.select_archive_refresh_candidates(
            weibos, state, _locate, skip_ids={"1"}
        )
        self.assertEqual(result, [])

    def test_recent_scope_filters_old(self):
        state = {"weibos": {}}
        weibos = [
            {"id": "old", "created_at": "2020-01-01T00:00:00"},
            {"id": "new", "created_at": "2026-10-01T00:00:00"},
        ]
        result = comment_refresh.select_archive_refresh_candidates(
            weibos,
            state,
            _locate,
            scope="recent",
            recent_days=30,
            now=datetime(2026, 10, 26, 12, 0, 0),
        )
        self.assertEqual([wid for wid, _ in result], ["new"])

    def test_unknown_scope_returns_empty(self):
        self.assertEqual(
            comment_refresh.select_archive_refresh_candidates(
                [], {"weibos": {}}, _locate, scope="bogus"
            ),
            [],
        )
