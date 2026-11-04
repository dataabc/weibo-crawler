"""下载失败记录（not_downloaded.txt）管理。

格式（每行）：``微博ID:本地绝对路径:原始URL``

- ``record_path``：决定失败记录写到哪里（媒体目录 / 报告目录 / 自定义目录）；
- ``clear_record``：下载成功后删除对应失败行；
- ``load_map``：读取失败记录，返回 {本地绝对路径: (微博ID, URL)}，供媒体报告附原始 URL。

只做文件操作，不依赖 Weibo 实例；目录解析由调用方传入回调。
"""

from __future__ import annotations

import os
from collections.abc import Callable

from util.core import atomic_io

FILENAME = "not_downloaded.txt"


def record_path(
    file_path: str,
    configured_dir: str | None,
    resolve_report_dir: Callable[[str], str] | None = None,
) -> str:
    """返回失败记录文件路径。

    ``configured_dir`` 为空或 ``"media"``：写在目标文件所在目录；
    其他值：作为报告目录（相对 output_directory 或绝对路径）由 ``resolve_report_dir`` 解析。
    """
    configured = configured_dir if isinstance(configured_dir, str) else ""
    if configured.strip() not in ("", "media"):
        if resolve_report_dir is None:
            dir_path = configured
        else:
            dir_path = resolve_report_dir(configured)
    else:
        dir_path = os.path.dirname(os.path.abspath(file_path)) or os.getcwd()
    os.makedirs(dir_path, exist_ok=True)
    return os.path.join(dir_path, FILENAME)


def clear_record(error_file: str, weibo_id: str, url: str) -> bool:
    """下载成功后从失败记录删除对应行；返回是否发生修改。"""
    if not os.path.isfile(error_file):
        return False
    try:
        with open(error_file, "r", encoding="utf-8", errors="ignore") as handle:
            lines = handle.readlines()
        kept = [
            line
            for line in lines
            if not (line.startswith(f"{weibo_id}:") and url in line)
        ]
        if len(kept) == len(lines):
            return False
        # 原子写：中断时不会留下半截失败记录
        atomic_io.atomic_write_text(error_file, "".join(kept))
        return True
    except OSError:
        return False


def parse_line(line: str) -> tuple[str, str, str] | None:
    """解析一行：返回 (微博ID, 本地路径, URL)；格式不合法返回 None。

    注意 URL 自身含冒号（``https://``），不能按最后一个冒号切分；
    按 ``:http`` 标记定位 URL 起点（兼容路径中的冒号，如 Windows 盘符）。
    """
    line = line.strip()
    if not line or line.count(":") < 2:
        return None
    weibo_id, rest = line.split(":", 1)
    marker = rest.find(":http")
    if marker < 0:
        return None
    local_path = rest[:marker].strip()
    url = rest[marker + 1:].strip()
    if not local_path or not url.startswith(("http://", "https://")):
        return None
    return weibo_id.strip(), local_path, url


def collect_files(user_dir: str, report_dir: str | None = None) -> list[str]:
    """收集用户目录下所有 not_downloaded.txt（含媒体目录多份与报告目录一份）。"""
    files: list[str] = []
    if user_dir and os.path.isdir(user_dir):
        for dirpath, _dirs, names in os.walk(user_dir):
            for name in names:
                if name == FILENAME and not name.startswith("._"):
                    files.append(os.path.join(dirpath, name))
    if report_dir:
        candidate = os.path.join(report_dir, FILENAME)
        if candidate not in files:
            files.append(candidate)
    return files


def load_map(user_dir: str, report_dir: str | None = None) -> dict[str, tuple[str, str]]:
    """读取失败记录，返回 {本地绝对路径: (微博ID, URL)}。"""
    result: dict[str, tuple[str, str]] = {}
    for path in collect_files(user_dir, report_dir):
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    parsed = parse_line(line)
                    if parsed is None:
                        continue
                    weibo_id, local_path, url = parsed
                    result[os.path.abspath(local_path)] = (weibo_id, url)
        except OSError:
            continue
    return result
