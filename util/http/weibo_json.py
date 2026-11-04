"""微博 JSON 请求与解析。

``get_weibo_json`` 负责请求第 N 页接口、处理验证码/空数据/网络异常，并返回
解析后的 JSON；依赖的 wb 属性/方法（session/headers/crawl_success 等）由
Weibo 实例提供。
"""

from __future__ import annotations

import logging

from requests.exceptions import RequestException

logger = logging.getLogger("weibo")


def _sleep(seconds):
    ""
    from weibo import sleep as weibo_sleep

    weibo_sleep(seconds)


def get_weibo_json(wb, page) -> object:
    """获取网页中微博json数据"""
    url = "https://m.weibo.cn/api/container/getIndex?"
    params = (
        {
            "container_ext": "profile_uid:" + str(wb.user_config["user_id"]),
            "containerid": "100103type=401&q=" + wb.query,
            "page_type": "searchall",
        }
        if wb.query
        else {"containerid": "230413" + str(wb.user_config["user_id"])}
    )
    params["page"] = page
    params["count"] = wb.page_weibo_count
    # 重试与退避：最多重试 3 次、单次退避上限 30 秒，避免多页连续失败
    # 把整轮拖成数小时。
    max_retries = 3
    retries = 0
    backoff_factor = 5

    while retries < max_retries:
        try:
            # 防封禁：使用随机请求头
            current_headers = wb.get_random_headers()

            # 防封禁：动态延迟
            delay = wb.calculate_dynamic_delay()
            if delay > 0:
                logger.debug(f"动态延迟: {delay:.1f} 秒")
                _sleep(delay)

            response = wb.session.get(url, params=params, headers=current_headers, timeout=10)
            response.raise_for_status()  # 如果响应状态码不是 200，会抛出 HTTPError
            js = response.json()

            # 更新统计：成功请求
            wb.update_crawl_stats(request_count=1)

            if 'data' in js:
                logger.info(f"成功获取到页面 {page} 的数据。")
                return js

            # 无 data 的响应分两种，不能再一律当验证码并 sys.exit：
            # 1) 带 url：验证码挑战，交 handle_captcha 处理；
            # 2) 不带 url：分页自然结束（典型 {"ok":0,"msg":"这里还没有内容"}），
            #    返回 None 作为「无更多数据」哨兵，由 get_one_page 置 api_ended。
            if js.get("url"):
                logger.warning("未能获取到数据，检测到验证码挑战。")
                if wb.handle_captcha(js):
                    logger.info("用户已完成验证码验证，继续请求数据。")
                    retries = 0  # 重置重试计数器
                    continue
                logger.error("验证码验证失败或未完成，跳过当前页面。")
                return {}
            logger.info(
                "第%d页接口无更多数据（%s），视为分页结束。",
                page,
                js.get("msg") or "无 data 字段",
            )
            return None
        except RequestException as e:
            retries += 1
            sleep_time = min(30, backoff_factor * (2 ** retries))
            logger.error(f"请求失败，错误信息：{e}。等待 {sleep_time} 秒后重试...")
            _sleep(sleep_time)
            # 更新统计：API错误
            wb.update_crawl_stats(api_error=True)
        except ValueError as ve:
            retries += 1
            sleep_time = min(30, backoff_factor * (2 ** retries))
            logger.error(f"JSON 解码失败，错误信息：{ve}。等待 {sleep_time} 秒后重试...")
            _sleep(sleep_time)
            # 更新统计：API错误
            wb.update_crawl_stats(api_error=True)

    logger.error("超过最大重试次数，跳过当前页面。")
    return {}
