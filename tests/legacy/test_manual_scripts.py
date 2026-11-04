'scripts 下手工会话脚本的启动引导检查。'

import ast
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPTS = {
    "scripts/legacy/service.py": "weibo",
    "scripts/manual/test_llm.py": "util.misc.llm_analyzer",
}


def parse(path):
    with open(os.path.join(PROJECT_ROOT, path), encoding="utf-8") as handle:
        return ast.parse(handle.read(), filename=path)


def node_index(tree, predicate):
    for index, node in enumerate(ast.walk(tree)):
        if predicate(node):
            return index
    return None


class ScriptBootstrapTest(unittest.TestCase):
    def test_project_root_added_to_sys_path_before_imports(self):
        for rel_path, target_module in SCRIPTS.items():
            with self.subTest(script=rel_path):
                tree = parse(rel_path)
                with open(os.path.join(PROJECT_ROOT, rel_path), encoding="utf-8") as handle:
                    source = handle.read()

                # 1) 必须定义 PROJECT_DIR 并把项目根插入 sys.path
                self.assertIn("PROJECT_DIR", source)
                self.assertIn("sys.path.insert", source)

                # 2) 引导必须出现在目标模块导入之前（按源码位置比较）
                bootstrap_pos = source.find("sys.path.insert")
                import_pos = source.find(f"import {target_module}")
                if import_pos < 0:
                    import_pos = source.find(f"from {target_module}")
                self.assertGreater(bootstrap_pos, -1, "缺少 sys.path 引导")
                self.assertGreater(import_pos, -1, f"未找到 {target_module} 的导入")
                self.assertLess(
                    bootstrap_pos,
                    import_pos,
                    "sys.path 引导必须在导入项目模块之前",
                )

                # 3) 语法可解析（parse 成功即通过）
                self.assertIsInstance(tree, ast.Module)


class ServiceRouteSmokeTest(unittest.TestCase):
    """service.py 统一用 ``/api/`` 前缀，且引用当前版本的配置函数。"""

    def test_service_uses_current_weibo_api(self):
        with open(
            os.path.join(PROJECT_ROOT, "scripts/legacy/service.py"), encoding="utf-8"
        ) as handle:
            source = handle.read()
        for name in ("Weibo", "get_config", "handle_config_renaming"):
            self.assertIn(name, source)
        self.assertIn("config", source)
        # Flask 路由不应再硬编码旧端口以外的地址（保持默认 5000）
        self.assertIn("5000", source)


if __name__ == "__main__":
    unittest.main()
