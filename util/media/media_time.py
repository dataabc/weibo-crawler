"""媒体文件时间处理。

- ``write_exif_time``：把微博发布时间写入 JPG EXIF（DateTimeOriginal）；
- ``set_file_time``：把微博发布时间写入文件系统访问/修改时间。

两者都受配置开关控制（write_time_in_exif / change_file_time），失败只记日志、"""

from __future__ import annotations

import os
import time

try:
    import piexif
except Exception:  # noqa: BLE001
    piexif = None


def write_exif_time(file_path: str, time_str: str, enabled: bool = True, logger=None) -> bool:
    """写入 JPG EXIF 拍摄时间；enabled=False 或失败返回 False。"""
    if not enabled:
        return False
    try:
        if piexif is None:
            return False
        # "YYYY-MM-DDTHH:MM:SS" → "YYYY:MM:DD HH:MM:SS"
        exif_time = str(time_str).replace("-", ":").replace("T", " ")[:19]
        exif_dict = {"Exif": {piexif.ExifIFD.DateTimeOriginal: exif_time}}
        exif_bytes = piexif.dump(exif_dict)
        piexif.insert(exif_bytes, file_path)
        if logger is not None:
            logger.debug("[EXIF] 已将时间 %s 写入 %s", exif_time, file_path)
        return True
    except Exception as exc:  # noqa: BLE001
        if logger is not None:
            logger.debug("EXIF写入跳过或失败: %s", exc)
        return False


def set_file_time(file_path: str, time_str: str, enabled: bool = True, logger=None) -> bool:
    """设置文件系统访问/修改时间；enabled=False 或失败返回 False。"""
    if not enabled:
        return False
    try:
        clean_time = str(time_str).replace("T", " ")
        tick = time.mktime(time.strptime(clean_time, "%Y-%m-%d %H:%M:%S"))
        os.utime(file_path, (tick, tick))
        if logger is not None:
            logger.debug("[FILE] 已将时间 %s 写入 %s", clean_time, file_path)
        return True
    except Exception as exc:  # noqa: BLE001
        if logger is not None:
            logger.debug("修改文件系统时间失败: %s", exc)
        return False
