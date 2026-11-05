"""用户列表文件更新。"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger("weibo")


def update_user_config_file(wb, user_config_file_path) -> None:
    """更新用户列表文件里该用户的第三列（上次成功抓取时间）。

    按任意空白 split，兼容昵称含空格、多空格/制表符对齐；保留其余列并原子写回。
    """
    if not wb.start_date:
        logger.warning("未记录本轮抓取开始时间，跳过用户列表第三列更新")
        return
    try:
        with open(user_config_file_path, "rb") as f:
            raw = f.read()
        lines = [line.decode("utf-8-sig") for line in raw.splitlines()]
    except UnicodeDecodeError:
        logger.error("%s文件应为utf-8编码，请先将文件编码转为utf-8再运行程序", user_config_file_path)
        sys.exit()
    except OSError as e:
        logger.warning("读取用户列表失败，跳过第三列更新: %s", e)
        return
    changed = False
    for i, line in enumerate(lines):
        info = line.strip().split()
        if len(info) == 0 or not info[0].isdigit():
            continue
        if wb.user_config["user_id"] != info[0]:
            continue
        if len(info) == 1:
            info.append(wb.user.get("screen_name", ""))
            info.append(wb.start_date)
        elif len(info) == 2:
            info.append(wb.start_date)
        else:
            info[2] = wb.start_date
        lines[i] = " ".join(info)
        changed = True
        break
    if not changed:
        return
    trailing_newline = raw.endswith(b"\n")
    text = "\n".join(lines) + ("\n" if trailing_newline else "")
    try:
        wb._atomic_write_text(user_config_file_path, text)
    except OSError as e:
        logger.warning("写回用户列表失败: %s", e)
