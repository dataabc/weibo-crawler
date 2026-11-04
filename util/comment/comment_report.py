"""评论下载情况报告渲染。

纯文本渲染（不写文件）：统计本轮评论抓取结果（写入/未变化/无评论/跳过/失败、
一级评论与二级回复数、请求数/错误数、重试数）并逐条列出微博状态。
"""

from __future__ import annotations

from datetime import datetime


def render_comment_summary(
    results: list[dict],
    screen_name: str,
    user_id: str,
    generated_at: datetime | None = None,
) -> str:
    """渲染评论下载情况报告文本。"""
    written = sum(1 for item in results if item.get("status") == "已写入")
    unchanged = sum(1 for item in results if item.get("status") == "未变化")
    empty = sum(1 for item in results if item.get("status") == "无评论")
    skipped = sum(1 for item in results if item.get("status") == "跳过")
    failed = sum(1 for item in results if item.get("status") == "失败")
    retried = sum(1 for item in results if item.get("retried"))
    roots = sum(int(item.get("roots") or 0) for item in results)
    replies = sum(int(item.get("replies") or 0) for item in results)
    requests_total = sum(int(item.get("requests") or 0) for item in results)
    errors = sum(int(item.get("errors") or 0) for item in results)
    generated = (generated_at or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        "评论下载情况报告",
        "生成时间: %s" % generated,
        "账号: %s（%s）" % (screen_name, user_id),
        "本轮处理微博数: %d（其中重试上次失败 %d 条）" % (len(results), retried),
        "已写入评论块的微博数: %d；未变化: %d" % (written, unchanged),
        "无评论: %d 条；跳过: %d 条" % (empty, skipped),
        "失败微博数: %d（已记入评论失败重试清单，下一轮自动重试）" % failed,
        "已存档评论: %d 条一级 + %d 条二级回复" % (roots, replies),
        "评论请求总数: %d，请求错误数: %d" % (requests_total, errors),
        "评论者白名单过滤: %s"
        % (
            "已启用"
            if any(item.get("filtered_users") for item in results)
            else "未启用"
        ),
        "说明: 微博评论接口有上限，已存档条数可能少于微博显示的评论总数；"
        "评论块的份量与边界见「口径与叫法」。",
        "-" * 60,
    ]
    for item in results:
        lines.append(
            "微博id: %s | 状态: %s | 已存档: %d 条一级 + %d 条回复 | "
            "评论总数: %d | 请求: %d | 错误: %d | %s"
            % (
                item.get("weibo_id", ""),
                item.get("status", ""),
                int(item.get("roots") or 0),
                int(item.get("replies") or 0),
                int(item.get("total_number") or 0),
                int(item.get("requests") or 0),
                int(item.get("errors") or 0),
                item.get("note", ""),
            )
        )
    return "\n".join(lines) + "\n"
