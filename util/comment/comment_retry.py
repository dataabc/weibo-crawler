"""评论失败重试清单。

每账号一份 ``report/comment_retry_<账号>.json``，记录本轮抓取失败（网络/HTTP/
风控）的微博评论，下一轮自动重试，避免评论永久缺失。纯文件读写，依赖由调用方
注入（报告目录、原子写、日志）。
"""

from __future__ import annotations

import json
import os


def retry_path(report_dir: str, user_id: str) -> str:
    """重试清单路径：``<报告目录>/comment_retry_<账号>.json``。"""
    return os.path.join(report_dir, "comment_retry_%s.json" % user_id)


def load(path: str, logger=None) -> dict[str, dict]:
    """读取重试清单；不存在/损坏返回空 dict（不抛异常）。"""
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            return {
                str(key): value
                for key, value in data.items()
                if isinstance(value, dict)
            }
    except (OSError, ValueError) as exc:
        if logger is not None:
            logger.warning("读取评论失败重试清单失败（忽略）: %s", exc)
    return {}


def save(path: str, retry_map: dict, atomic_write, logger=None) -> bool:
    """原子写重试清单；清单为空时删除文件。返回是否写成功。"""
    if not retry_map:
        if os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass
        return True
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        atomic_write(path, json.dumps(retry_map, ensure_ascii=False, indent=2))
        return True
    except Exception as exc:  # noqa: BLE001
        if logger is not None:
            logger.warning("写入评论失败重试清单失败: %s", exc)
        return False
