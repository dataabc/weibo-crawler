"""评论图片下载规划。

给定评论结果（一级评论 + 回复），遍历有图片的评论并按固定命名规则生成
下载目标路径：``<图片目录>/<发布时间>.jpg``（与正文图片/视频命名一致）。实际下载由调用方完成
（下载器需要 Weibo 实例的 headers/session/失败记录），本模块只做纯规划。
"""

from __future__ import annotations

import os


def iter_comments(roots) -> list:
    """把一级评论与回复展开成一个列表（一级在前，回复紧随）。"""
    comments: list = []
    for root in roots or []:
        comments.append(root)
        comments.extend(getattr(root, "replies", None) or [])
    return comments


def image_target(image_dir: str, comment) -> tuple[str, str] | None:
    """返回 (文件名, 完整路径)；评论无图片或字段缺失返回 None。"""
    pic_url = getattr(comment, "pic_url", "")
    if not pic_url:
        return None
    created_at = getattr(comment, "created_at", "") or "unknown"
    stamp = str(created_at).replace(" ", "_").replace(":", "-")
    # 与正文图片/视频命名保持一致：<发布时间>.jpg（多条时由下载侧补序号）
    file_name = "%s.jpg" % stamp
    return file_name, os.path.join(image_dir, file_name)
