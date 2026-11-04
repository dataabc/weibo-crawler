"""HTTP 请求辅助。

- ``random_headers``：防封禁随机请求头（UA/Accept-Language/Referer）；
- ``fetch_json``：统一 JSON 请求（带异常兜底，返回 (json, status)）。

只做纯逻辑，不持有 session；session/配置由调用方传入。
"""

from __future__ import annotations

import random

from requests.exceptions import RequestException

DEFAULT_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0"
]
DEFAULT_ACCEPT_LANGUAGES = ["zh-CN,zh;q=0.9,en;q=0.8"]
DEFAULT_REFERERS = ["https://m.weibo.cn/", "https://weibo.com/"]

_FIXED_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "cache-control": "max-age=0",
    "priority": "u=0, i",
    "sec-ch-ua": "\"Chromium\";v=\"136\", \"Microsoft Edge\";v=\"136\", "
    "\"Not.A/Brand\";v=\"99\"",
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": "\"Windows\"",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
    "upgrade-insecure-requests": "1",
}


def random_headers(
    enabled: bool,
    anti_ban_config: dict | None,
    fallback_headers: dict,
    rng: random.Random | None = None,
) -> dict:
    """返回请求头：关闭防封禁时原样返回 fallback；开启时随机化。"""
    if not enabled:
        return fallback_headers
    config = anti_ban_config or {}
    chooser = rng or random
    user_agent = chooser.choice(config.get("user_agents") or DEFAULT_USER_AGENTS)
    accept_language = chooser.choice(
        config.get("accept_languages") or DEFAULT_ACCEPT_LANGUAGES
    )
    referer = chooser.choice(config.get("referer_list") or DEFAULT_REFERERS)
    headers = dict(_FIXED_HEADERS)
    headers.update(
        {
            "Referer": referer,
            "accept-language": accept_language,
            "user-agent": user_agent,
        }
    )
    return headers


def fetch_json(
    session,
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    logger=None,
    timeout: int = 10,
) -> tuple[dict, int]:
    ""
    try:
        response = session.get(
            url, params=params, headers=headers, verify=False, timeout=timeout
        )
        response.raise_for_status()
        return response.json(), response.status_code
    except RequestException as exc:
        if logger is not None:
            logger.error("请求失败，错误信息：%s", exc)
        return {}, 500
    except ValueError as exc:
        if logger is not None:
            logger.error("JSON 解码失败，错误信息：%s", exc)
        return {}, 500
