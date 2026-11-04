"""util/config/config_loader.py 的 apply/validate 快照测试。

说明：测试不联网——Session 预热会被 try/except 兜底；user_id_list 用 list 形式
避免读取真实用户列表文件。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests import _support  # noqa: E402

_support.bootstrap()

import weibo  # noqa: E402
from util.config import config_loader  # noqa: E402


class _FakeCookieJar:
    def __init__(self):
        self.data = {}

    def update(self, other):
        self.data.update(other or {})

    def __iter__(self):
        return iter([])


class _FakeSession:
    """最小 Session：满足 apply() 的 cookies/mount/get 调用，不联网。"""

    def __init__(self, *args, **kwargs):
        self.cookies = _FakeCookieJar()
        self.headers = {}
        self.mounted = {}

    def get(self, *args, **kwargs):
        raise OSError("测试环境不联网")

    def mount(self, prefix, adapter):
        self.mounted[prefix] = adapter


def _template_config():
    config = _support.load_json5_file(
        os.path.join(_support.PROJECT_ROOT, "config", "config.json.example")
    )
    # 用 list 形式，避免 apply 去读 user_id_list.txt 文件
    config["user_id_list"] = ["1234567890"]
    config["cookie"] = ""
    config.pop("llm_config", None)
    return config


class ConfigLoaderApplyTest(unittest.TestCase):
    def setUp(self):
        # 注意：不要在用例里重复 _support.install_import_stubs()——那会重新安装
        # json5 stub，污染依赖真实 JSON5 解析的 test_config_migrate（discover 共进程）。
        # apply() 内部 requests.Session() 需要 cookies/mount 能力。
        # 注意：必须恢复全局 Session，否则会污染后续测试（discover 共用同一进程）。
        self._saved_session = sys.modules["requests"].Session
        sys.modules["requests"].Session = _FakeSession
        self.config = _template_config()
        self.wb = weibo.Weibo.__new__(weibo.Weibo)

    def tearDown(self):
        sys.modules["requests"].Session = self._saved_session

    def test_apply_from_template_sets_core_attributes(self):
        config_loader.apply(self.wb, self.config)

        # 写入模式与目录
        self.assertEqual(self.wb.write_mode, self.config["write_mode"])
        self.assertEqual(self.wb.output_directory, self.config["output_directory"])

        self.assertIn(
            self.wb.markdown_sort_order, ("new_to_old", "old_to_new")
        )
        self.assertIn(
            self.wb.markdown_image_order,
            ("live_photo_first", "image_first_interleaved"),
        )

        # 评论 / 存档核对 / 报告开关
        self.assertEqual(
            self.wb.comment_refresh_scope, self.config.get("comment_refresh_scope", "all")
        )
        self.assertEqual(
            self.wb.comment_refresh_limit,
            int(self.config.get("comment_refresh_limit", 300)),
        )
        self.assertIsInstance(self.wb.archive_check_enabled, int)
        self.assertIsInstance(self.wb.crawl_summary_enabled, int)
        self.assertIsInstance(self.wb.media_check_enabled, int)

        # 运行期状态初值
        self.assertEqual(self.wb.got_count, 0)
        self.assertEqual(self.wb.weibo, [])
        self.assertEqual(self.wb.weibo_id_list, [])
        self.assertEqual(self.wb.long_sleep_count_before_each_user, 0)
        self.assertEqual(self.wb.user_config_file_path, "")
        self.assertEqual(len(self.wb.user_config_list), 1)
        self.assertEqual(
            self.wb.user_config_list[0]["user_id"], "1234567890"
        )
        self.assertEqual(self.wb.user_config_list[0]["since_date"], self.wb.since_date)

        # Session 与请求头
        self.assertIsInstance(self.wb.session, _FakeSession)
        self.assertIn("Referer", self.wb.headers)
        self.assertIn("http://", self.wb.session.mounted)
        self.assertIn("https://", self.wb.session.mounted)

        # 防封禁默认值与统计字典
        self.assertIn("enabled", self.wb.anti_ban_config)
        self.assertIsInstance(self.wb.crawl_stats, dict)
        for key in ("weibo_count", "request_count", "api_errors"):
            self.assertIn(key, self.wb.crawl_stats)

    def test_apply_keeps_byte_and_count_settings(self):
        config_loader.apply(self.wb, self.config)
        self.assertEqual(
            self.wb.remove_html_tag, int(self.config.get("remove_html_tag", 1))
        )
        self.assertEqual(
            self.wb.only_crawl_original, int(self.config.get("only_crawl_original", 0))
        )
        self.assertEqual(
            self.wb.store_binary_in_sqlite,
            int(self.config.get("store_binary_in_sqlite", 0)),
        )

    def test_validate_accepts_template(self):
        # 模板配置应该通过 validate（不抛 SystemExit）
        config_loader.validate(self.wb, _template_config())

    def test_validate_rejects_invalid_markdown_split_by(self):
        config = _template_config()
        config["markdown_split_by"] = "week"
        with self.assertRaises(SystemExit):
            config_loader.validate(self.wb, config)

    def test_validate_rejects_unknown_write_mode(self):
        config = _template_config()
        config["write_mode"] = ["unknown_mode"]
        with self.assertRaises(SystemExit):
            config_loader.validate(self.wb, config)

    def test_validate_warns_but_accepts_archived_write_modes(self):
        # mysql/mongo/post 已归档：仍可启动（告警跳过），不能 sys.exit
        config = _template_config()
        config["write_mode"] = ["markdown", "mysql"]
        config_loader.validate(self.wb, config)


if __name__ == "__main__":
    unittest.main()
