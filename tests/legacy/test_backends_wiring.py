"""MySQL / MongoDB / POST 后端恢复后的接线测试。

这些后端保留但**未在真实服务上验证**（docs/未验证功能.md）。本文件只验证：
1. ``Weibo`` 上恢复了上游同名薄封装，且指向 ``util/legacy/backends``；
2. ``write_data`` / ``user_to_database`` 在 write_mode 含对应值时**会调用**它们；
3. 依赖缺失/连接失败时**不退出主流程**（返回 False，不 raise / 不 sys.exit）；
4. ``validate`` 不再把这些 write_mode 当废弃值拒绝。"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests import _support  # noqa: E402

_support.bootstrap()

import weibo  # noqa: E402
from util.core import write_pipeline  # noqa: E402
from util.config import config_loader  # noqa: E402
from util.legacy import backends as legacy_backends  # noqa: E402


def make_wb(**attrs):
    wb = weibo.Weibo.__new__(weibo.Weibo)
    wb.write_mode = ["markdown"]
    wb.readonly_mode = False
    wb.got_count = 1
    wb.weibo = [{"id": "1"}]
    wb.user = {"screen_name": "测试"}
    wb.run_failures = []
    wb.user_config = {"user_id": "1"}
    wb.mysql_config = None
    wb.mongodb_URI = ""
    wb.post_config = {"api_url": "https://api.example.com", "api_token": ""}
    # write_data 后续的媒体/编辑步骤：测试里关掉，避免引入无关依赖
    wb.original_pic_download = 0
    wb.original_video_download = 0
    wb.original_live_photo_download = 0
    wb.only_crawl_original = 1
    wb.retweet_pic_download = 0
    wb.retweet_video_download = 0
    wb.retweet_live_photo_download = 0
    wb.write_markdown = lambda wrote_count: None
    wb._apply_edit_updates = lambda wrote_count: None
    for key, value in attrs.items():
        setattr(wb, key, value)
    return wb


class WrapperExistenceTest(unittest.TestCase):
    """Weibo 恢复的上游同名方法必须存在且指向归档实现。"""

    def test_wrappers_exist_and_delegate(self):
        wb = make_wb()
        for name in (
            "user_to_mysql",
            "user_to_mongodb",
            "weibo_to_mysql",
            "weibo_to_mongodb",
            "write_post",
            "info_to_mongodb",
            "mysql_create",
            "mysql_create_database",
            "mysql_create_table",
            "mysql_insert",
        ):
            self.assertTrue(callable(getattr(wb, name, None)), f"缺少 Weibo.{name}")

    def test_user_to_mysql_delegates(self):
        wb = make_wb()
        with mock.patch.object(legacy_backends, "user_to_mysql", return_value=True) as fn:
            wb.user_to_mysql()
        fn.assert_called_once_with(wb)

    def test_weibo_to_mysql_delegates_with_count(self):
        wb = make_wb()
        with mock.patch.object(legacy_backends, "weibo_to_mysql", return_value=True) as fn:
            wb.weibo_to_mysql(2)
        fn.assert_called_once_with(wb, 2)

    def test_write_post_delegates(self):
        wb = make_wb()
        with mock.patch.object(legacy_backends, "write_post", return_value=None) as fn:
            wb.write_post(0)
        fn.assert_called_once_with(wb, 0)


class WriteDataBranchTest(unittest.TestCase):
    """write_data 按 write_mode 调用对应后端。"""

    def _run(self, write_mode):
        wb = make_wb(write_mode=write_mode)
        wb._run_write_step = lambda name, func, *args: func(*args)  # 直接执行，便于断言
        with mock.patch.object(legacy_backends, "weibo_to_mysql", return_value=True) as mysql_fn, \
             mock.patch.object(legacy_backends, "weibo_to_mongodb", return_value=True) as mongo_fn, \
             mock.patch.object(legacy_backends, "write_post", return_value=None) as post_fn:
            write_pipeline.write_data(wb, 0)
        return mysql_fn, mongo_fn, post_fn

    def test_mysql_branch_called(self):
        mysql_fn, mongo_fn, post_fn = self._run(["mysql"])
        mysql_fn.assert_called_once()
        mongo_fn.assert_not_called()
        post_fn.assert_not_called()

    def test_mongodb_alias_both_call(self):
        for mode in ("mongo", "mongodb"):
            mysql_fn, mongo_fn, _ = self._run([mode])
            mongo_fn.assert_called_once()
            mysql_fn.assert_not_called()

    def test_post_branch_called(self):
        _, _, post_fn = self._run(["post"])
        post_fn.assert_called_once()

    def test_markdown_only_does_not_touch_legacy_backends(self):
        mysql_fn, mongo_fn, post_fn = self._run(["markdown"])
        mysql_fn.assert_not_called()
        mongo_fn.assert_not_called()
        post_fn.assert_not_called()


class UserToDatabaseBranchTest(unittest.TestCase):
    def test_mysql_and_mongo_user_write_called(self):
        wb = make_wb(write_mode=["mysql", "mongo"])
        wb.user_to_csv = mock.Mock()
        wb._run_write_step = lambda name, func, *args: func(*args)
        with mock.patch.object(legacy_backends, "user_to_mysql", return_value=True) as mysql_fn, \
             mock.patch.object(legacy_backends, "user_to_mongodb", return_value=True) as mongo_fn:
            wb.user_to_database()
        mysql_fn.assert_called_once()
        mongo_fn.assert_called_once()

    def test_readonly_mode_skips_all(self):
        wb = make_wb(write_mode=["mysql"], readonly_mode=True)
        wb.user_to_csv = mock.Mock()
        wb.user_to_database()
        wb.user_to_csv.assert_not_called()


class BackendFailureIsNonFatalTest(unittest.TestCase):
    """依赖缺失/连接失败：记录 warning、返回 False，不退出主流程。"""

    def test_mongodb_without_pymongo_does_not_exit(self):
        wb = make_wb()
        saved = sys.modules.pop("pymongo", None)
        try:
            with mock.patch.dict(sys.modules, {"pymongo": None}):
                result = legacy_backends.info_to_mongodb(wb, "weibo", [{"id": "1"}])
        finally:
            if saved is not None:
                sys.modules["pymongo"] = saved
        self.assertFalse(result)

    def test_mysql_without_pymysql_does_not_exit(self):
        wb = make_wb()
        with mock.patch.dict(sys.modules, {"pymysql": None}):
            ok_db = legacy_backends.mysql_create_database(wb, {"host": "x"}, "SELECT 1")
            ok_table = legacy_backends.mysql_create_table(wb, {"host": "x"}, "SELECT 1")
        self.assertFalse(ok_db)
        self.assertFalse(ok_table)


class ValidateAcceptsRestoredModesTest(unittest.TestCase):
    def _template(self):
        config = _support.load_json5_file(
            os.path.join(_support.PROJECT_ROOT, "config", "config.json.example")
        )
        config["user_id_list"] = ["1234567890"]
        config["cookie"] = ""
        return config

    def test_mysql_mongo_post_are_valid_write_modes(self):
        wb = make_wb()
        for mode in ("mysql", "mongo", "mongodb", "post"):
            config = self._template()
            config["write_mode"] = [mode]
            try:
                config_loader.validate(wb, config)
            except SystemExit as exc:  # pragma: no cover - 失败时帮助定位
                self.fail(f"write_mode={mode} 被拒绝: {exc}")

    def test_truly_unknown_mode_still_rejected(self):
        from util.config import config_loader as loader

        wb = make_wb()
        config = self._template()
        config["write_mode"] = ["not_a_mode"]
        with self.assertRaises(SystemExit):
            loader.validate(wb, config)


if __name__ == "__main__":
    unittest.main()
