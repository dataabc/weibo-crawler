"""util.config_migrate 的单元测试。

直接运行：python3 test_config_migrate.py
"""

import copy
import json
import os
import stat
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from util.config_migrate import (
    DEFAULT_RENAME_MAP,
    MigrationResult,
    format_summary,
    load_config_file,
    migrate_config,
    write_config_with_backup,
)


class MigrateConfigTest(unittest.TestCase):
    """配置合并、改名和结果记录测试。"""

    def test_missing_top_level_keys_are_filled_without_overwriting(self):
        """缺顶层键时补齐，用户已有值优先。"""
        template = {
            "only_crawl_original": 0,
            "user_id_list": ["template-user"],
            "since_date": "2026-01-01",
            "write_mode": ["markdown"],
        }
        user = {
            "only_crawl_original": 1,
            "user_id_list": ["user-123"],
        }

        result = migrate_config(user, template)

        self.assertEqual(result.added_keys, ["since_date", "write_mode"])
        self.assertEqual(result.merged["only_crawl_original"], 1)
        self.assertEqual(result.merged["user_id_list"], ["user-123"])
        self.assertEqual(result.merged["since_date"], "2026-01-01")
        self.assertEqual(result.merged["write_mode"], ["markdown"])
        self.assertEqual(
            list(result.merged),
            ["only_crawl_original", "user_id_list", "since_date", "write_mode"],
        )
        self.assertEqual(result.unknown_keys, [])

    def test_missing_nested_keys_are_filled_recursively(self):
        """缺嵌套键时递归补齐；整块缺失只记父路径。"""
        template = {
            "markdown_preview": {
                "link_preview": 0,
                "inline_image_preview": 1,
            },
            "markdown_file_dir": {
                "original_image_dir": "img",
            },
        }
        user = {
            "markdown_preview": {
                "link_preview": 1,
            },
        }

        result = migrate_config(user, template)

        self.assertEqual(
            result.added_keys,
            [
                "markdown_preview.inline_image_preview",
                "markdown_file_dir",
            ],
        )
        self.assertEqual(result.merged["markdown_preview"]["link_preview"], 1)
        self.assertEqual(
            result.merged["markdown_preview"]["inline_image_preview"], 1
        )
        self.assertEqual(
            result.merged["markdown_file_dir"]["original_image_dir"], "img"
        )

    def test_legacy_keys_are_renamed(self):
        """filter/result_dir_name 正确改名。"""
        template = {
            "only_crawl_original": 0,
            "user_id_as_folder_name": 0,
        }
        user = {
            "filter": 1,
            "result_dir_name": 1,
        }

        result = migrate_config(user, template, DEFAULT_RENAME_MAP)

        self.assertEqual(
            result.renamed_keys,
            [
                ("filter", "only_crawl_original"),
                ("result_dir_name", "user_id_as_folder_name"),
            ],
        )
        self.assertNotIn("filter", result.merged)
        self.assertNotIn("result_dir_name", result.merged)
        self.assertEqual(result.merged["only_crawl_original"], 1)
        self.assertEqual(result.merged["user_id_as_folder_name"], 1)
        self.assertEqual(result.added_keys, [])

    def test_archive_check_keys_migrated(self):
        """旧检查报告键与上一版功能键都迁移到 weibo_archive_check。"""
        template = {"weibo_archive_check": 0, "invisible_weibo_dirs": ["已不可见"]}
        for old_key in ("missing_weibo_report", "weibo_delete_save_check"):
            with self.subTest(old_key=old_key):
                result = migrate_config({old_key: 1}, template, DEFAULT_RENAME_MAP)
                self.assertEqual(result.merged["weibo_archive_check"], 1)
                self.assertNotIn(old_key, result.merged)

    def test_existing_new_key_wins_and_old_key_is_removed(self):
        """新旧键同时存在时保留新键，旧键不重复、不残留。"""
        template = {"only_crawl_original": 0}
        user = {
            "filter": 0,
            "only_crawl_original": 1,
        }

        result = migrate_config(user, template, DEFAULT_RENAME_MAP)

        self.assertEqual(result.merged, {"only_crawl_original": 1})
        self.assertNotIn("filter", result.merged)
        self.assertEqual(result.renamed_keys, [])
        self.assertEqual(result.added_keys, [])
        self.assertEqual(result.unknown_keys, [])

    def test_unknown_keys_are_preserved_at_the_end(self):
        """未知顶层键保留，并追加在模板键之后。"""
        template = {
            "only_crawl_original": 0,
            "user_id_list": [],
        }
        user = {
            "only_crawl_original": 1,
            "user_id_list": [],
            "legacy_note": {"text": "保留"},
            "extra": True,
        }

        result = migrate_config(user, template)

        self.assertEqual(result.unknown_keys, ["legacy_note", "extra"])
        self.assertEqual(
            list(result.merged),
            ["only_crawl_original", "user_id_list", "legacy_note", "extra"],
        )
        self.assertEqual(result.merged["legacy_note"], {"text": "保留"})
        self.assertTrue(result.merged["extra"])

    def test_inputs_are_not_modified_and_result_is_detached(self):
        """迁移不修改入参，返回的新对象也不与入参共享容器。"""
        template = {
            "write_mode": ["markdown"],
            "markdown_preview": {
                "link_preview": 0,
                "inline_image_preview": 1,
            },
        }
        user = {
            "write_mode": ["csv"],
            "markdown_preview": {
                "link_preview": 1,
            },
        }
        template_before = copy.deepcopy(template)
        user_before = copy.deepcopy(user)

        result = migrate_config(user, template)

        self.assertEqual(user, user_before)
        self.assertEqual(template, template_before)
        self.assertIsNot(result.merged, user)
        self.assertIsNot(result.merged["markdown_preview"], user["markdown_preview"])

        # 修改结果不能反向污染用户配置或模板。
        result.merged["write_mode"].append("json")
        result.merged["markdown_preview"]["inline_image_preview"] = 0
        self.assertEqual(user, user_before)
        self.assertEqual(template, template_before)


