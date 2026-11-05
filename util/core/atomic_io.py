"""原子写与文件备份。

- ``atomic_write_bytes`` / ``atomic_write_text``：临时文件 + os.replace + fsync，
  避免中断留下半截文件（主存档与媒体都是核心资产）；
  ``preserve_mode=True`` 时保留目标文件原权限；
- ``backup_file``：按天生成的 ``<path>.bak.<YYYYMMDD>[_N]`` 备份（同一天多份编号递增）。
"""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime


def atomic_write_bytes(path: str, data: bytes, preserve_mode: bool = False) -> None:
    """原子写二进制（同目录临时文件 + os.replace + fsync）。

    ``preserve_mode=True``：写前记录目标文件权限，写后恢复（新建文件时为 0600）。
    """
    directory = os.path.dirname(os.path.abspath(path)) or os.getcwd()
    os.makedirs(directory, exist_ok=True)
    original_mode = None
    if preserve_mode:
        try:
            original_mode = os.stat(path).st_mode
        except OSError:
            original_mode = None
    fd, tmp_path = tempfile.mkstemp(prefix=".atomic-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        if original_mode is not None:
            try:
                os.chmod(path, original_mode)
            except OSError:
                pass
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def atomic_write_text(path: str, text: str, preserve_mode: bool = False) -> None:
    """原子写文本（UTF-8）。"""
    atomic_write_bytes(path, text.encode("utf-8"), preserve_mode=preserve_mode)


def backup_file(path: str, logger=None) -> str | None:
    """把文件备份为 ``<path>.bak.<YYYYMMDD>[_N]``；文件不存在返回 None。"""
    if not os.path.isfile(path):
        return None
    day = datetime.now().strftime("%Y%m%d")
    backup_path = f"{path}.bak.{day}"
    if os.path.exists(backup_path):
        index = 2
        while os.path.exists(f"{path}.bak.{day}_{index}"):
            index += 1
        backup_path = f"{path}.bak.{day}_{index}"
    try:
        shutil.copy2(path, backup_path)
        return backup_path
    except OSError as exc:
        if logger is not None:
            logger.warning("备份文件失败（继续）: %s", exc)
        return None
