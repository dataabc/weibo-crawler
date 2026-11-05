"""本测试用标准库 `symtable` 做静态检查：对每个非测试源文件，找出「在函数里当
全局名使用、但模块级既没定义也没 import」的名字。这等价于 ruff 的 F821 规则，
不依赖第三方工具，可在 unittest 里零成本运行。

维护提示：如果将来使用 `globals()` 动态注入名字，需要在本测试里显式放行。"""

import builtins
import symtable
import sys
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_BUILTIN_NAMES = set(dir(builtins)) | {
    "__file__",
    "__name__",
    "__doc__",
    "__package__",
    "__builtins__",
    "__annotations__",
    "__class__",
    "__spec__",
    "__loader__",
    "__dict__",
}

_EXCLUDE_DIRS = {".git", "__pycache__", "tests", "Output", ".venv", "venv"}


def _iter_source_files():
    for path in sorted(_PROJECT_ROOT.rglob("*.py")):
        relative = path.relative_to(_PROJECT_ROOT)
        # （代码备份副本），不在检查范围内，否则会把备份里的旧代码当成本仓问题。
        if any(
            part in _EXCLUDE_DIRS or part.startswith(".")
            for part in relative.parts
        ):
            continue
        yield path


def _collect_undefined_names(table, module_globals, found):
    for symbol in table.get_symbols():
        name = symbol.get_name()
        if (
            symbol.is_global()
            and not symbol.is_assigned()
            and name not in module_globals
            and name not in _BUILTIN_NAMES
        ):
            found.add(name)
    for child in table.get_children():
        _collect_undefined_names(child, module_globals, found)


def _scan_file(path):
    source = path.read_text(encoding="utf-8")
    table = symtable.symtable(source, str(path), "exec")
    module_globals = set(table.get_identifiers())
    found = set()
    for child in table.get_children():
        _collect_undefined_names(child, module_globals, found)
    return sorted(found)


class UndefinedNameGateTest(unittest.TestCase):
    """全仓非测试源码不允许出现未定义全局名。"""

    def test_no_undefined_global_names(self):
        problems = {}
        for path in _iter_source_files():
            missing = _scan_file(path)
            if missing:
                problems[str(path.relative_to(_PROJECT_ROOT))] = missing
        self.assertEqual(
            problems,
            {},
            "发现未定义全局名（重构漏 import 的典型症状）：\n"
            + "\n".join(f"  {name}: {missing}" for name, missing in problems.items()),
        )


if __name__ == "__main__":
    unittest.main()