class ConfigFileTest(unittest.TestCase):
    """配置文件读写与备份测试。"""

    def test_load_config_file_supports_json5_comments(self):
        """能解析带行注释、块注释和尾随逗号的 JSON5。"""
        content = """{
            // 行注释
            "user_id_list": ["123"],
            "api_url": "https://example.com/a//b",
            "note": "字符串里的 /* 不是注释 */",
            "enabled": true,
            "items": [1, 2,],
            /* 块注释 */
        }
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "config.json")
            with open(path, "w", encoding="utf-8") as config_file:
                config_file.write(content)

            config = load_config_file(path)

        self.assertEqual(config["user_id_list"], ["123"])
        self.assertEqual(config["api_url"], "https://example.com/a//b")
        self.assertEqual(config["note"], "字符串里的 /* 不是注释 */")
        self.assertTrue(config["enabled"])
        self.assertEqual(config["items"], [1, 2])

    def test_load_config_file_errors_include_path(self):
        """文件缺失、解析失败或根节点不是对象时均报 ValueError 且带路径。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_path = os.path.join(temp_dir, "missing.json")
            with self.assertRaises(ValueError) as missing_context:
                load_config_file(missing_path)
            self.assertIn(missing_path, str(missing_context.exception))

            bad_path = os.path.join(temp_dir, "bad.json")
            with open(bad_path, "w", encoding="utf-8") as config_file:
                config_file.write("{ this is not json }")
            with self.assertRaises(ValueError) as bad_context:
                load_config_file(bad_path)
            self.assertIn(bad_path, str(bad_context.exception))

            array_path = os.path.join(temp_dir, "array.json")
            with open(array_path, "w", encoding="utf-8") as config_file:
                config_file.write("[1, 2, 3]")
            with self.assertRaises(ValueError) as array_context:
                load_config_file(array_path)
            self.assertIn(array_path, str(array_context.exception))

    def test_write_config_with_backup_and_reload(self):
        """生成备份、保留原内容、写回可再次读取的规范化 JSON。"""
        original_text = """{
    "filter": 1,
    // 用户自己的注释
    "custom": "保留"
}
"""
        template = {
            "only_crawl_original": 0,
            "user_id_as_folder_name": 0,
            "since_date": "2026-01-01",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "config.json")
            with open(path, "w", encoding="utf-8") as config_file:
                config_file.write(original_text)
            os.chmod(path, 0o640)

            user_config = load_config_file(path)
            result = migrate_config(user_config, template, DEFAULT_RENAME_MAP)
            backup_path = write_config_with_backup(path, result.merged)

            self.assertTrue(os.path.isfile(backup_path))
            self.assertEqual(os.path.dirname(backup_path), temp_dir)
            self.assertTrue(
                os.path.basename(backup_path).startswith("config.json.bak.")
            )

            with open(backup_path, "r", encoding="utf-8") as backup_file:
                self.assertEqual(backup_file.read(), original_text)

            reloaded = load_config_file(path)
            self.assertEqual(reloaded, result.merged)
            self.assertEqual(reloaded["only_crawl_original"], 1)
            self.assertEqual(reloaded["custom"], "保留")

            with open(path, "r", encoding="utf-8") as config_file:
                written_text = config_file.read()
            self.assertEqual(json.loads(written_text), result.merged)
            self.assertTrue(written_text.endswith("\n"))
            self.assertNotIn("//", written_text)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o640)

    def test_backup_name_collision_adds_sequence(self):
        """同一时间戳的备份已存在时，追加序号避免覆盖。"""
        original_text = '{"filter": 1}\n'

        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "config.json")
            with open(path, "w", encoding="utf-8") as config_file:
                config_file.write(original_text)

            base_path = (
                f"{path}.bak.20261024-065218"
            )
            with open(base_path, "w", encoding="utf-8") as backup_file:
                backup_file.write("较早的备份")
            with open(f"{base_path}.1", "w", encoding="utf-8") as backup_file:
                backup_file.write("更早的备份")

            class FixedDateTime:
                @classmethod
                def now(cls):
                    return datetime(2026, 10, 24, 6, 52, 18)

            with patch("util.config_migrate.datetime", FixedDateTime):
                backup_path = write_config_with_backup(path, {"filter": 1})

            self.assertEqual(backup_path, f"{base_path}.2")
            with open(backup_path, "r", encoding="utf-8") as backup_file:
                self.assertEqual(backup_file.read(), original_text)


