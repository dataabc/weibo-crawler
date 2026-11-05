"""覆盖项：
- anti_ban_config 与默认值合并（只写 {"enabled": true} 不再 KeyError）；
- get_page_count 先校验后除法（非法 page_weibo_count 返回 None 不抛异常）；
- get_write_info 字段白名单（llm_analysis 等附加字段不再挤偏 CSV 列）；
- SQLite 连接每次都补建表（0 字节/旧库缺 comments、reposts 表）；
- 解析容错（string_to_int / standardize_date / standardize_info /
        get_one_weibo 解析失败计数）；
- update_user_config_file 按任意空白 split、保留其余列、原子写；
- weibo_to_sqlite 单条评论抓取异常隔离 + parse_sqlite_comment 容错；
- mysql_insert 只取建表列（附加字段不再触发 1054）；
- weibo_to_sqlite 的 sleep 条件（每条都睡 → 每 20 条才睡）。

测试环境无第三方依赖：按 tests/test_p0_regressions.py 的做法「只在缺失时补
stub、运行期临时替换、用完恢复」；全部 tempfile、不联网、不 commit。"""

import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


class _RequestException(Exception):
    pass


from tests import _support  # noqa: E402

_support.bootstrap()

import weibo  # noqa: E402
from util.legacy import backends as legacy_backends  # noqa: E402  # noqa: E402

sys.modules.pop("json5", None)  # 避免影响 util.config_migrate 的降级解析测试


def new_wb() -> "weibo.Weibo":
    """构造不跑 __init__ 的 Weibo 实例，只注入被测方法需要的属性。"""
    return weibo.Weibo.__new__(weibo.Weibo)


class _FakeCookieJar:
    def __init__(self) -> None:
        self.data = {}

    def update(self, values) -> None:
        self.data.update(values)


class _FakeSession:
    """__init__ 预热会请求 m.weibo.cn：这里一律抛错（被 __init__ 捕获），保证不联网。"""

    def __init__(self) -> None:
        self.cookies = _FakeCookieJar()

    def get(self, *args, **kwargs):
        raise RuntimeError("测试环境不联网")

    def mount(self, *args, **kwargs):
        pass


class _FakeAdapter:
    def __init__(self, *args, **kwargs):
        pass


def _full_config(**overrides) -> dict:
    """满足 validate_config + __init__ 直接取值的最小完整 config。"""
    config = {
        "only_crawl_original": 0,
        "remove_html_tag": 0,
        "since_date": "2024-01-01",
        "write_mode": ["csv"],
        "user_id_list": ["123"],
        "download_comment": 0,
        "comment_max_download_count": 0,
        "download_repost": 0,
        "repost_max_download_count": 0,
        "original_pic_download": 0,
        "retweet_pic_download": 0,
        "original_video_download": 0,
        "retweet_video_download": 0,
        "page_weibo_count": 20,
        "anti_ban_config": {"enabled": True},
        "cookie": "",
    }
    config.update(overrides)
    return config


class AntiBanConfigMergeTest(unittest.TestCase):
    'anti_ban_config 缺键时与默认值合并，不再 KeyError。'

    def _make(self) -> "weibo.Weibo":
        with mock.patch.object(weibo.requests, "Session", _FakeSession), \
                mock.patch.object(weibo, "HTTPAdapter", _FakeAdapter):
            return weibo.Weibo(_full_config())

    def test_missing_keys_merged_with_defaults(self) -> None:
        wb = self._make()

        self.assertTrue(wb.anti_ban_enabled)
        self.assertEqual(wb.anti_ban_config["enabled"], True)
        self.assertEqual(wb.anti_ban_config["max_weibo_per_session"], 500)
        self.assertEqual(wb.anti_ban_config["batch_size"], 50)
        self.assertEqual(wb.anti_ban_config["batch_delay"], 30)
        self.assertEqual(wb.anti_ban_config["request_delay_min"], 8)
        self.assertEqual(wb.anti_ban_config["request_delay_max"], 15)
        self.assertEqual(wb.anti_ban_config["max_session_time"], 600)
        self.assertEqual(wb.anti_ban_config["max_api_errors"], 5)
        # 用户显式配置优先于默认值
        wb2 = self._make_with_overrides()
        self.assertEqual(wb2.anti_ban_config["batch_size"], 7)

    def _make_with_overrides(self) -> "weibo.Weibo":
        config = _full_config(anti_ban_config={"enabled": True, "batch_size": 7})
        with mock.patch.object(weibo.requests, "Session", _FakeSession), \
                mock.patch.object(weibo, "HTTPAdapter", _FakeAdapter):
            return weibo.Weibo(config)

    def test_get_pages_banner_does_not_raise_keyerror(self) -> None:
        wb = self._make()
        wb.get_user_info = lambda: 0
        wb.user = {"screen_name": "测试用户"}
        wb.user_config = wb.user_config_list[0]
        wb.get_page_count = lambda: None

        wb.get_pages()

        self.assertFalse(wb.crawl_success["success"])
        self.assertIn("页数计算失败", wb.crawl_success["reason"])


