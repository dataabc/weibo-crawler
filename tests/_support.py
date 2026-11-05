"""测试公共支持：第三方依赖 stub、项目路径、日志隔离。

用法（测试文件顶部）：

    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from tests import _support

    _support.bootstrap()
    import weibo  # noqa: E402

约定（与原有测试写法保持一致，避免改变导入顺序语义）：

- **只补缺失的 stub，不覆盖已安装模块**：``unittest discover`` 会先导入全部测试
  模块再运行，无条件覆盖 ``requests.Session`` 会污染 ``test_download_records``
  等依赖自己 Session 的用例；
- 需要自定义 Session 的测试，在 ``bootstrap()`` 之后自行覆盖
  （参考 ``tests/media/test_download_records.py``）；
- 日志目录重定向到临时目录（``WEIBO_LOG_DIR``），测试不再写仓库 ``log/``。"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

_LOG_DIR: str | None = None


def default_stub_classes():
    """返回默认的 (Session, RequestException, Adapter) 替身类。"""

    class _Session:
        def __init__(self, *args, **kwargs):
            pass

    class _RequestException(Exception):
        pass

    class _Adapter:
        def __init__(self, *args, **kwargs):
            pass

    return _Session, _RequestException, _Adapter


def isolate_logs() -> str:
    """把 weibo 的日志文件重定向到临时目录（幂等），返回目录路径。

    必须在 ``import weibo`` 之前调用；``weibo._init_logging`` 读取
    ``WEIBO_LOG_DIR`` 后把 FileHandler 指向该目录。
    """
    global _LOG_DIR
    if _LOG_DIR is None:
        _LOG_DIR = tempfile.mkdtemp(prefix="weibo-tests-logs-")
        os.environ["WEIBO_LOG_DIR"] = _LOG_DIR
    return _LOG_DIR


def install_import_stubs(session_class=None) -> None:
    """幂等安装第三方依赖替身（只补缺失，不覆盖已安装模块）。"""

    def add(name: str, **attrs):
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        sys.modules[name] = module
        return module

    default_session, request_exception, adapter = default_stub_classes()
    if "requests" not in sys.modules:
        add(
            "requests",
            Session=session_class or default_session,
            get=lambda *a, **k: None,
        )
    if "requests.exceptions" not in sys.modules:
        add("requests.exceptions", RequestException=request_exception)
    if "requests.adapters" not in sys.modules:
        add("requests.adapters", HTTPAdapter=adapter)
    if "json5" not in sys.modules:
        add("json5", loads=json5_loads, load=json5_load)
    if "tqdm" not in sys.modules:
        add("tqdm", tqdm=lambda iterable, **kwargs: iterable)
    if "lxml" not in sys.modules:
        lxml = add("lxml")
        lxml.etree = add("lxml.etree")
    if "piexif" not in sys.modules:
        add("piexif")
    if "schedule" not in sys.modules:
        add("schedule", every=lambda *a, **k: None, run_pending=lambda: None)


def strip_json5_comments(text: str) -> str:
    """去掉 JSON5 的 // 与 /* */ 注释（保留字符串内的内容），供测试解析配置文件。"""
    out = []
    index = 0
    in_string = False
    quote = ""
    escaped = False
    while index < len(text):
        char = text[index]
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
            index += 1
            continue
        if char in "\"'":
            in_string = True
            quote = char
            out.append(char)
            index += 1
            continue
        if char == "/" and index + 1 < len(text):
            if text[index + 1] == "/":
                while index < len(text) and text[index] not in "\r\n":
                    index += 1
                continue
            if text[index + 1] == "*":
                index += 2
                while index + 1 < len(text) and not (
                    text[index] == "*" and text[index + 1] == "/"
                ):
                    index += 1
                index += 2
                continue
        out.append(char)
        index += 1
    return "".join(out)


def load_json5_file(path) -> dict:
    """读取 JSON5 配置文件（测试内不依赖 json5 库：去注释后按 JSON 解析）。"""
    import json

    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    return json.loads(strip_json5_comments(text), strict=False)


def _strip_trailing_commas(text: str) -> str:
    """去掉对象/数组末尾多余的逗号（字符串内不处理），供 json5 stub 使用。"""
    out = []
    index = 0
    in_string = False
    quote = ""
    escaped = False
    while index < len(text):
        char = text[index]
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
            index += 1
            continue
        if char in "\"'":
            in_string = True
            quote = char
            out.append(char)
            index += 1
            continue
        if char == ",":
            lookahead = index + 1
            while lookahead < len(text) and text[lookahead] in " \t\r\n":
                lookahead += 1
            if lookahead < len(text) and text[lookahead] in "}]":
                index = lookahead
                continue
        out.append(char)
        index += 1
    return "".join(out)


def json5_loads(text: str) -> dict:
    """json5 stub 的 loads 实现：去注释、去尾逗号后按 JSON 解析。

    重要：这是「最小可用实现」而不是空实现——config_migrate 的
    ``_loads_json5`` 会根据 json5 模块是否存在决定用库还是内置降级；
    若 stub 返回空 dict，所有依赖真实配置解析的测试都会被静默带偏。
    """
    import json

    return json.loads(_strip_trailing_commas(strip_json5_comments(text)), strict=False)


def json5_load(fp) -> dict:
    return json5_loads(fp.read())


def bootstrap(session_class=None) -> None:
    """测试文件统一入口：隔离日志 + 安装 stub（幂等）。"""
    isolate_logs()
    install_import_stubs(session_class=session_class)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------

#: 典型媒体二进制样例（下载/完整性/报告类测试共用）
JPEG = b"\xFF\xD8\xFF" + b"0" * 100 + b"\xff\xd9"
PNG = b"\x89PNG\r\n\x1A\n" + b"0" * 100 + b"IEND\xaeB`\x82"
HTML = b"<!DOCTYPE html><html><body>blocked</body></html>"


def write_text(path: Path, text: str) -> None:
    """写 UTF-8 文本文件（自动创建父目录），测试夹具用。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def remove_tree(path: Path) -> None:
    '删除测试产物目录，兼容 exFAT 等非 POSIX 盘。'
    root = Path(path)
    if not root.exists():
        return
    try:
        shutil.rmtree(root)
        return
    except OSError:
        pass
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        for name in filenames:
            try:
                os.unlink(os.path.join(dirpath, name))
            except FileNotFoundError:
                pass
        for name in dirnames:
            try:
                os.rmdir(os.path.join(dirpath, name))
            except OSError:
                pass
    try:
        os.rmdir(root)
    except OSError:
        pass
