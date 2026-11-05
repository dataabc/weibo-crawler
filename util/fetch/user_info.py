"""用户信息获取与解析。

``get_user_info`` 请求用户信息接口、校验状态并解析为 wb.user；依赖的
wb 属性/方法（session/headers/validate_user 等）由 Weibo 实例提供。
"""

from __future__ import annotations

import logging
import random
import sys
from collections import OrderedDict

from requests.exceptions import RequestException

logger = logging.getLogger("weibo")


def _sleep(seconds):
    """延迟取 weibo 模块的 sleep（便于测试 patch）。"""
    from weibo import sleep as weibo_sleep

    weibo_sleep(seconds)


def get_user_info(wb) -> int:
    """获取用户信息"""
    params = {"containerid": "100505" + str(wb.user_config["user_id"])}
    url = "https://m.weibo.cn/api/container/getIndex"

    # 这里在读取下一个用户的时候很容易被ban，需要优化休眠时长
    # 加一个count，不需要一上来啥都没干就sleep
    if wb.long_sleep_count_before_each_user > 0:
        sleep_time = random.randint(30, 60)
        # 添加log，否则一般用户不知道以为程序卡了
        logger.info(f"""短暂sleep {sleep_time}秒，避免被ban""")
        _sleep(sleep_time)
        logger.info("sleep结束")
    wb.long_sleep_count_before_each_user = wb.long_sleep_count_before_each_user + 1

    max_retries = 5  # 设置最大重试次数，避免无限循环
    retries = 0
    backoff_factor = 5  # 指数退避的基数（秒）

    while retries < max_retries:
        try:
            logger.info(f"准备获取ID：{wb.user_config['user_id']}的用户信息第{retries+1}次。")

            # 防封禁：使用随机请求头
            current_headers = wb.get_random_headers()

            # 防封禁：动态延迟
            delay = wb.calculate_dynamic_delay()
            if delay > 0:
                logger.debug(f"动态延迟: {delay:.1f} 秒")
                _sleep(delay)

            response = wb.session.get(url, params=params, headers=current_headers, timeout=10)
            response.raise_for_status()
            js = response.json()

            # 更新统计：成功请求
            wb.update_crawl_stats(request_count=1)
            if 'data' in js and 'userInfo' in js['data']:
                info = js["data"]["userInfo"]
                user_info = OrderedDict()
                user_info["id"] = wb.user_config["user_id"]
                user_info["screen_name"] = info.get("screen_name", "")
                user_info["gender"] = info.get("gender", "")
                params = {
                    "containerid": "230283" + str(wb.user_config["user_id"]) + "_-_INFO"
                }
                zh_list = ["生日", "所在地", "IP属地", "小学", "初中", "高中", "大学", "公司", "注册时间", "阳光信用"]
                en_list = [
                    "birthday",
                    "location",
                    "ip_location",
                    "education",
                    "education",
                    "education",
                    "education",
                    "company",
                    "registration_time",
                    "sunshine",
                ]
                for i in en_list:
                    user_info[i] = ""
                js, _ = wb.get_json(params)
                if js["ok"]:
                    cards = js["data"]["cards"]
                    if isinstance(cards, list) and len(cards) > 1:
                        card_list = cards[0]["card_group"] + cards[1]["card_group"]
                        for card in card_list:
                            if card.get("item_name") in zh_list:
                                user_info[
                                    en_list[zh_list.index(card.get("item_name"))]
                                ] = card.get("item_content", "")
                user_info["statuses_count"] = wb.string_to_int(
                    info.get("statuses_count", 0)
                )
                user_info["followers_count"] = wb.string_to_int(
                    info.get("followers_count", 0)
                )
                user_info["follow_count"] = wb.string_to_int(info.get("follow_count", 0))
                user_info["description"] = info.get("description", "")
                user_info["profile_url"] = info.get("profile_url", "")
                user_info["profile_image_url"] = info.get("profile_image_url", "")
                user_info["avatar_hd"] = info.get("avatar_hd", "")
                user_info["urank"] = info.get("urank", 0)
                user_info["mbrank"] = info.get("mbrank", 0)
                user_info["verified"] = info.get("verified", False)
                user_info["verified_type"] = info.get("verified_type", -1)
                user_info["verified_reason"] = info.get("verified_reason", "")
                wb.user = wb.standardize_info(user_info)
                wb.user_to_database()
                logger.info(f"成功获取到用户 {wb.user_config['user_id']} 的信息。")
                return 0
            elif isinstance(js.get("url"), str) and js.get("url").strip():
                logger.warning("未能获取到用户信息，可能需要验证码验证。")
                if wb.handle_captcha(js):
                    logger.info("用户已完成验证码验证，继续请求用户信息。")
                    retries = 0  # 重置重试计数器
                    continue
                else:
                    logger.error("验证码验证失败或未完成，程序将退出。")
                    sys.exit()
            elif isinstance(js.get("msg"), str) and "这里还没有内容" in js.get("msg"):
                logger.warning("未能获取到用户信息，可能账号已注销或用户id有误。")
                return 1
            else:
                logger.warning("未能获取到用户信息。")
                return 1
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
    logger.error("超过最大重试次数，跳过该用户（不终止整轮抓取）。")
    return 1