class GetPageCountTest(unittest.TestCase):
    'get_page_count 先校验配置再除法。'

    def test_valid_count(self) -> None:
        wb = new_wb()
        wb.user = {"statuses_count": 100}
        wb.page_weibo_count = 20
        self.assertEqual(wb.get_page_count(), 5)

    def test_invalid_page_weibo_count_returns_none(self) -> None:
        for invalid in ("20", 0, None, True, -5):
            with self.subTest(page_weibo_count=invalid):
                wb = new_wb()
                wb.user = {"statuses_count": 100}
                wb.page_weibo_count = invalid
                self.assertIsNone(wb.get_page_count())

    def test_invalid_statuses_count_returns_none(self) -> None:
        wb = new_wb()
        wb.user = {"statuses_count": "不是数字"}
        wb.page_weibo_count = 20
        self.assertIsNone(wb.get_page_count())


class WriteInfoWhitelistTest(unittest.TestCase):
    'get_write_info 只输出 WRITE_INFO_KEYS 白名单字段。'

    def test_extra_fields_excluded(self) -> None:
        wb = new_wb()
        wb.only_crawl_original = 1
        row = {key: "值-%s" % key for key in weibo.WRITE_INFO_KEYS}
        row.update(
            {
                "id": 1,
                "llm_analysis": {"sentiment": "positive"},
                "user_id": "123",
                "screen_name": "测试用户",
                "retweet": {"id": "9"},
                "live_photo_indices": [1],
                "links": ["https://example.com"],
            }
        )
        wb.weibo = [row]

        result = wb.get_write_info(0)

        self.assertEqual(len(result), 1)
        info = result[0]
        self.assertNotIn("llm_analysis", info)
        self.assertNotIn("user_id", info)
        self.assertNotIn("screen_name", info)
        self.assertNotIn("links", info)
        self.assertNotIn("live_photo_indices", info)
        self.assertEqual(set(info.keys()), set(weibo.WRITE_INFO_KEYS))
        self.assertEqual(len(info), len(weibo.WRITE_INFO_KEYS))
        self.assertEqual(info["id"], "1\t")

    def test_retweet_row_aligns_with_headers(self) -> None:
        """含转发微博的行必须与表头等长，源用户两列不丢失。"""
        wb = new_wb()
        wb.only_crawl_original = 0
        retweet = {key: "转-%s" % key for key in weibo.WRITE_INFO_KEYS}
        retweet.update({"id": "2", "user_id": "456", "screen_name": "源用户"})
        row = {key: "值-%s" % key for key in weibo.WRITE_INFO_KEYS}
        row.update({"id": 1, "retweet": retweet})
        wb.weibo = [row]

        info = wb.get_write_info(0)[0]
        headers = wb.get_result_headers()

        self.assertEqual(len(info), len(headers))
        values = list(info.values())
        # 顺序：18 列原创字段 + 是否原创 + 源用户id + 源用户昵称 + 18 列源微博
        self.assertFalse(values[18])
        self.assertEqual(values[19], "456")
        self.assertEqual(values[20], "源用户")
        self.assertEqual(values[-1], "转-" + weibo.WRITE_INFO_KEYS[-1])


