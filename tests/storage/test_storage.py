"""存储层（csv/json/sqlite）行为测试：迁移前的护栏。"""

import json
import os
import sys
import tempfile
import unittest
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


from tests import _support  # noqa: E402

_support.bootstrap()

import weibo  # noqa: E402


def _make_wb(tmpdir):
    wb = weibo.Weibo.__new__(weibo.Weibo)
    wb.user = {"screen_name": "测试用户"}
    wb.weibo = [
        {"id": "1", "text": "a"},
        {"id": "2", "text": "b"},
    ]
    wb.got_count = 2
    wb.sqlite_db_path = os.path.join(tmpdir, "weibo.db")
    wb.store_binary_in_sqlite = 1
    return wb


class JsonStorageTest(unittest.TestCase):
    def test_write_json_and_dedup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb = _make_wb(tmpdir)
            path = os.path.join(tmpdir, "weibo.json")
            wb.get_filepath = lambda _type: path
            wb.write_json(0)
            data = json.load(open(path, encoding="utf-8"))
            self.assertEqual(len(data["weibo"]), 2)
            self.assertEqual(data["user"]["screen_name"], "测试用户")
            # 重写同 ID：不重复（update_json_data 按 ID 合并）
            wb.write_json(0)
            data = json.load(open(path, encoding="utf-8"))
            self.assertEqual(len(data["weibo"]), 2)

    def test_corrupt_json_backed_up_and_rebuilt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb = _make_wb(tmpdir)
            path = os.path.join(tmpdir, "weibo.json")
            wb.get_filepath = lambda _type: path
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("not json")
            wb.write_json(0)
            backups = [f for f in os.listdir(tmpdir) if ".corrupt." in f]
            self.assertEqual(len(backups), 1)
            data = json.load(open(path, encoding="utf-8"))
            self.assertEqual(len(data["weibo"]), 2)


class CsvStorageTest(unittest.TestCase):
    def test_write_csv_dedup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb = _make_wb(tmpdir)
            path = os.path.join(tmpdir, "weibo.csv")
            wb.get_filepath = lambda _type: path
            wb.get_result_headers = lambda: ["id", "text"]
            wb.get_write_info = lambda wrote_count: [
                OrderedDict({"id": "1", "text": "a"}),
                OrderedDict({"id": "2", "text": "b"}),
            ]
            wb.write_csv(0)
            lines = open(path, encoding="utf-8-sig").read().strip().split("\n")
            self.assertEqual(len(lines), 3)  # 表头 + 2 行
            wb.write_csv(0)
            lines = open(path, encoding="utf-8-sig").read().strip().split("\n")
            self.assertEqual(len(lines), 3)  # 去重后不新增


class SqliteStorageTest(unittest.TestCase):
    def test_create_table_and_file_records(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb = _make_wb(tmpdir)
            con = wb.get_sqlite_connection()
            tables = {
                row[0]
                for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            con.close()
            self.assertIn("user", tables)
            self.assertIn("weibo", tables)
            self.assertIn("bins", tables)
            # 文件记录：未存在 → False；插入后 → True
            self.assertFalse(wb.sqlite_exist_file("/tmp/a.jpg"))
            wb.insert_file_sqlite("/tmp/a.jpg", "100", "https://x/a.jpg", b"data")
            self.assertTrue(wb.sqlite_exist_file("/tmp/a.jpg"))


if __name__ == "__main__":
    unittest.main()
