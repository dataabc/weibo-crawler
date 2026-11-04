"""缺失媒体的在线归因探测。

本模块对「缺失」条目做一次轻量在线请求（通常个位数、可关闭/失败降级），
用 not_downloaded.txt 里的原始 URL 区分归因：

- 平台侧已删除（404/410）→ 无法恢复；
- 本地缺失但平台仍可下载（2xx/3xx 且非 HTML）→ 可重新下载；
- 平台拒绝访问（401/403 或返回 HTML 错误页）→ 需人工确认；
- 网络失败 → 无法确认；
- 没有下载来源记录 → 无下载来源记录。

只做探测、不下载、不修改本地文件；失败一律降级为「无法确认」，不阻断报告。"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("weibo")

ATTRIBUTION_PLATFORM_DELETED = "平台侧已删除"
ATTRIBUTION_RECOVERABLE = "本地缺失，平台仍可下载"
ATTRIBUTION_RESTRICTED = "平台拒绝访问，需人工确认"
ATTRIBUTION_UNKNOWN = "无法确认"
ATTRIBUTION_NO_SOURCE = "无下载来源记录"


def _probe_one(session, headers, url: str, timeout: float) -> str:
    """探测单个 URL，返回归因字符串；任何异常都降级为「无法确认」。"""
    if not url:
        return ATTRIBUTION_NO_SOURCE
    try:
        # stream=True：只拿响应头与状态码，不读大文件正文
        resp = session.get(
            url,
            headers=headers,
            timeout=timeout,
            stream=True,
            allow_redirects=True,
        )
    except Exception as exc:  # noqa: BLE001 兜底：网络失败不阻断报告
        logger.debug("缺失媒体探测失败: %s", exc)
        return ATTRIBUTION_UNKNOWN
    status = int(getattr(resp, "status_code", 0) or 0)
    content_type = ""
    try:
        content_type = str(resp.headers.get("content-type") or "").lower()
    except Exception:  # noqa: BLE001 响应替身可能没有 headers
        content_type = ""
    finally:
        try:
            resp.close()
        except Exception:  # noqa: BLE001 关闭失败不影响结论
            pass

    if status in (404, 410):
        return ATTRIBUTION_PLATFORM_DELETED
    if status in (401, 403):
        return ATTRIBUTION_RESTRICTED
    if 200 <= status < 400:
        # CDN 防盗链/风控页常以 200 + text/html 返回，不能算「仍可下载」
        if "html" in content_type:
            return ATTRIBUTION_RESTRICTED
        return ATTRIBUTION_RECOVERABLE
    return ATTRIBUTION_UNKNOWN


def probe_missing_sources(
    session,
    headers,
    local_paths,
    sources,
    timeout: float = 10,
) -> dict[str, str]:
    """对缺失的本地路径做在线探测。

    :param session: requests.Session 兼容对象（Weibo.session）
    :param headers: 请求头（Weibo.headers）
    :param local_paths: 缺失媒体的本地绝对路径列表
    :param sources: ``{本地路径: (微博ID, 原始URL)}``（not_downloaded.load_map 的结果）
    :param timeout: 单条探测超时秒数
    :return: ``{本地路径: 归因}``
    """
    result: dict[str, str] = {}
    for local_path in local_paths:
        info = sources.get(os.path.abspath(local_path)) or sources.get(local_path)
        if not info:
            result[local_path] = ATTRIBUTION_NO_SOURCE
            continue
        url = info[1] if isinstance(info, (tuple, list)) and len(info) >= 2 else ""
        result[local_path] = _probe_one(session, headers, url, timeout)
    return result