class FormatSummaryTest(unittest.TestCase):
    """中文摘要格式测试。"""

    def test_summary_for_no_changes(self):
        """没有新增、改名或未知键时提示无需迁移。"""
        result = MigrationResult(merged={})
        self.assertEqual(format_summary(result), "配置已是最新，无需迁移")

    def test_summary_with_all_change_types(self):
        """所有变更类型按固定顺序输出。"""
        result = MigrationResult(
            merged={},
            added_keys=["since_date", "markdown_preview.inline_image_preview"],
            renamed_keys=[("filter", "only_crawl_original")],
            unknown_keys=["custom"],
        )

        expected = (
            "新增配置 2 项：since_date, markdown_preview.inline_image_preview\n"
            "旧键改名 1 项：filter → only_crawl_original\n"
            "未知键 1 项（已保留）：custom"
        )
        self.assertEqual(format_summary(result), expected)

    def test_summary_with_only_unknown_keys(self):
        """仅有未知键时也要报告，而不是说无需迁移。"""
        result = MigrationResult(merged={}, unknown_keys=["custom"])
        self.assertEqual(format_summary(result), "未知键 1 项（已保留）：custom")


class RenameMapTest(unittest.TestCase):
    """rename_map 为空时的兼容性。"""

    def test_none_and_empty_rename_maps_are_safe(self):
        """rename_map 为 None 或空字典时不报错，也不执行改名。"""
        template = {"only_crawl_original": 0}
        user = {"filter": 1}

        for rename_map in (None, {}):
            with self.subTest(rename_map=rename_map):
                result = migrate_config(user, template, rename_map)
                self.assertEqual(result.merged["filter"], 1)
                self.assertIn("only_crawl_original", result.merged)
                self.assertEqual(result.renamed_keys, [])
                self.assertEqual(result.added_keys, ["only_crawl_original"])
                self.assertEqual(result.unknown_keys, ["filter"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