class SqliteTableInitTest(unittest.TestCase):
    'SQLite 连接每次补建表，0 字节/旧库不再缺 comments、reposts 表。'

    def test_existing_empty_db_gets_all_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "weibodata.db"
            db_path.write_bytes(b"")  # 模拟 0 字节/旧版残缺库
            wb = new_wb()
            wb.sqlite_db_path = str(db_path)

            con = wb.get_sqlite_connection()
            try:
                names = {
                    row[0]
                    for row in con.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
            finally:
                con.close()

        for table in ("weibo", "comments", "reposts"):
            self.assertIn(table, names)


class BackoffCapTest(unittest.TestCase):
    """重试 3 次、单次退避上限 30 秒，累计约 60 秒。"""

    def test_retry_sleep_total_capped(self) -> None:
        class _FailSession:
            def __init__(self) -> None:
                self.calls = 0

            def get(self, *args, **kwargs):
                self.calls += 1
                raise weibo.RequestException("模拟网络失败")

        wb = new_wb()
        wb.user_config = {"user_id": "123"}
        wb.query = ""
        wb.page_weibo_count = 20
        wb.headers = {}
        wb.session = _FailSession()
        wb.get_random_headers = lambda: {}
        wb.calculate_dynamic_delay = lambda: 0
        wb.update_crawl_stats = lambda **kwargs: None
        wb.handle_captcha = lambda js: False
        sleeps = []

        with mock.patch.object(weibo, "sleep", sleeps.append):
            result = wb.get_weibo_json(1)

        self.assertEqual(result, {})
        self.assertEqual(wb.session.calls, 3)
        self.assertEqual(sleeps, [10, 20, 30])
        self.assertLessEqual(sum(sleeps), 60)


class StringToIntTest(unittest.TestCase):
    """string_to_int 兼容「1.2万+」「1000+」「1,234」等接口真实格式。"""

    def test_variants(self) -> None:
        wb = new_wb()
        cases = {
            "1.2万+": 12000,
            "1000+": 1000,
            "1,234": 1234,
            "1.5亿": 150000000,
            "": 0,
            "abc": 0,
            None: 0,
            7: 7,
            1.9: 1,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(wb.string_to_int(raw), expected)


class StandardizeDateTest(unittest.TestCase):
    """standardize_date 不再依赖 %c/locale，兼容英文格式与 ISO 格式。"""

    def test_english_and_iso_formats(self) -> None:
        wb = new_wb()
        for raw in (
            "Sat May 02 10:00:00 +0800 2020",
            "2020-05-02T10:00:00",
            "2020-05-02 10:00:00",
        ):
            with self.subTest(raw=raw):
                created_at, full_created_at = wb.standardize_date(raw)
                self.assertEqual(created_at, "2020-05-02T10:00:00")
                self.assertEqual(full_created_at, "2020-05-02 10:00:00")

    def test_relative_formats(self) -> None:
        wb = new_wb()
        created_at, full_created_at = wb.standardize_date("20分钟前")
        self.assertEqual(len(created_at), len("2024-01-01T00:00:00"))
        self.assertEqual(len(full_created_at), len("2024-01-01 00:00:00"))


class StandardizeInfoEncodingTest(unittest.TestCase):
    ""

    def test_no_stdout_encoding_dependency(self) -> None:
        class _AsciiStdout:
            encoding = "ascii"

            def write(self, text):
                pass

            def flush(self):
                pass

        wb = new_wb()
        with mock.patch.object(sys, "stdout", _AsciiStdout()):
            result = wb.standardize_info({"text": "中文\u200b内容\ufeff"})

        self.assertEqual(result["text"], "中文内容")


class ParseFailureCountTest(unittest.TestCase):
    """get_one_weibo 解析失败不静默，计入 crawl_success["parse_failures"]。"""

    def test_invalid_info_increments_counter(self) -> None:
        wb = new_wb()
        wb.crawl_success = {"attempted": True, "success": True}

        self.assertIsNone(wb.get_one_weibo({}))
        self.assertEqual(wb.crawl_success["parse_failures"], 1)

        self.assertIsNone(wb.get_one_weibo({"mblog": {}}))
        self.assertEqual(wb.crawl_success["parse_failures"], 2)


class UpdateUserConfigFileTest(unittest.TestCase):
    '更新用户列表第三列时按任意空白 split、保留其余列、原子写。'

    START_DATE = "2024-06-01T10:00:00"

    def _wb(self) -> "weibo.Weibo":
        wb = new_wb()
        wb.start_date = self.START_DATE
        wb.user_config = {"user_id": "123"}
        wb.user = {"screen_name": "昵称"}
        return wb

    def test_multi_space_row_keeps_columns_and_others_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "user_id_list.txt"
            path.write_text(
                "123  昵称  2020-01-01\n456 示例用户2 2021-01-01\n", encoding="utf-8"
            )

            self._wb().update_user_config_file(str(path))

            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[0].split(), ["123", "昵称", self.START_DATE])
            self.assertEqual(lines[1], "456 示例用户2 2021-01-01")  # 非目标行不变
            self.assertFalse(Path(str(path) + ".tmp").exists())  # 原子写无残留

    def test_four_column_row_keeps_query_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "user_id_list.txt"
            path.write_text("123 昵称 2020-01-01 梦想,希望\n", encoding="utf-8")

            self._wb().update_user_config_file(str(path))

            fields = path.read_text(encoding="utf-8").split()
            self.assertEqual(fields, ["123", "昵称", self.START_DATE, "梦想,希望"])

    def test_missing_start_date_leaves_file_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "user_id_list.txt"
            original = "123  昵称  2020-01-01\n"
            path.write_text(original, encoding="utf-8")
            wb = self._wb()
            wb.start_date = ""

            wb.update_user_config_file(str(path))

            self.assertEqual(path.read_text(encoding="utf-8"), original)


def _sqlite_weibo(weibo_id: str = "1", comments_count: int = 1) -> dict:
    """parse_sqlite_weibo 需要的完整微博 dict（字段与真实解析结果一致）。"""
    return {
        "user_id": "u123",
        "id": weibo_id,
        "bid": "b" + weibo_id,
        "screen_name": "测试用户",
        "text": "正文",
        "article_url": "",
        "topics": "",
        "at_users": "",
        "pics": "",
        "video_url": "",
        "live_photo_url": "",
        "location": "",
        "created_at": "2024-06-01T10:00:00",
        "full_created_at": "2024-06-01 10:00:00",
        "source": "微博",
        "attitudes_count": 0,
        "comments_count": comments_count,
        "reposts_count": 0,
        "retweet_id": "",
        "edited": False,
        "edit_count": 0,
    }


def _make_sqlite_wb(db_path: str) -> "weibo.Weibo":
    wb = new_wb()
    wb.sqlite_db_path = db_path
    wb.download_comment = 1
    wb.download_repost = 0
    wb.comment_max_download_count = 10
    wb.repost_max_download_count = 0
    return wb


class SqliteExceptionIsolationTest(unittest.TestCase):
    '单条微博的评论抓取异常不再冒泡，SQLite 连接必定关闭。'

    def test_comment_failure_does_not_raise_and_connection_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "weibodata.db"
            wb = _make_sqlite_wb(str(db_path))
            wb.weibo = [_sqlite_weibo()]

            def broken_comments(*args, **kwargs):
                raise RuntimeError("模拟评论接口失败")

            wb.get_weibo_comments = broken_comments
            captured = {}
            real_connect = wb.get_sqlite_connection

            def tracked_connect():
                con = real_connect()
                captured["con"] = con
                return con

            wb.get_sqlite_connection = tracked_connect

            wb.weibo_to_sqlite(0)

            self.assertIn("con", captured)
            # 连接已关闭：再执行 SQL 应报 ProgrammingError
            with self.assertRaises(sqlite3.ProgrammingError):
                captured["con"].execute("SELECT 1")
            # 微博本体已入库，说明失败只影响评论，不影响主数据
            con = sqlite3.connect(str(db_path))
            try:
                count = con.execute("SELECT COUNT(*) FROM weibo").fetchone()[0]
            finally:
                con.close()
            self.assertEqual(count, 1)

    def test_parse_sqlite_comment_tolerates_missing_fields(self) -> None:
        wb = new_wb()
        wb.remove_html_tag = 0
        wb.comment_pic_download = 0

        comment = wb.parse_sqlite_comment({"id": "c1"}, {"id": "w1"})

        self.assertEqual(comment["id"], "c1")
        self.assertEqual(comment["weibo_id"], "w1")
        self.assertEqual(comment["user_id"], "")
        self.assertEqual(comment["user_screen_name"], "")
        self.assertEqual(comment["text"], "")
        self.assertEqual(comment["pic_url"], "")

    def test_parse_sqlite_comment_ignores_empty(self) -> None:
        wb = new_wb()
        self.assertIsNone(wb.parse_sqlite_comment({}, {"id": "w1"}))
        self.assertIsNone(wb.parse_sqlite_comment(None, {"id": "w1"}))


class SqliteSleepConditionTest(unittest.TestCase):
    ""

    def test_no_sleep_for_fewer_than_20_comments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wb = _make_sqlite_wb(str(Path(tmp) / "weibodata.db"))
            wb.weibo = [_sqlite_weibo(str(i), comments_count=1) for i in range(1, 11)]
            wb.get_weibo_comments = lambda *args, **kwargs: None
            sleeps = []

            with mock.patch.object(weibo, "sleep", sleeps.append):
                wb.weibo_to_sqlite(0)

            self.assertEqual(sleeps, [])


class _FakeMysqlCursor:
    def __init__(self, capture: dict, fail: bool = False) -> None:
        self.capture = capture
        self.fail = fail

    def executemany(self, sql, params):
        self.capture["sql"] = sql
        self.capture["params"] = params
        if self.fail:
            raise RuntimeError("模拟 MySQL 写入失败")


class _FakeMysqlConnection:
    def __init__(self, capture: dict, fail: bool = False) -> None:
        self.capture = capture
        self.fail = fail

    def cursor(self):
        return _FakeMysqlCursor(self.capture, self.fail)

    def commit(self):
        self.capture["committed"] = True

    def rollback(self):
        self.capture["rolled_back"] = True

    def close(self):
        self.capture["closed"] = True


class FakePymysql:
    """最小 pymysql 替身：记录 connect/executemany 的入参，不连真实数据库。"""

    def __init__(self, capture: dict, fail: bool = False) -> None:
        self.capture = capture
        self.fail = fail
        self.module = types.ModuleType("pymysql")

        def connect(**kwargs):
            self.capture["connect_kwargs"] = kwargs
            return _FakeMysqlConnection(self.capture, self.fail)

        self.module.connect = connect

    def __enter__(self):
        self._saved = sys.modules.get("pymysql")
        sys.modules["pymysql"] = self.module
        return self

    def __exit__(self, *args):
        if self._saved is None:
            sys.modules.pop("pymysql", None)
        else:
            sys.modules["pymysql"] = self._saved


class MysqlInsertWhitelistTest(unittest.TestCase):
    'mysql_insert 只取建表列，附加字段不再触发 MySQL 1054。'

    def _wb(self) -> "weibo.Weibo":
        wb = new_wb()
        wb.mysql_config = None
        return wb

    def test_extra_columns_filtered_from_sql(self) -> None:
        capture = {}
        rows = [
            {
                "id": "1",
                "bid": "b1",
                "user_id": "u1",
                "screen_name": "测试用户",
                "text": "正文",
                "created_at": "2024-06-01 10:00:00",
                "live_photo_indices": [1],
                "links": ["https://example.com"],
                "full_created_at": "2024-06-01 10:00:00",
            }
        ]
        wb = self._wb()
        with FakePymysql(capture):
            ok = legacy_backends.mysql_insert(wb, {"host": "localhost"}, "weibo", rows)

        self.assertTrue(ok)
        self.assertNotIn("live_photo_indices", capture["sql"])
        self.assertNotIn("links", capture["sql"])
        self.assertNotIn("full_created_at", capture["sql"])
        self.assertIn("INSERT INTO weibo(", capture["sql"])
        self.assertEqual(len(capture["params"]), 1)
        self.assertEqual(capture["params"][0][0], "1")  # id 列排在白名单第一位
        self.assertTrue(capture.get("closed"))

    def test_executemany_failure_returns_false_and_rolls_back(self) -> None:
        capture = {}
        wb = self._wb()
        with FakePymysql(capture, fail=True):
            ok = legacy_backends.mysql_insert(wb, {"host": "localhost"}, "weibo", [{"id": "1"}])

        self.assertFalse(ok)
        self.assertTrue(capture.get("rolled_back"))
        self.assertTrue(capture.get("closed"))

    def test_empty_data_list_returns_true_without_connecting(self) -> None:
        capture = {}
        wb = self._wb()
        with FakePymysql(capture):
            self.assertTrue(legacy_backends.mysql_insert(wb, {"host": "localhost"}, "weibo", []))
        self.assertNotIn("connect_kwargs", capture)


if __name__ == "__main__":
    unittest.main()
