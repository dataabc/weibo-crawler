"""微博可见性核验（statuses/show）。

供评论回填（区分空响应原因）和主爬（确认已删除微博）共用。
"""

from __future__ import annotations

WEIBO_STATUS_URL = "https://m.weibo.cn/statuses/show"


def check_weibo_visibility(session, headers, weibo_id: str, timeout: float = 15.0):
    """返回 ``(kind, message)``：visible / deleted / restricted / unknown。"""
    try:
        response = session.get(
            WEIBO_STATUS_URL,
            params={"id": str(weibo_id)},
            headers=headers,
            timeout=timeout,
        )
        if getattr(response, "status_code", 200) != 200:
            return "unknown", f"HTTP {response.status_code}"
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 状态核验失败不阻断主流程
        return "unknown", f"{type(exc).__name__}: {exc}"
    if not isinstance(payload, dict):
        return "unknown", "响应不是字典"
    message = str(payload.get("msg") or "")
    data = payload.get("data")
    if payload.get("ok") == 1 and isinstance(data, dict):
        # ok=1 表示当前 cookie 能访问；visible.type 只是分享范围
        # （0/7 公开、1 仅自己、6 好友、10 粉丝），不能据此判成无权限。
        return "visible", message
    errno = str(payload.get("errno") or "")
    # 只认平台明确的两种业务错误；其他 ok=0 一律 unknown，并保留 msg 原文。
    if errno == "20101" or "该微博不存在" in message:
        return "deleted", message
    if errno == "20112" or "暂无查看权限" in message:
        return "restricted", message
    return "unknown", message
