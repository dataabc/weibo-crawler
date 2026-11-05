"""评论导出 CSV 测试。

覆盖：开关跳过、数据库缺失、用户 ID 为空、无评论、正常导出（只导出当前用户
微博下的评论 + 按微博拆分）、数据库异常不抛出。"""

import csv
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests import _support  # noqa: E402

_support.bootstrap()

from util.comment import comment_export  # noqa: E402

HEADER = [
    "id",
    "weibo_id",
    "created_at",
    "user_screen_name",
    "text",
    "pic_url",
    "like_count",
]


class CommentExportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = str(self.root / "weibodata.db")
        self.user_dir = self.root / "测试用户"
        self.user_dir.mkdir()
        self.wb = types.SimpleNamespace()
        self.wb.write_mode = ["sqlite", "markdown"]
        self.wb.download_comment = 1
        self.wb.user_config = {"user_id": "123"}
        self.wb.user = {"screen_name": "测试用户"}
        self.wb.get_sqlte_path = lambda: self.db_path
        self.wb.get_filepath = lambda _type: str(self.user_dir / "123.csv")

    def tearDown(self):
        self.tmp.cleanup()

    def _make_db(self):
        con = sqlite3.connect(self.db_path)
        try:
            con.execute("CREATE TABLE weibo (id TEXT PRIMARY KEY, user_id TEXT)")
            con.execute(
                "CREATE TABLE comments ("
                "id TEXT, weibo_id TEXT, created_at TEXT, user_screen_name TEXT, "
                "text TEXT, pic_url TEXT, like_count INTEGER)"
            )
            con.executemany(
                "INSERT INTO weibo VALUES (?, ?)", [("w1", "123"), ("w2", "999")]
            )
            con.executemany(
                "INSERT INTO comments VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    ("c1", "w1", "2024-01-01", "甲", "评论1", "", 1),
                    ("c2", "w1", "2024-01-02", "乙", "评论2", "", 2),
                    ("c3", "w2", "2024-01-03", "丙", "他人微博评论", "", 3),
                ],
            )
            con.commit()
        finally:
            con.close()

    def _csv_files(self):
        return sorted(self.user_dir.glob("*.csv"))

    def test_exports_only_current_user_comments(self):
        self._make_db()
        comment_export.export_comments_to_csv_for_current_user(self.wb)

        summary = self.user_dir / "测试用户_comments.csv"
        self.assertTrue(summary.is_file(), self._csv_files())
        with summary.open(encoding="utf-8-sig") as handle:
            rows = list(csv.reader(handle))
        self.assertEqual(rows[0], HEADER)
        self.assertEqual(len(rows), 3)  # 表头 + 2 条（不含其他用户的 w2）
        self.assertEqual([row[0] for row in rows[1:]], ["c1", "c2"])

        # 按微博拆分导出
        per_weibo = self.user_dir / "测试用户_w1_comments.csv"
        self.assertTrue(per_weibo.is_file())
        with per_weibo.open(encoding="utf-8-sig") as handle:
            per_rows = list(csv.reader(handle))
        self.assertEqual(len(per_rows), 3)

    def test_no_comments_skips_export(self):
        self._make_db()
        self.wb.user_config = {"user_id": "888"}  # 数据库里没有该用户的微博
        comment_export.export_comments_to_csv_for_current_user(self.wb)
        self.assertEqual(self._csv_files(), [])

    def test_missing_database_returns_without_raising(self):
        comment_export.export_comments_to_csv_for_current_user(self.wb)
        self.assertEqual(self._csv_files(), [])

    def test_skips_when_sqlite_not_in_write_mode(self):
        self.wb.write_mode = ["markdown"]
        self.wb.get_sqlte_path = lambda: (_ for _ in ()).throw(
            AssertionError("write_mode 不含 sqlite 时不应读取数据库路径")
        )
        comment_export.export_comments_to_csv_for_current_user(self.wb)
        self.assertEqual(self._csv_files(), [])

    def test_skips_when_download_comment_off(self):
        self.wb.download_comment = 0
        comment_export.export_comments_to_csv_for_current_user(self.wb)
        self.assertEqual(self._csv_files(), [])

    def test_empty_user_id_returns(self):
        self._make_db()
        self.wb.user_config = {}
        comment_export.export_comments_to_csv_for_current_user(self.wb)
        self.assertEqual(self._csv_files(), [])

    def test_database_schema_error_does_not_raise(self):
        # 空数据库（没有 comments/weibo 表）：异常应被兜底捕获
        sqlite3.connect(self.db_path).close()
        comment_export.export_comments_to_csv_for_current_user(self.wb)
        self.assertEqual(self._csv_files(), [])


if __name__ == "__main__":
    unittest.main()
