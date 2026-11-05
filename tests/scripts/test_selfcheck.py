"""scripts/weibo_selfcheck.py 的部署自检测试。"""

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests import _support  # noqa: E402

_support.bootstrap()

_SCRIPTS_DIR = os.path.join(_support.PROJECT_ROOT, "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import weibo_selfcheck as selfcheck  # noqa: E402


def _copy_runtime_tree(target: str) -> None:
    ''
    for name in ("util", "scripts"):
        shutil.copytree(
            os.path.join(_support.PROJECT_ROOT, name),
            os.path.join(target, name),
            ignore=shutil.ignore_patterns("__pycache__", "._*"),
        )
    shutil.copy2(
        os.path.join(_support.PROJECT_ROOT, "weibo.py"),
        os.path.join(target, "weibo.py"),
    )


class SelfcheckTest(unittest.TestCase):
    def test_iter_required_files_covers_key_modules(self):
        files = set(selfcheck._iter_required_files())
        for name in (
            "weibo.py",
            "__main__.py",
            "const.py",
            "util/core/project.py",
            "util/report/archive_check.py",
            "util/config/config_loader.py",
            "scripts/weibo_selfcheck.py",
        ):
            self.assertIn(name, files, f"动态清单缺少 {name}")
        # AppleDouble 与缓存不应进入清单
        self.assertFalse(any(os.path.basename(name).startswith("._") for name in files))
        self.assertFalse(any("__pycache__" in name for name in files))

    def test_check_files_passes_in_workspace(self):
        self.assertEqual(selfcheck.check_files(), [])

    def test_check_imports_passes_with_test_stubs(self):
        # 已由 _support 安装 requests/lxml 等替身，关键模块应可导入
        self.assertEqual(selfcheck.check_imports(), [])

    def test_runtime_consistency_detects_missing_and_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            notes, errors = selfcheck.check_runtime_consistency(tmp)
            self.assertTrue(errors)
            self.assertTrue(any("现场缺少" in error for error in errors))

            # 2) 完整复制：应一致
            _copy_runtime_tree(tmp)
            notes, errors = selfcheck.check_runtime_consistency(tmp)
            self.assertEqual(errors, [], errors)
            self.assertTrue(any("一致" in note for note in notes))

            # 3) 改动一个文件：应报版本不一致
            target = os.path.join(tmp, "util", "core", "project.py")
            with open(target, "a", encoding="utf-8") as handle:
                handle.write("\n# changed in test\n")
            notes, errors = selfcheck.check_runtime_consistency(tmp)
            self.assertTrue(any("版本不一致" in error for error in errors), errors)

    def test_main_returns_zero_in_workspace(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = selfcheck.main([])
        self.assertEqual(code, 0, buffer.getvalue())
        self.assertIn("自检通过", buffer.getvalue())


    def test_check_config_reads_config_subdir(self):
        """配置已迁移到 config/ 子目录时，自检也要能读到。"""
        import json
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "config"))
            payload = {key: 0 for key in selfcheck.NEW_CONFIG_KEYS}
            with open(
                os.path.join(tmp, "config", "config.json"), "w", encoding="utf-8"
            ) as handle:
                json.dump(payload, handle)
            with mock.patch.object(selfcheck, "PROJECT_DIR", tmp):
                notes, errors = selfcheck.check_config()
            self.assertEqual(errors, [])
            self.assertTrue(any("齐全" in note for note in notes), notes)


if __name__ == "__main__":
    unittest.main()
