#!/usr/bin/env python
# -*- coding: utf-8 -*-

import codecs
import copy
import csv
import json
import logging
import logging.config
import math
import os
import random
import re
import sqlite3
import sys
import time
import warnings
import webbrowser
from collections import OrderedDict
from datetime import date, datetime, timedelta
from pathlib import Path
from time import sleep

import requests
from requests.exceptions import RequestException
from lxml import etree
import json5
from requests.adapters import HTTPAdapter
from tqdm import tqdm

import const
from util import csvutil
from util.dateutil import convert_to_days_ago
from util.notify import push_deer
from util.llm_analyzer import LLMAnalyzer  # 导入 LLM 分析器

import piexif

warnings.filterwarnings("ignore")

# 如果日志文件夹不存在，则创建
if not os.path.isdir("log/"):
    os.makedirs("log/")
logging_path = os.path.split(os.path.realpath(__file__))[0] + os.sep + "logging.conf"
logging.config.fileConfig(logging_path)
logger = logging.getLogger("weibo")

# 日期时间格式
DTFORMAT = "%Y-%m-%dT%H:%M:%S"

class Weibo(object):
    def __init__(self, config):
        """Weibo类初始化"""
        self.validate_config(config)
        self.only_crawl_original = config["only_crawl_original"]  # 取值范围为0、1,程序默认值为0,代表要爬取用户的全部微博,1代表只爬取用户的原创微博
        self.remove_html_tag = config[
            "remove_html_tag"
        ]  # 取值范围为0、1, 0代表不移除微博中的html tag, 1代表移除
        since_date = config["since_date"]
        # since_date 若为整数，则取该天数之前的日期；若为 yyyy-mm-dd，则增加时间
        if isinstance(since_date, int):
            since_date = date.today() - timedelta(since_date)
            since_date = since_date.strftime(DTFORMAT)
        elif self.is_date(since_date):
            since_date = "{}T00:00:00".format(since_date)
        elif self.is_datetime(since_date):
            pass
        else:
            logger.error("since_date 格式不正确，请确认配置是否正确")
            sys.exit()
        self.since_date = since_date  # 起始时间，即爬取发布日期从该值到现在的微博，形式为yyyy-mm-ddThh:mm:ss，如：2023-08-21T09:23:03
        self.start_page = config.get("start_page", 1)  # 开始爬的页，如果中途被限制而结束可以用此定义开始页码
        self.write_mode = config[
            "write_mode"
        ]  # 结果信息保存类型，为list形式，可包含csv、mongo和mysql三种类型
        self.original_pic_download = config[
            "original_pic_download"
        ]  # 取值范围为0、1, 0代表不下载原创微博图片,1代表下载
        self.retweet_pic_download = config[
            "retweet_pic_download"
        ]  # 取值范围为0、1, 0代表不下载转发微博图片,1代表下载
        self.original_video_download = config[
            "original_video_download"
        ]  # 取值范围为0、1, 0代表不下载原创微博视频,1代表下载
        self.retweet_video_download = config[
            "retweet_video_download"
        ]  # 取值范围为0、1, 0代表不下载转发微博视频,1代表下载
        
        # 新增Live Photo视频下载配置
        self.original_live_photo_download = config.get("original_live_photo_download", 0)
        self.retweet_live_photo_download = config.get("retweet_live_photo_download", 0)
        
        self.download_comment = config["download_comment"]  # 1代表下载评论,0代表不下载
        self.comment_max_download_count = config[
            "comment_max_download_count"
        ]  # 如果设置了下评论，每条微博评论数会限制在这个值内
        self.download_repost = config["download_repost"]  # 1代表下载转发,0代表不下载
        self.repost_max_download_count = config[
            "repost_max_download_count"
        ]  # 如果设置了下转发，每条微博转发数会限制在这个值内
        self.user_id_as_folder_name = config.get(
            "user_id_as_folder_name", 0
        )  # 结果目录名，取值为0或1，决定结果文件存储在用户昵称文件夹里还是用户id文件夹里
        self.write_time_in_exif = config.get(
            "write_time_in_exif", 0
        )  # 是否开启微博时间写入EXIF，取值范围为0、1, 0代表不开启, 1代表开启
        self.change_file_time = config.get(
            "change_file_time", 0
        )  # 是否修改文件时间，取值范围为0、1, 0代表不开启, 1代表开启
        
        # Cookie支持：优先使用环境变量WEIBO_COOKIE，其次使用config.json中的配置
        cookie_string = os.environ.get("WEIBO_COOKIE") or config.get("cookie")
        if os.environ.get("WEIBO_COOKIE"):
            logger.info("使用环境变量WEIBO_COOKIE中的Cookie")
        
        core_cookies = {}   # 核心包
        backup_cookies = {} # 备份
        # Cookie清洗：提取核心字段。若后续预热失败，则回退使用原版 _T_WM/XSRF-TOKEN
        if cookie_string and "SUB=" in cookie_string:
            # 1. 提取核心 SUB
            match_sub = re.search(r'SUB=(.*?)(;|$)', cookie_string)
            if match_sub:
                core_cookies['SUB'] = match_sub.group(1)
            
            # 2. 提取备份指纹
            match_twm = re.search(r'_T_WM=(.*?)(;|$)', cookie_string)
            if match_twm:
                backup_cookies['_T_WM'] = match_twm.group(1)
            
            match_xsrf = re.search(r'XSRF-TOKEN=(.*?)(;|$)', cookie_string)
            if match_xsrf:
                backup_cookies['XSRF-TOKEN'] = match_xsrf.group(1)
        
        # 保底：如果没有提取到 SUB，说明格式特殊，全量加载
        if not core_cookies and cookie_string:
            for pair in cookie_string.split(';'):
                if '=' in pair:
                    key, value = pair.split('=', 1)
                    core_cookies[key.strip()] = value.strip()
                    
        self.headers = {
            'Referer': 'https://m.weibo.cn/',  # 修正 Referer 为 m.weibo.cn
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
            'cache-control': 'max-age=0',
            'priority': 'u=0, i',
            'sec-ch-ua': '"Chromium";v="136", "Microsoft Edge";v="136", "Not.A/Brand";v="99"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'navigate',
            'sec-fetch-site': 'same-origin',
            'upgrade-insecure-requests': '1',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0',
        }
        self.mysql_config = config.get("mysql_config")  # MySQL数据库连接配置，可以不填
        self.mongodb_URI = config.get("mongodb_URI")  # MongoDB数据库连接字符串，可以不填
        self.post_config = config.get("post_config")  # post_config，可以不填
        self.page_weibo_count = config.get("page_weibo_count")  # page_weibo_count，爬取一页的微博数，默认10页
        
        # 初始化 LLM 分析器
        self.llm_analyzer = LLMAnalyzer(config) if config.get("llm_config") else None
        
        user_id_list = config["user_id_list"]
        requests_session = requests.Session()
        requests_session.cookies.update(core_cookies)

        self.session = requests_session
        try:
            # 请求只带 SUB
            # 服务器下发适配 m.weibo.cn 的新指纹
            self.session.get("https://m.weibo.cn", headers=self.headers, timeout=10)
            logger.info("Session 预热成功，服务器已下发最新指纹。")
            
        except Exception as e:
            #请求失败时，启用备份
            logger.warning(f"Session 预热失败 ({e})，正在启用备份 Cookie...")
            self.session.cookies.update(backup_cookies) # 把旧指纹装进去救急

        adapter = HTTPAdapter(max_retries=5)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)
        # 避免卡住
        if isinstance(user_id_list, list):
            random.shuffle(user_id_list)

        query_list = config.get("query_list") or []
        if isinstance(query_list, str):
            query_list = query_list.split(",")
        self.query_list = query_list
        if not isinstance(user_id_list, list):
            if not os.path.isabs(user_id_list):
                user_id_list = (
                    os.path.split(os.path.realpath(__file__))[0] + os.sep + user_id_list
                )
            self.user_config_file_path = user_id_list  # 用户配置文件路径
            user_config_list = self.get_user_config_list(user_id_list)
        else:
            self.user_config_file_path = ""
            user_config_list = [
                {
                    "user_id": user_id,
                    "since_date": self.since_date,
                    "query_list": query_list,
                }
                for user_id in user_id_list
            ]

        self.user_config_list = user_config_list  # 要爬取的微博用户的user_config列表
        self.user_config = {}  # 用户配置,包含用户id和since_date
        self.start_date = ""  # 获取用户第一条微博时的日期
        self.query = ""
        self.user = {}  # 存储目标微博用户信息
        self.got_count = 0  # 存储爬取到的微博数
        self.weibo = []  # 存储爬取到的所有微博信息
        self.weibo_id_list = []  # 存储爬取到的所有微博id
        self.long_sleep_count_before_each_user = 0 #每个用户前的长时间sleep避免被ban
        self.store_binary_in_sqlite = config.get("store_binary_in_sqlite", 0)

        # 防封禁配置初始化
        self.anti_ban_config = config.get("anti_ban_config", {})
        self.anti_ban_enabled = self.anti_ban_config.get("enabled", False)

        # 爬取状态跟踪
        self.crawl_stats = {
            "weibo_count": 0,      # 已爬取微博数
            "request_count": 0,    # 已发送请求数
            "api_errors": 0,       # API错误数
            "start_time": None,    # 开始时间
            "batch_count": 0,      # 当前批次计数
            "last_batch_time": None # 上次批次时间
        }
    def calculate_dynamic_delay(self):
        """计算动态延迟时间"""
        if not self.anti_ban_enabled:
            return 0

        config = self.anti_ban_config
        base_delay = config.get("request_delay_min", 8)

        # 根据请求次数增加延迟
        request_count = self.crawl_stats["request_count"]
        if request_count > 100:
            base_delay += 5
        if request_count > 300:
            base_delay += 10

        # 根据爬取时间增加延迟
        if self.crawl_stats["start_time"]:
            time_elapsed = time.time() - self.crawl_stats["start_time"]
            if time_elapsed > 300:  # 5分钟
                base_delay += 5

        # 随机波动
        max_delay = config.get("request_delay_max", 15)
        return random.uniform(base_delay, max_delay)

    def should_pause_session(self):
        """检查是否应该暂停当前会话"""
        if not self.anti_ban_enabled:
            return False, ""

        config = self.anti_ban_config
        current_time = time.time()

        # 条件1：达到数量阈值
        max_weibo = config.get("max_weibo_per_session", 500)
        if self.crawl_stats["weibo_count"] >= max_weibo:
            return True, f"达到单次运行最大微博数({max_weibo})"

        # 条件2：运行时间过长
        if self.crawl_stats["start_time"]:
            session_time = current_time - self.crawl_stats["start_time"]
            max_time = config.get("max_session_time", 600)
            if session_time > max_time:
                return True, f"单次运行时间过长({int(session_time)}秒)"

        # 条件3：API错误率过高
        max_errors = config.get("max_api_errors", 5)
        if self.crawl_stats["api_errors"] >= max_errors:
            return True, f"API错误过多({self.crawl_stats['api_errors']}次)"

        # 条件4：随机概率（模拟用户休息）
        random_prob = config.get("random_rest_probability", 0.01)
        if random.random() < random_prob:
            return True, "随机休息"

        return False, ""

    def check_batch_delay(self):
        """检查是否需要批次延迟"""
        if not self.anti_ban_enabled:
            return

        config = self.anti_ban_config
        batch_size = config.get("batch_size", 50)
        batch_delay = config.get("batch_delay", 30)

        # 检查是否达到批次大小
        if self.crawl_stats["batch_count"] >= batch_size:
            current_time = time.time()

            # 检查距离上次批次的时间
            if self.crawl_stats["last_batch_time"]:
                time_since_last_batch = current_time - self.crawl_stats["last_batch_time"]
                if time_since_last_batch < batch_delay:
                    # 如果距离上次批次时间太短，等待补足
                    wait_time = batch_delay - time_since_last_batch
                    logger.info(f"批次延迟: 等待 {wait_time:.1f} 秒")
                    sleep(wait_time)

            logger.info(f"批次延迟: 等待 {batch_delay} 秒")
            sleep(batch_delay)

            # 重置批次计数
            self.crawl_stats["batch_count"] = 0
            self.crawl_stats["last_batch_time"] = time.time()

    def get_random_headers(self):
        """获取随机请求头"""
        if not self.anti_ban_enabled:
            return self.headers

        config = self.anti_ban_config

        # 随机选择User-Agent
        user_agents = config.get("user_agents", [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0"
        ])
        user_agent = random.choice(user_agents)

        # 随机选择Accept-Language
        accept_languages = config.get("accept_languages", [
            "zh-CN,zh;q=0.9,en;q=0.8"
        ])
        accept_language = random.choice(accept_languages)

        # 随机选择Referer
        referers = config.get("referer_list", [
            "https://m.weibo.cn/",
            "https://weibo.com/"
        ])
        referer = random.choice(referers)

        # 返回随机化的请求头
        return {
            'Referer': referer,
            'accept': 'application/json, text/plain, */*',
            'accept-language': accept_language,
            'cache-control': 'max-age=0',
            'priority': 'u=0, i',
            'sec-ch-ua': '"Chromium";v="136", "Microsoft Edge";v="136", "Not.A/Brand";v="99"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'navigate',
            'sec-fetch-site': 'same-origin',
            'upgrade-insecure-requests': '1',
            'user-agent': user_agent,
        }

    def update_crawl_stats(self, weibo_count=0, request_count=0, api_error=False):
        """更新爬取统计"""
        if not self.anti_ban_enabled:
            return

        if weibo_count > 0:
            self.crawl_stats["weibo_count"] += weibo_count
            self.crawl_stats["batch_count"] += weibo_count

        if request_count > 0:
            self.crawl_stats["request_count"] += request_count

        if api_error:
            self.crawl_stats["api_errors"] += 1

    def reset_crawl_stats(self):
        """重置爬取统计（休息后调用）"""
        self.crawl_stats = {
            "weibo_count": 0,
            "request_count": 0,
            "api_errors": 0,
            "start_time": time.time(),
            "batch_count": 0,
            "last_batch_time": None
        }
        logger.info("爬取统计已重置，继续爬取")

    def perform_anti_ban_rest(self):
        """执行防封禁休息"""
        if not self.anti_ban_enabled:
            return

        config = self.anti_ban_config
        rest_time_min = config.get("rest_time_min", 600)
        
        # 添加随机波动（±10%）
        rest_time = int(rest_time_min * random.uniform(0.9, 1.1))
        
        logger.info("┌────────────────────────────────────┐")
        logger.info("│ 🛡️ 防封禁休息中...                 │")
        logger.info("│ 休息时间: %-4d 秒                  │", rest_time)
        logger.info("│ 预计恢复: %s       │", 
                   (datetime.now() + timedelta(seconds=rest_time)).strftime("%H:%M:%S"))
        logger.info("└────────────────────────────────────┘")
        
        # 执行休息
        sleep(rest_time)
        
        logger.info("休息结束，继续爬取微博")

    def validate_config(self, config):
        """验证配置是否正确"""

        # 验证如下1/0相关值
        argument_list = [
            "only_crawl_original",
            "original_pic_download",
            "retweet_pic_download",
            "original_video_download",
            "retweet_video_download",
            "original_live_photo_download",
            "retweet_live_photo_download",
            "download_comment",
            "download_repost",
        ]
        for argument in argument_list:
            # 使用 get() 获取值，新增字段默认为0
            value = config.get(argument, 0)
            if value != 0 and value != 1:
                logger.warning("%s值应为0或1,请重新输入", argument)
                sys.exit()

        # 验证query_list
        query_list = config.get("query_list") or []
        if (not isinstance(query_list, list)) and (not isinstance(query_list, str)):
            logger.warning("query_list值应为list类型或字符串,请重新输入")
            sys.exit()

        # 验证write_mode
        write_mode = ["csv", "json", "mongo", "mysql", "sqlite", "post", "markdown"]
        if not isinstance(config["write_mode"], list):
            sys.exit("write_mode值应为list类型")
        for mode in config["write_mode"]:
            if mode not in write_mode:
                logger.warning(
                    "%s为无效模式，请从csv、json、mongo、mysql、sqlite、post、markdown中挑选一个或多个作为write_mode", mode
                )
                sys.exit()
        # 验证运行模式
        if "sqlite" not in config["write_mode"] and const.MODE == "append":
            logger.warning("append模式下请将sqlite加入write_mode中")
            sys.exit()

        # 验证user_id_list
        user_id_list = config["user_id_list"]
        if (not isinstance(user_id_list, list)) and (not user_id_list.endswith(".txt")):
            logger.warning("user_id_list值应为list类型或txt文件路径")
            sys.exit()
        if not isinstance(user_id_list, list):
            if not os.path.isabs(user_id_list):
                user_id_list = (
                    os.path.split(os.path.realpath(__file__))[0] + os.sep + user_id_list
                )
            if not os.path.isfile(user_id_list):
                logger.warning("不存在%s文件", user_id_list)
                sys.exit()

        # 验证since_date
        since_date = config["since_date"]
        if (not isinstance(since_date, int)) and (not self.is_datetime(since_date)) and (not self.is_date(since_date)):
            logger.warning("since_date值应为yyyy-mm-dd形式、yyyy-mm-ddTHH:MM:SS形式或整数，请重新输入")
            sys.exit()

        comment_max_count = config["comment_max_download_count"]
        if not isinstance(comment_max_count, int):
            logger.warning("最大下载评论数 (comment_max_download_count) 应为整数类型")
            sys.exit()
        elif comment_max_count < 0:
            logger.warning("最大下载评论数 (comment_max_download_count) 应该为正整数")
            sys.exit()

        repost_max_count = config["repost_max_download_count"]
        if not isinstance(repost_max_count, int):
            logger.warning("最大下载转发数 (repost_max_download_count) 应为整数类型")
            sys.exit()
        elif repost_max_count < 0:
            logger.warning("最大下载转发数 (repost_max_download_count) 应该为正整数")
            sys.exit()

    def is_datetime(self, since_date):
        """判断日期格式是否为 %Y-%m-%dT%H:%M:%S"""
        try:
            datetime.strptime(since_date, DTFORMAT)
            return True
        except ValueError:
            return False
    
    def is_date(self, since_date):
        """判断日期格式是否为 %Y-%m-%d"""
        try:
            datetime.strptime(since_date, "%Y-%m-%d")
            return True
        except ValueError:
            return False

    def get_json(self, params):
        url = "https://m.weibo.cn/api/container/getIndex?"
        try:
            r = self.session.get(url, params=params, headers=self.headers, verify=False, timeout=10)
            r.raise_for_status()
            response_json = r.json()
            return response_json, r.status_code
        except RequestException as e:
            logger.error(f"请求失败，错误信息：{e}")
            return {}, 500
        except ValueError as ve:
            logger.error(f"JSON 解码失败，错误信息：{ve}")
            return {}, 500

    def handle_captcha(self, js):
        """
        处理验证码挑战，提示用户手动完成验证。

        参数:
            js (dict): API 返回的 JSON 数据。

        返回:
            bool: 如果用户成功完成验证码，返回 True；否则返回 False。
        """
        logger.debug(f"收到的 JSON 数据：{js}")
        
        captcha_url = js.get("url")
        if captcha_url:
            logger.warning("检测到验证码挑战。正在打开验证码页面以供手动验证。")
            webbrowser.open(captcha_url)
        else:
            logger.warning("检测到可能的验证码挑战，但未提供验证码 URL。请手动检查浏览器并完成验证码验证。")
            return False
        
        logger.info("请在打开的浏览器窗口中完成验证码验证。")
        while True:
            try:
                # 等待用户输入
                user_input = input("完成验证码后，请输入 'y' 继续，或输入 'q' 退出：").strip().lower()

                if user_input == 'y':
                    logger.info("用户输入 'y'，继续爬取。")
                    return True
                elif user_input == 'q':
                    logger.warning("用户选择退出，程序中止。")
                    sys.exit("用户选择退出，程序中止。")
                else:
                    logger.warning("无效输入，请重新输入 'y' 或 'q'。")
            except EOFError:
                logger.error("读取用户输入时发生 EOFError，程序退出。")
                sys.exit("输入流已关闭，程序中止。")
    
    def get_weibo_json(self, page):
        """获取网页中微博json数据"""
        url = "https://m.weibo.cn/api/container/getIndex?"
        params = (
            {
                "container_ext": "profile_uid:" + str(self.user_config["user_id"]),
                "containerid": "100103type=401&q=" + self.query,
                "page_type": "searchall",
            }
            if self.query
            else {"containerid": "230413" + str(self.user_config["user_id"])}
        )
        params["page"] = page
        params["count"] = self.page_weibo_count
        max_retries = 5
        retries = 0
        backoff_factor = 5

        while retries < max_retries:
            try:
                # 防封禁：使用随机请求头
                current_headers = self.get_random_headers()

                # 防封禁：动态延迟
                delay = self.calculate_dynamic_delay()
                if delay > 0:
                    logger.debug(f"动态延迟: {delay:.1f} 秒")
                    sleep(delay)

                response = self.session.get(url, params=params, headers=current_headers, timeout=10)
                response.raise_for_status()  # 如果响应状态码不是 200，会抛出 HTTPError
                js = response.json()

                # 更新统计：成功请求
                self.update_crawl_stats(request_count=1)

                if 'data' in js:
                    logger.info(f"成功获取到页面 {page} 的数据。")
                    return js
                else:
                    logger.warning("未能获取到数据，可能需要验证码验证。")
                    if self.handle_captcha(js):
                        logger.info("用户已完成验证码验证，继续请求数据。")
                        retries = 0  # 重置重试计数器
                        continue
                    else:
                        logger.error("验证码验证失败或未完成，程序将退出。")
                        sys.exit()
            except RequestException as e:
                retries += 1
                sleep_time = backoff_factor * (2 ** retries)
                logger.error(f"请求失败，错误信息：{e}。等待 {sleep_time} 秒后重试...")
                sleep(sleep_time)
                # 更新统计：API错误
                self.update_crawl_stats(api_error=True)
            except ValueError as ve:
                retries += 1
                sleep_time = backoff_factor * (2 ** retries)
                logger.error(f"JSON 解码失败，错误信息：{ve}。等待 {sleep_time} 秒后重试...")
                sleep(sleep_time)
                # 更新统计：API错误
                self.update_crawl_stats(api_error=True)

        logger.error("超过最大重试次数，跳过当前页面。")
        return {}
    
    def user_to_csv(self):
        """将爬取到的用户信息写入csv文件"""
        file_dir = os.path.split(os.path.realpath(__file__))[0] + os.sep + "weibo"
        if not os.path.isdir(file_dir):
            os.makedirs(file_dir)
        file_path = file_dir + os.sep + "users.csv"
        self.user_csv_file_path = file_path
        result_headers = [
            "用户id",
            "昵称",
            "性别",
            "生日",
            "所在地",
            "IP属地",
            "学习经历",
            "公司",
            "注册时间",
            "阳光信用",
            "微博数",
            "粉丝数",
            "关注数",
            "简介",
            "主页",
            "头像",
            "高清头像",
            "微博等级",
            "会员等级",
            "是否认证",
            "认证类型",
            "认证信息",
            "上次记录微博信息",
        ]
        result_data = [
            [
                v.encode("utf-8") if "unicode" in str(type(v)) else v
                for v in self.user.values()
            ]
        ]
        # 已经插入信息的用户无需重复插入，返回的id是空字符串或微博id 发布日期%Y-%m-%d
        last_weibo_msg = csvutil.insert_or_update_user(
            logger, result_headers, result_data, file_path
        )
        self.last_weibo_id = last_weibo_msg.split(" ")[0] if last_weibo_msg else ""
        self.last_weibo_date = (
            last_weibo_msg.split(" ")[1]
            if last_weibo_msg
            else self.user_config["since_date"]
        )

    def user_to_mongodb(self):
        """将爬取的用户信息写入MongoDB数据库"""
        user_list = [self.user]
        self.info_to_mongodb("user", user_list)
        logger.info("%s信息写入MongoDB数据库完毕", self.user["screen_name"])

    def user_to_mysql(self):
        """将爬取的用户信息写入MySQL数据库"""
        mysql_config = {
            "host": "localhost",
            "port": 3306,
            "user": "root",
            "password": "123456",
            "charset": "utf8mb4",
        }
        # 创建'weibo'数据库
        create_database = """CREATE DATABASE IF NOT EXISTS weibo DEFAULT
                         CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"""
        self.mysql_create_database(mysql_config, create_database)
        # 创建'user'表
        create_table = """
                CREATE TABLE IF NOT EXISTS user (
                id varchar(20) NOT NULL,
                screen_name varchar(30),
                gender varchar(10),
                statuses_count INT,
                followers_count INT,
                follow_count INT,
                registration_time varchar(20),
                sunshine varchar(20),
                birthday varchar(40),
                location varchar(200),
                ip_location varchar(50),
                education varchar(200),
                company varchar(200),
                description varchar(400),
                profile_url varchar(200),
                profile_image_url varchar(200),
                avatar_hd varchar(200),
                urank INT,
                mbrank INT,
                verified BOOLEAN DEFAULT 0,
                verified_type INT,
                verified_reason varchar(140),
                PRIMARY KEY (id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
        self.mysql_create_table(mysql_config, create_table)
        self.mysql_insert(mysql_config, "user", [self.user])
        logger.info("%s信息写入MySQL数据库完毕", self.user["screen_name"])

    def user_to_database(self):
        """将用户信息写入文件/数据库"""
        self.user_to_csv()
        if "mysql" in self.write_mode:
            self.user_to_mysql()
        if "mongo" in self.write_mode:
            self.user_to_mongodb()
        if "sqlite" in self.write_mode:
            self.user_to_sqlite()

    def get_user_info(self):
        """获取用户信息"""
        params = {"containerid": "100505" + str(self.user_config["user_id"])}
        url = "https://m.weibo.cn/api/container/getIndex"
        
        # 这里在读取下一个用户的时候很容易被ban，需要优化休眠时长
        # 加一个count，不需要一上来啥都没干就sleep
        if self.long_sleep_count_before_each_user > 0:
            sleep_time = random.randint(30, 60)
            # 添加log，否则一般用户不知道以为程序卡了
            logger.info(f"""短暂sleep {sleep_time}秒，避免被ban""")        
            sleep(sleep_time)
            logger.info("sleep结束")  
        self.long_sleep_count_before_each_user = self.long_sleep_count_before_each_user + 1      

        max_retries = 5  # 设置最大重试次数，避免无限循环
        retries = 0
        backoff_factor = 5  # 指数退避的基数（秒）
        
        while retries < max_retries:
            try:
                logger.info(f"准备获取ID：{self.user_config['user_id']}的用户信息第{retries+1}次。")

                # 防封禁：使用随机请求头
                current_headers = self.get_random_headers()

                # 防封禁：动态延迟
                delay = self.calculate_dynamic_delay()
                if delay > 0:
                    logger.debug(f"动态延迟: {delay:.1f} 秒")
                    sleep(delay)

                response = self.session.get(url, params=params, headers=current_headers, timeout=10)
                response.raise_for_status()
                js = response.json()

                # 更新统计：成功请求
                self.update_crawl_stats(request_count=1)
                if 'data' in js and 'userInfo' in js['data']:
                    info = js["data"]["userInfo"]
                    user_info = OrderedDict()
                    user_info["id"] = self.user_config["user_id"]
                    user_info["screen_name"] = info.get("screen_name", "")
                    user_info["gender"] = info.get("gender", "")
                    params = {
                        "containerid": "230283" + str(self.user_config["user_id"]) + "_-_INFO"
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
                    js, _ = self.get_json(params)
                    if js["ok"]:
                        cards = js["data"]["cards"]
                        if isinstance(cards, list) and len(cards) > 1:
                            card_list = cards[0]["card_group"] + cards[1]["card_group"]
                            for card in card_list:
                                if card.get("item_name") in zh_list:
                                    user_info[
                                        en_list[zh_list.index(card.get("item_name"))]
                                    ] = card.get("item_content", "")
                    user_info["statuses_count"] = self.string_to_int(
                        info.get("statuses_count", 0)
                    )
                    user_info["followers_count"] = self.string_to_int(
                        info.get("followers_count", 0)
                    )
                    user_info["follow_count"] = self.string_to_int(info.get("follow_count", 0))
                    user_info["description"] = info.get("description", "")
                    user_info["profile_url"] = info.get("profile_url", "")
                    user_info["profile_image_url"] = info.get("profile_image_url", "")
                    user_info["avatar_hd"] = info.get("avatar_hd", "")
                    user_info["urank"] = info.get("urank", 0)
                    user_info["mbrank"] = info.get("mbrank", 0)
                    user_info["verified"] = info.get("verified", False)
                    user_info["verified_type"] = info.get("verified_type", -1)
                    user_info["verified_reason"] = info.get("verified_reason", "")
                    self.user = self.standardize_info(user_info)
                    self.user_to_database()
                    logger.info(f"成功获取到用户 {self.user_config['user_id']} 的信息。")
                    return 0
                elif isinstance(js.get("url"), str) and js.get("url").strip():
                    logger.warning("未能获取到用户信息，可能需要验证码验证。")
                    if self.handle_captcha(js):
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
                sleep_time = backoff_factor * (2 ** retries)
                logger.error(f"请求失败，错误信息：{e}。等待 {sleep_time} 秒后重试...")
                sleep(sleep_time)
                # 更新统计：API错误
                self.update_crawl_stats(api_error=True)
            except ValueError as ve:
                retries += 1
                sleep_time = backoff_factor * (2 ** retries)
                logger.error(f"JSON 解码失败，错误信息：{ve}。等待 {sleep_time} 秒后重试...")
                sleep(sleep_time)
                # 更新统计：API错误
                self.update_crawl_stats(api_error=True)
        logger.error("超过最大重试次数，程序将退出。")
        sys.exit("超过最大重试次数，程序已退出。")

    def get_long_weibo(self, id):
        """获取长微博"""
        url = "https://m.weibo.cn/detail/%s" % id
        logger.info(f"""URL: {url} """)
        for i in range(5):
            sleep(random.uniform(1.0, 2.5))
            html = self.session.get(url, headers=self.headers, verify=False).text
            html = html[html.find('"status":') :]
            html = html[: html.rfind('"call"')]
            html = html[: html.rfind(",")]
            html = "{" + html + "}"
            js = json.loads(html, strict=False)
            weibo_info = js.get("status")
            if weibo_info:
                weibo = self.parse_weibo(weibo_info)
                return weibo

    def get_pics(self, weibo_info):
        """获取微博原始图片url"""
        if weibo_info.get("pics"):
            pic_info = weibo_info["pics"]
            pic_list = [
                pic['large']['url'] for pic in pic_info
                if isinstance(pic, dict) and pic.get('large')
            ]
            pics = ",".join(pic_list)
        else:
            pics = ""
        return pics


    def get_live_photo_url(self, weibo_info):
        """获取Live Photo视频URL"""
        live_photo_list = weibo_info.get("live_photo", [])
        return ";".join(live_photo_list) if live_photo_list else ""
    def get_video_url(self, weibo_info):
        """获取微博普通视频URL"""
        video_url = ""
        if weibo_info.get("page_info"):
            if weibo_info["page_info"].get("type") == "video":
                media_info = weibo_info["page_info"].get("urls") or weibo_info["page_info"].get("media_info")
                if media_info:
                    video_url = (media_info.get("mp4_720p_mp4") or
                                media_info.get("mp4_hd_url") or
                                media_info.get("hevc_mp4_hd") or
                                media_info.get("mp4_sd_url") or
                                media_info.get("mp4_ld_mp4") or
                                media_info.get("stream_url_hd") or
                                media_info.get("stream_url"))
        return video_url

    def write_exif_time(self, file_path, time_str):
        if self.write_time_in_exif:
            """写入 JPG EXIF 元数据"""
            try:
                # 将 "2025-09-06T22:16:36" 转换为 "2025:09:06 22:16:36"
                exif_time = time_str.replace("-", ":").replace("T", " ")[:19]
                exif_dict = {"Exif": {piexif.ExifIFD.DateTimeOriginal: exif_time}}
                exif_bytes = piexif.dump(exif_dict)
                piexif.insert(exif_bytes, file_path)
                logger.debug(f"[EXIF] 已将时间 {exif_time} 写入 {file_path}")
            except Exception as e:
                logger.debug(f"EXIF写入跳过或失败: {e}")

    def set_file_time(self, file_path, time_str):
        if self.change_file_time:
            """修改文件系统时间（修改日期）"""
            try:
                # 兼容带 T 或不带 T 的格式
                clean_time = time_str.replace("T", " ")
                tick = time.mktime(time.strptime(clean_time, "%Y-%m-%d %H:%M:%S"))
                # 同时修改访问时间和修改时间
                os.utime(file_path, (tick, tick))
                logger.debug(f"[FILE] 已将时间 {clean_time} 写入 {file_path}")
            except Exception as e:
                logger.debug(f"修改文件系统时间失败: {e}")

    def download_one_file(self, url, file_path, type, weibo_id, created_at):
        """下载单个文件(图片/视频)"""
        try:

            file_exist = os.path.isfile(file_path)
            need_download = (not file_exist)
            sqlite_exist = False
            if "sqlite" in self.write_mode:
                sqlite_exist = self.sqlite_exist_file(file_path)

            if not need_download:
                return 

            s = requests.Session()
            s.mount('http://', HTTPAdapter(max_retries=5))
            s.mount('https://', HTTPAdapter(max_retries=5))
            try_count = 0
            success = False
            MAX_TRY_COUNT = 3
            detected_extension = None
            while try_count < MAX_TRY_COUNT:
                try:
                    response = s.get(
                        url, headers=self.headers, timeout=(5, 10), verify=False
                    )
                    response.raise_for_status()
                    downloaded = response.content
                    try_count += 1

                    # 获取文件后缀
                    url_path = url.split('?')[0]  # 去除URL中的参数
                    inferred_extension = os.path.splitext(url_path)[1].lower().strip('.')

                    # 通过 Magic Number 检测文件类型
                    if downloaded.startswith(b'\xFF\xD8\xFF'):
                        # JPEG 文件
                        if not downloaded.endswith(b'\xff\xd9'):
                            logger.debug(f"[DEBUG] JPEG 文件不完整: {url} ({try_count}/{MAX_TRY_COUNT})")
                            continue  # 文件不完整，继续重试
                        detected_extension = '.jpg'
                    elif downloaded.startswith(b'\x89PNG\r\n\x1A\n'):
                        # PNG 文件
                        if not downloaded.endswith(b'IEND\xaeB`\x82'):
                            logger.debug(f"[DEBUG] PNG 文件不完整: {url} ({try_count}/{MAX_TRY_COUNT})")
                            continue  # 文件不完整，继续重试
                        detected_extension = '.png'
                    else:
                        # 其他类型，使用原有逻辑处理
                        if inferred_extension in ['mp4', 'mov', 'webm', 'gif', 'bmp', 'tiff']:
                            detected_extension = '.' + inferred_extension
                        else:
                            # 尝试从 Content-Type 获取扩展名
                            content_type = response.headers.get('Content-Type', '').lower()
                            if 'image/jpeg' in content_type:
                                detected_extension = '.jpg'
                            elif 'image/png' in content_type:
                                detected_extension = '.png'
                            elif 'video/mp4' in content_type:
                                detected_extension = '.mp4'
                            elif 'video/quicktime' in content_type:
                                detected_extension = '.mov'
                            elif 'video/webm' in content_type:
                                detected_extension = '.webm'
                            elif 'image/gif' in content_type:
                                detected_extension = '.gif'
                            else:
                                # 使用原有的扩展名，如果无法确定
                                detected_extension = '.' + inferred_extension if inferred_extension else ''

                    # 动态调整文件路径的扩展名
                    if detected_extension:
                        file_path = re.sub(r'\.\w+$', detected_extension, file_path)

                    # 保存文件
                    if not os.path.isfile(file_path):
                        with open(file_path, "wb") as f:
                            f.write(downloaded)
                            logger.debug("[DEBUG] save " + file_path)
                        if detected_extension in ['.jpg', '.jpeg']:
                            try:
                                self.write_exif_time(file_path, created_at)
                            except Exception as e:
                                logger.error(f"写入EXIF失败: {e}")
                        try:
                            # 1. 无论什么格式，都修改系统时间 (方便文件夹排序)
                            self.set_file_time(file_path, created_at)
                        except Exception as e:
                            logger.error(f"修改文件系统时间失败: {e}")

                    success = True
                    logger.debug("[DEBUG] success " + url + "  " + str(try_count))
                    break  # 下载成功，退出重试循环

                except RequestException as e:
                    try_count += 1
                    logger.error(f"[ERROR] 请求失败，错误信息：{e}。尝试次数：{try_count}/{MAX_TRY_COUNT}")
                    sleep_time = 2 ** try_count  # 指数退避
                    sleep(sleep_time)
                except Exception as e:
                    logger.exception(f"[ERROR] 下载过程中发生错误: {e}")
                    break  # 对于其他异常，退出重试

            if success:
                if "sqlite" in self.write_mode and not sqlite_exist:
                    self.insert_file_sqlite(
                        file_path, weibo_id, url, downloaded
                    )
            else:
                logger.debug("[DEBUG] failed " + url + " TOTALLY")
                error_file = self.get_filepath(type) + os.sep + "not_downloaded.txt"
                with open(error_file, "ab") as f:
                    error_entry = f"{weibo_id}:{file_path}:{url}\n"
                    f.write(error_entry.encode(sys.stdout.encoding))
        except Exception as e:
            # 生成原始微博URL
            original_url = f"https://m.weibo.cn/detail/{weibo_id}"  # 新增
            error_file = self.get_filepath(type) + os.sep + "not_downloaded.txt"
            with open(error_file, "ab") as f:
                # 修改错误条目格式，添加原始URL
                error_entry = f"{weibo_id}:{file_path}:{url}:{original_url}\n"  # 修改
                f.write(error_entry.encode(sys.stdout.encoding))
            logger.exception(e)

    def sqlite_exist_file(self, url):
        if not os.path.exists(self.get_sqlte_path()):
            return True
        con = self.get_sqlite_connection()
        cur = con.cursor()

        query_sql = """SELECT url FROM bins WHERE path=? """
        count = cur.execute(query_sql, (url,)).fetchone()
        con.close()
        if count is None:
            return False

        return True

    def insert_file_sqlite(self, file_path, weibo_id, url, binary):
        if not weibo_id:
            return
        if self.store_binary_in_sqlite != 1:  # 新增配置判断
            return
        extension = Path(file_path).suffix
        if not extension:
            return
        if len(binary) <= 0:
            return

        file_data = OrderedDict()
        file_data["weibo_id"] = weibo_id
        file_data["ext"] = extension
        file_data["data"] = binary  # 仅当启用时存储二进制
        file_data["path"] = file_path
        file_data["url"] = url

        con = self.get_sqlite_connection()
        self.sqlite_insert(con, file_data, "bins")
        con.close()

    def handle_download(self, file_type, file_dir, urls, w):
        """处理下载相关操作"""
        file_prefix = w["created_at"][:11].replace("-", "") + "_" + str(w["id"])
        if file_type == "img":
            if "," in urls:
                url_list = urls.split(",")
                for i, url in enumerate(url_list):
                    index = url.rfind(".")
                    if len(url) - index >= 5:
                        file_suffix = ".jpg"
                    else:
                        file_suffix = url[index:]
                    file_name = file_prefix + "_" + str(i + 1) + file_suffix
                    file_path = file_dir + os.sep + file_name
                    self.download_one_file(url, file_path, file_type, w["id"], w["created_at"])
            else:
                index = urls.rfind(".")
                if len(urls) - index > 5:
                    file_suffix = ".jpg"
                else:
                    file_suffix = urls[index:]
                file_name = file_prefix + file_suffix
                file_path = file_dir + os.sep + file_name
                self.download_one_file(urls, file_path, file_type, w["id"], w["created_at"])
        elif file_type == "video" or file_type == "live_photo":
            file_suffix = ".mp4"
            if ";" in urls:
                url_list = urls.split(";")
                for i, url in enumerate(url_list):
                    if url.endswith(".mov"):
                        file_suffix = ".mov"
                    file_name = file_prefix + "_" + str(i + 1) + file_suffix
                    file_path = file_dir + os.sep + file_name
                    self.download_one_file(url, file_path, file_type, w["id"], w["created_at"])
            else:
                if urls.endswith(".mov"):
                    file_suffix = ".mov"
                file_name = file_prefix + file_suffix
                file_path = file_dir + os.sep + file_name
                self.download_one_file(urls, file_path, file_type, w["id"], w["created_at"])

    def download_files(self, file_type, weibo_type, wrote_count):
        try:
            describe = ""
            if file_type == "img":
                describe = "图片"
                key = "pics"
            elif file_type == "video":
                describe = "视频"
                key = "video_url"
            elif file_type == "live_photo":
                describe = "Live Photo视频"
                key = "live_photo_url"
            else:
                return
            
            if weibo_type == "original":
                describe = "原创微博" + describe
            else:
                describe = "转发微博" + describe
            
            logger.info("即将进行%s下载", describe)
            file_dir = self.get_filepath(file_type)
            file_dir = file_dir + os.sep + describe
            
            # 检查是否有文件需要下载
            has_files = False
            for w in self.weibo[wrote_count:]:
                if weibo_type == "retweet":
                    if w.get("retweet"):
                        w = w["retweet"]
                    else:
                        continue
                if w.get(key):
                    has_files = True
                    break
            
            if has_files:
                if not os.path.isdir(file_dir):
                    os.makedirs(file_dir)
                
                for w in tqdm(self.weibo[wrote_count:], desc="Download progress"):
                    if weibo_type == "retweet":
                        if w.get("retweet"):
                            w = w["retweet"]
                        else:
                            continue
                    if w.get(key):
                        self.handle_download(file_type, file_dir, w.get(key), w)
                
                logger.info("%s下载完毕,保存路径:", describe)
                logger.info(file_dir)
            else:
                logger.info("没有%s需要下载", describe)
        except Exception as e:
            logger.exception(e)

    def get_location(self, selector):
        """获取微博发布位置"""
        location_icon = "timeline_card_small_location_default.png"
        span_list = selector.xpath("//span")
        location = ""
        for i, span in enumerate(span_list):
            if span.xpath("img/@src"):
                if location_icon in span.xpath("img/@src")[0]:
                    location = span_list[i + 1].xpath("string(.)")
                    break
        return location

    def get_article_url(self, selector):
        """获取微博中头条文章的url"""
        article_url = ""
        text = selector.xpath("string(.)")
        if text.startswith("发布了头条文章"):
            url = selector.xpath("//a/@data-url")
            if url and url[0].startswith("http://t.cn"):
                article_url = url[0]
        return article_url

    def get_topics(self, selector):
        """获取参与的微博话题"""
        span_list = selector.xpath("//span[@class='surl-text']")
        topics = ""
        topic_list = []
        for span in span_list:
            text = span.xpath("string(.)")
            if len(text) > 2 and text[0] == "#" and text[-1] == "#":
                topic_list.append(text[1:-1])
        if topic_list:
            topics = ",".join(topic_list)
        return topics

    def get_at_users(self, selector):
        """获取@用户"""
        a_list = selector.xpath("//a")
        at_users = ""
        at_list = []
        for a in a_list:
            if "@" + a.xpath("@href")[0][3:] == a.xpath("string(.)"):
                at_list.append(a.xpath("string(.)")[1:])
        if at_list:
            at_users = ",".join(at_list)
        return at_users

    def string_to_int(self, string):
        """字符串转换为整数"""
        if isinstance(string, int):
            return string
        elif string.endswith("万+"):
            string = string[:-2] + "0000"
        elif string.endswith("万"):
            string = float(string[:-1]) * 10000
        elif string.endswith("亿"):
            string = float(string[:-1]) * 100000000
        return int(string)

    def standardize_date(self, created_at):
        """标准化微博发布时间"""
        if "刚刚" in created_at:
            ts = datetime.now()
        elif "分钟" in created_at:
            minute = created_at[: created_at.find("分钟")]
            minute = timedelta(minutes=int(minute))
            ts = datetime.now() - minute
        elif "小时" in created_at:
            hour = created_at[: created_at.find("小时")]
            hour = timedelta(hours=int(hour))
            ts = datetime.now() - hour
        elif "昨天" in created_at:
            day = timedelta(days=1)
            ts = datetime.now() - day
        else:
            created_at = created_at.replace("+0800 ", "")
            ts = datetime.strptime(created_at, "%c")

        created_at = ts.strftime(DTFORMAT)
        full_created_at = ts.strftime("%Y-%m-%d %H:%M:%S")
        return created_at, full_created_at

    def standardize_info(self, weibo):
        """标准化信息，去除乱码"""
        for k, v in weibo.items():
            if (
                "bool" not in str(type(v))
                and "int" not in str(type(v))
                and "list" not in str(type(v))
                and "long" not in str(type(v))
            ):
                weibo[k] = (
                    v.replace("\u200b", "")
                    .encode(sys.stdout.encoding, "ignore")
                    .decode(sys.stdout.encoding)
                )
        return weibo

    def parse_weibo(self, weibo_info):
        weibo = OrderedDict()
        if weibo_info["user"]:
            weibo["user_id"] = weibo_info["user"]["id"]
            weibo["screen_name"] = weibo_info["user"]["screen_name"]
        else:
            weibo["user_id"] = ""
            weibo["screen_name"] = ""
        weibo["id"] = int(weibo_info["id"])
        weibo["bid"] = weibo_info["bid"]
        text_body = weibo_info["text"]
        selector = etree.HTML(f"{text_body}<hr>" if text_body.isspace() else text_body)
        if self.remove_html_tag:
            text_list = selector.xpath("//text()")
            # 若text_list中的某个字符串元素以 @ 或 # 开始，则将该元素与前后元素合并为新元素，否则会带来没有必要的换行
            text_list_modified = []
            for ele in range(len(text_list)):
                if ele > 0 and (text_list[ele-1].startswith(('@','#')) or text_list[ele].startswith(('@','#'))):
                    text_list_modified[-1] += text_list[ele]
                else:
                    text_list_modified.append(text_list[ele])
            weibo["text"] = "\n".join(text_list_modified)
        else:
            weibo["text"] = text_body
        weibo["article_url"] = self.get_article_url(selector)
        weibo["pics"] = self.get_pics(weibo_info)
        weibo["video_url"] = self.get_video_url(weibo_info)  # 普通视频URL
        weibo["live_photo_url"] = self.get_live_photo_url(weibo_info)  # Live Photo视频URL
        weibo["location"] = self.get_location(selector)
        weibo["created_at"] = weibo_info["created_at"]
        weibo["source"] = weibo_info["source"]
        weibo["attitudes_count"] = self.string_to_int(
            weibo_info.get("attitudes_count", 0)
        )
        weibo["comments_count"] = self.string_to_int(
            weibo_info.get("comments_count", 0)
        )
        weibo["reposts_count"] = self.string_to_int(weibo_info.get("reposts_count", 0))
        weibo["topics"] = self.get_topics(selector)
        weibo["at_users"] = self.get_at_users(selector)
        
        # 使用 LLM 分析微博内容
        if self.llm_analyzer:
            weibo = self.llm_analyzer.analyze_weibo(weibo)
            logger.info("完整分析结果：\n%s", json.dumps(weibo, ensure_ascii=False, indent=2))
        return self.standardize_info(weibo)

    def print_user_info(self):
        """打印用户信息"""
        logger.info("+" * 100)
        logger.info("用户信息")
        logger.info("用户id：%s", self.user["id"])
        logger.info("用户昵称：%s", self.user["screen_name"])
        gender = "女" if self.user["gender"] == "f" else "男"
        logger.info("性别：%s", gender)
        logger.info("生日：%s", self.user["birthday"])
        logger.info("所在地：%s", self.user["location"])
        logger.info("IP属地：%s", self.user.get("ip_location", "未获取"))        
        logger.info("教育经历：%s", self.user["education"])
        logger.info("公司：%s", self.user["company"])
        logger.info("阳光信用：%s", self.user["sunshine"])
        logger.info("注册时间：%s", self.user["registration_time"])
        logger.info("微博数：%d", self.user["statuses_count"])
        logger.info("粉丝数：%d", self.user["followers_count"])
        logger.info("关注数：%d", self.user["follow_count"])
        logger.info("url：https://m.weibo.cn/profile/%s", self.user["id"])
        if self.user.get("verified_reason"):
            logger.info(self.user["verified_reason"])
        logger.info(self.user["description"])
        logger.info("+" * 100)

    def print_one_weibo(self, weibo):
        """打印一条微博"""
        try:
            logger.info("微博id：%d", weibo["id"])
            logger.info("微博正文：%s", weibo["text"])
            logger.info("原始图片url：%s", weibo["pics"])
            logger.info("微博位置：%s", weibo["location"])
            logger.info("发布时间：%s", weibo["created_at"])
            logger.info("发布工具：%s", weibo["source"])
            logger.info("点赞数：%d", weibo["attitudes_count"])
            logger.info("评论数：%d", weibo["comments_count"])
            logger.info("转发数：%d", weibo["reposts_count"])
            logger.info("话题：%s", weibo["topics"])
            logger.info("@用户：%s", weibo["at_users"])
            logger.info("已编辑，编辑次数：%d" % weibo.get("edit_count", 0) if weibo.get("edited") else "未编辑")            
            logger.info("url：https://m.weibo.cn/detail/%d", weibo["id"])
        except OSError:
            pass

    def print_weibo(self, weibo):
        """打印微博，若为转发微博，会同时打印原创和转发部分"""
        if weibo.get("retweet"):
            logger.info("*" * 100)
            logger.info("转发部分：")
            self.print_one_weibo(weibo["retweet"])
            logger.info("*" * 100)
            logger.info("原创部分：")
        self.print_one_weibo(weibo)
        logger.info("-" * 120)

    def get_one_weibo(self, info):
        """获取一条微博的全部信息"""
        try:
            weibo_info = info["mblog"]
            weibo_id = weibo_info["id"]
            retweeted_status = weibo_info.get("retweeted_status")
            is_long = (
                True if weibo_info.get("pic_num") > 9 else weibo_info.get("isLongText")
            )
            if retweeted_status and retweeted_status.get("id"):  # 转发
                retweet_id = retweeted_status.get("id")
                is_long_retweet = retweeted_status.get("isLongText")
                if is_long:
                    weibo = self.get_long_weibo(weibo_id)
                    if not weibo:
                        weibo = self.parse_weibo(weibo_info)
                else:
                    weibo = self.parse_weibo(weibo_info)
                if is_long_retweet:
                    retweet = self.get_long_weibo(retweet_id)
                    if not retweet:
                        retweet = self.parse_weibo(retweeted_status)
                else:
                    retweet = self.parse_weibo(retweeted_status)
                (
                    retweet["created_at"],
                    retweet["full_created_at"],
                ) = self.standardize_date(retweeted_status["created_at"])
                weibo["retweet"] = retweet
            else:  # 原创
                if is_long:
                    weibo = self.get_long_weibo(weibo_id)
                    if not weibo:
                        weibo = self.parse_weibo(weibo_info)
                else:
                    weibo = self.parse_weibo(weibo_info)
            weibo["created_at"], weibo["full_created_at"] = self.standardize_date(
                weibo_info["created_at"]
            )
            edit_count = weibo_info.get("edit_count", 0)
            weibo["edited"] = edit_count > 0
            weibo["edit_count"] = edit_count
            return weibo
        except Exception as e:
            logger.exception(e)

    def get_weibo_comments(self, weibo, max_count, on_downloaded):
        """
        :weibo standardlized weibo
        :max_count 最大允许下载数
        :on_downloaded 下载完成时的实例方法回调
        """
        if weibo["comments_count"] == 0:
            return

        logger.info(
            "正在下载评论 微博id:{id}".format(id=weibo["id"])
        )
        self._get_weibo_comments_cookie(weibo, 0, max_count, None, on_downloaded)

    def get_weibo_reposts(self, weibo, max_count, on_downloaded):
        """
        :weibo standardlized weibo
        :max_count 最大允许下载数
        :on_downloaded 下载完成时的实例方法回调
        """
        if weibo["reposts_count"] == 0:
            return

        logger.info(
            "正在下载转发 微博id:{id}".format(id=weibo["id"])
        )
        self._get_weibo_reposts_cookie(weibo, 0, max_count, 1, on_downloaded)

    def _get_weibo_comments_cookie(
        self, weibo, cur_count, max_count, max_id, on_downloaded
    ):
        """
        :weibo standardlized weibo
        :cur_count  已经下载的评论数
        :max_count 最大允许下载数
        :max_id 微博返回的max_id参数
        :on_downloaded 下载完成时的实例方法回调
        """
        if cur_count >= max_count:
            return

        id = weibo["id"]
        params = {"mid": id}
        if max_id:
            params["max_id"] = max_id
        url = "https://m.weibo.cn/comments/hotflow?max_id_type=0"
        req = self.session.get(
            url,
            params=params,
            headers=self.headers,
        )
        json = None
        error = False
        try:
            json = req.json()
        except Exception as e:
            # 没有cookie会抓取失败
            # 微博日期小于某个日期的用这个url会被403 需要用老办法尝试一下
            error = True

        if error:
            # 最大好像只能有50条 TODO: improvement
            self._get_weibo_comments_nocookie(weibo, 0, max_count, 1, on_downloaded)
            return

        data = json.get("data")
        if not data:
            # 新接口没有抓取到的老接口也试一下
            self._get_weibo_comments_nocookie(weibo, 0, max_count, 1, on_downloaded)
            return

        comments = data.get("data")
        count = len(comments)
        if count == 0:
            # 没有了可以直接跳出递归
            return

        if on_downloaded:
            on_downloaded(weibo, comments)

        # 随机睡眠一下
        if max_count % 40 == 0:
            sleep(random.randint(1, 5))

        cur_count += count
        max_id = data.get("max_id")

        if max_id == 0:
            return

        self._get_weibo_comments_cookie(
            weibo, cur_count, max_count, max_id, on_downloaded
        )

    def _get_weibo_comments_nocookie(
        self, weibo, cur_count, max_count, page, on_downloaded
    ):
        """
        :weibo standardlized weibo
        :cur_count  已经下载的评论数
        :max_count 最大允许下载数
        :page 下载的页码 从 1 开始
        :on_downloaded 下载完成时的实例方法回调
        """
        if cur_count >= max_count:
            return
        id = weibo["id"]
        url = "https://m.weibo.cn/api/comments/show?id={id}&page={page}".format(
            id=id, page=page
        )
        req = self.session.get(url)
        json = None
        try:
            json = req.json()
        except Exception as e:
            logger.warning("未能抓取完整评论 微博id: {id}".format(id=id))
            return

        data = json.get("data")
        if not data:
            return
        comments = data.get("data")
        count = len(comments)
        if count == 0:
            # 没有了可以直接跳出递归
            return

        if on_downloaded:
            on_downloaded(weibo, comments)

        cur_count += count
        page += 1

        # 随机睡眠一下
        if page % 2 == 0:
            sleep(random.randint(1, 5))

        req_page = data.get("max")

        if req_page == 0:
            return

        if page > req_page:
            return
        self._get_weibo_comments_nocookie(
            weibo, cur_count, max_count, page, on_downloaded
        )

    def _get_weibo_reposts_cookie(
        self, weibo, cur_count, max_count, page, on_downloaded
    ):
        """
        :weibo standardlized weibo
        :cur_count  已经下载的转发数
        :max_count 最大允许下载数
        :page 下载的页码 从 1 开始
        :on_downloaded 下载完成时的实例方法回调
        """
        if cur_count >= max_count:
            return
        id = weibo["id"]
        url = "https://m.weibo.cn/api/statuses/repostTimeline"
        params = {"id": id, "page": page}
        req = self.session.get(
            url,
            params=params,
            headers=self.headers,
        )

        json = None
        try:
            json = req.json()
        except Exception as e:
            logger.warning(
                "未能抓取完整转发 微博id: {id}".format(id=id)
            )
            return

        data = json.get("data")
        if not data:
            return
        reposts = data.get("data")
        count = len(reposts)
        if count == 0:
            # 没有了可以直接跳出递归
            return

        if on_downloaded:
            on_downloaded(weibo, reposts)

        cur_count += count
        page += 1

        # 随机睡眠一下
        if page % 2 == 0:
            sleep(random.randint(2, 5))

        req_page = data.get("max")

        if req_page == 0:
            return

        if page > req_page:
            return
        self._get_weibo_reposts_cookie(weibo, cur_count, max_count, page, on_downloaded)



    def get_one_page(self, page):
        """获取一页的全部微博"""
        try:
            js = self.get_weibo_json(page)
            if js["ok"]:
                weibos = js["data"]["cards"]
                
                if self.query:
                    weibos = weibos[0]["card_group"]
                # 如果需要检查cookie，在循环第一个人的时候，就要看看仅自己可见的信息有没有，要是没有直接报错
                for w in weibos:
                    if w["card_type"] == 11:
                        temp = w.get("card_group",[0])
                        if len(temp) >= 1:
                            w = temp[0] or w
                        else:
                            w = w
                    if w["card_type"] == 9:
                        wb = self.get_one_weibo(w)
                        if wb:
                            if (
                                const.CHECK_COOKIE["CHECK"]
                                and (not const.CHECK_COOKIE["CHECKED"])
                                and wb["text"].startswith(
                                    const.CHECK_COOKIE["HIDDEN_WEIBO"]
                                )
                            ):
                                const.CHECK_COOKIE["CHECKED"] = True
                                logger.info("cookie检查通过")
                                if const.CHECK_COOKIE["EXIT_AFTER_CHECK"]:
                                    return True
                            if wb["id"] in self.weibo_id_list:
                                continue
                            created_at = datetime.strptime(wb["created_at"], DTFORMAT)
                            since_date = datetime.strptime(
                                self.user_config["since_date"], DTFORMAT
                            )
                            if const.MODE == "append":
                                # append模式：增量获取微博
                                if self.first_crawler:
                                    # 记录最新微博id，写入上次抓取id的csv
                                    self.latest_weibo_id = str(wb["id"])
                                    csvutil.update_last_weibo_id(
                                        wb["user_id"],
                                        str(wb["id"]) + " " + wb["created_at"],
                                        self.user_csv_file_path,
                                    )
                                    self.first_crawler = False
                                if str(wb["id"]) == self.last_weibo_id:
                                    if const.CHECK_COOKIE["CHECK"] and (
                                        not const.CHECK_COOKIE["CHECKED"]
                                    ):
                                        # 已经爬取过最新的了，只是没检查到cookie，一旦检查通过，直接放行
                                        const.CHECK_COOKIE["EXIT_AFTER_CHECK"] = True
                                        continue
                                    if self.last_weibo_id == self.latest_weibo_id:
                                        logger.info(
                                            "{} 用户没有发新微博".format(
                                                self.user["screen_name"]
                                            )
                                        )
                                    else:
                                        logger.info(
                                            "增量获取微博完毕，将最新微博id从 {} 变更为 {}".format(
                                                self.last_weibo_id, self.latest_weibo_id
                                            )
                                        )
                                    return True
                                # 上一次标记的微博被删了，就把上一条微博时间记录推前两天，多抓点评论或者微博内容修改
                                # TODO 更加合理的流程是，即使读取到上次更新微博id，也抓取增量评论，由此获得更多的评论
                                since_date = datetime.strptime(
                                    convert_to_days_ago(self.last_weibo_date, 1),
                                    DTFORMAT,
                                )
                            if created_at < since_date:
                                # 检查是否为置顶微博
                                is_pinned = w.get("mblog", {}).get("mblogtype", 0) == 2
                                if is_pinned:
                                    logger.debug(f"[置顶微博] 微博ID={wb['id']}, 发布时间={created_at}, 是置顶微博，跳过但继续检查后续微博")
                                    continue
                                
                                logger.debug(f"[日期过滤] 微博ID={wb['id']}, 发布时间={created_at}, 起始时间={since_date}, 被跳过")
                                # 如果要检查还没有检查cookie，不能直接跳出
                                if const.CHECK_COOKIE["CHECK"] and (
                                    not const.CHECK_COOKIE["CHECKED"]
                                ):
                                    continue
                                else:
                                    logger.info(
                                        "{}已获取{}({})的第{}页{}微博{}".format(
                                            "-" * 30,
                                            self.user["screen_name"],
                                            self.user["id"],
                                            page,
                                            '包含"' + self.query + '"的'
                                            if self.query
                                            else "",
                                            "-" * 30,
                                        )
                                    )
                                    return True
                            else:
                                logger.debug(f"[日期通过] 微博ID={wb['id']}, 发布时间={created_at}, 起始时间={since_date}")
                            if (not self.only_crawl_original) or ("retweet" not in wb.keys()):
                                self.weibo.append(wb)
                                self.weibo_id_list.append(wb["id"])
                                self.got_count += 1

                                # 防封禁：更新微博统计
                                self.update_crawl_stats(weibo_count=1)

                                # 防封禁：检查是否需要暂停
                                if self.anti_ban_enabled:
                                    should_pause, reason = self.should_pause_session()
                                    if should_pause:
                                        logger.warning(f"触发防封禁暂停: {reason}")
                                        return "need_rest"  # 返回特殊值表示需要休息

                                # 这里是系统日志输出，尽量别太杂
                                logger.info(
                                    "已获取用户 {} 的微博，内容为 {}".format(
                                        self.user["screen_name"], wb["text"]
                                    )
                                )
                                # self.print_weibo(wb)
                            else:
                                logger.info("正在过滤转发微博")
                    
                if const.CHECK_COOKIE["CHECK"] and not const.CHECK_COOKIE["CHECKED"]:
                    logger.warning("经检查，cookie无效，系统退出")
                    if const.NOTIFY["NOTIFY"]:
                        push_deer("经检查，cookie无效，系统退出")
                    sys.exit()
            else:
                return True
            logger.info(
                "{}已获取{}({})的第{}页微博{}".format(
                    "-" * 30, self.user["screen_name"], self.user["id"], page, "-" * 30
                )
            )
        except Exception as e:
            logger.exception(e)

    def get_page_count(self):
        """获取微博页数"""
        try:
            weibo_count = self.user["statuses_count"]
            page_weibo_count = self.page_weibo_count
            page_count = int(math.ceil(weibo_count / page_weibo_count))
            if not isinstance(page_weibo_count, int):
                raise ValueError("config.json中每页爬取的微博数 page_weibo_count 必须是一个整数")
            return page_count
        except KeyError:
            logger.exception(
                "程序出错，错误原因可能为以下两者：\n"
                "1.user_id不正确；\n"
                "2.此用户微博可能需要设置cookie才能爬取。\n"
                "解决方案：\n"
                "请参考\n"
                "https://github.com/dataabc/weibo-crawler#如何获取user_id\n"
                "获取正确的user_id；\n"
                "或者参考\n"
                "https://github.com/dataabc/weibo-crawler#3程序设置\n"
                "中的“设置cookie”部分设置cookie信息"
            )

    def get_write_info(self, wrote_count):
        """获取要写入的微博信息"""
        write_info = []
        for w in self.weibo[wrote_count:]:
            wb = OrderedDict()
            for k, v in w.items():
                if k not in ["user_id", "screen_name", "retweet"]:
                    if "unicode" in str(type(v)):
                        v = v.encode("utf-8")
                    if k == "id":
                        v = str(v) + "\t"
                    wb[k] = v
            if not self.only_crawl_original:
                if w.get("retweet"):
                    wb["is_original"] = False
                    for k2, v2 in w["retweet"].items():
                        if "unicode" in str(type(v2)):
                            v2 = v2.encode("utf-8")
                        if k2 == "id":
                            v2 = str(v2) + "\t"
                        wb["retweet_" + k2] = v2
                else:
                    wb["is_original"] = True
            write_info.append(wb)
        return write_info

    def get_filepath(self, type):
        """获取结果文件路径"""
        try:
            dir_name = self.user["screen_name"]
            if self.user_id_as_folder_name:
                dir_name = str(self.user_config["user_id"])
            file_dir = (
                os.path.split(os.path.realpath(__file__))[0]
                + os.sep
                + "weibo"
                + os.sep
                + dir_name
            )
            if type in ["img", "video", "live_photo"]:
                file_dir = file_dir + os.sep + type
            elif type == "markdown":
                # Markdown文件保存在用户目录下，图片在用户目录的img子目录中
                file_dir = file_dir
            if not os.path.isdir(file_dir):
                os.makedirs(file_dir)
            if type in ["img", "video", "live_photo"]:
                return file_dir
            elif type == "markdown":
                # 对于markdown类型，返回目录路径，文件名会在generate_markdown_file中指定
                return file_dir
            file_path = file_dir + os.sep + str(self.user_config["user_id"]) + "." + type
            return file_path
        except Exception as e:
            logger.exception(e)

    def get_result_headers(self):
        """获取要写入结果文件的表头"""
        result_headers = [
            "id",
            "bid",
            "正文",
            "头条文章url",
            "原始图片url",
            "视频url",
            "Live Photo视频url",
            "位置",
            "日期",
            "工具",
            "点赞数",
            "评论数",
            "转发数",
            "话题",
            "@用户",
            "完整日期",
            "是否编辑过",
            "编辑次数",            
        ]
        if not self.only_crawl_original:
            result_headers2 = ["是否原创", "源用户id", "源用户昵称"]
            result_headers3 = ["源微博" + r for r in result_headers]
            result_headers = result_headers + result_headers2 + result_headers3
        return result_headers

    def write_csv(self, wrote_count):
        """将爬到的信息写入csv文件"""
        write_info = self.get_write_info(wrote_count)
        result_headers = self.get_result_headers()
        result_data = [w.values() for w in write_info]
        file_path = self.get_filepath("csv")
        self.csv_helper(result_headers, result_data, file_path)

    def csv_helper(self, headers, result_data, file_path):
        """将指定信息写入csv文件"""
        if not os.path.isfile(file_path):
            is_first_write = 1
        else:
            is_first_write = 0
        if sys.version < "3":  # python2.x
            with open(file_path, "ab") as f:
                f.write(codecs.BOM_UTF8)
                writer = csv.writer(f)
                if is_first_write:
                    writer.writerows([headers])
                writer.writerows(result_data)
        else:  # python3.x

            with open(file_path, "a", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                if is_first_write:
                    writer.writerows([headers])
                writer.writerows(result_data)
        if headers[0] == "id":
            logger.info("%d条微博写入csv文件完毕,保存路径:", self.got_count)
        else:
            logger.info("%s 信息写入csv文件完毕，保存路径:", self.user["screen_name"])
        logger.info(file_path)

    def update_json_data(self, data, weibo_info):
        """更新要写入json结果文件中的数据，已经存在于json中的信息更新为最新值，不存在的信息添加到data中"""
        data["user"] = self.user
        if data.get("weibo"):
            is_new = 1  # 待写入微博是否全部为新微博，即待写入微博与json中的数据不重复
            for old in data["weibo"]:
                if weibo_info[-1]["id"] == old["id"]:
                    is_new = 0
                    break
            if is_new == 0:
                for new in weibo_info:
                    flag = 1
                    for i, old in enumerate(data["weibo"]):
                        if new["id"] == old["id"]:
                            data["weibo"][i] = new
                            flag = 0
                            break
                    if flag:
                        data["weibo"].append(new)
            else:
                data["weibo"] += weibo_info
        else:
            data["weibo"] = weibo_info
        return data

    def write_json(self, wrote_count):
        """将爬到的信息写入json文件"""
        data = {}
        path = self.get_filepath("json")
        if os.path.isfile(path):
            with codecs.open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        weibo_info = self.weibo[wrote_count:]
        data = self.update_json_data(data, weibo_info)
        with codecs.open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        logger.info("%d条微博写入json文件完毕,保存路径:", self.got_count)
        logger.info(path)

    def send_post_request_with_token(self, url, data, token, max_retries, backoff_factor):
        headers = {
            'Content-Type': 'application/json',
            'api-token': f'{token}',
        }
        for attempt in range(max_retries + 1):
            try:
                response = self.session.get(url, json=data, headers=headers)
                if response.status_code == requests.codes.ok:
                    return response.json()
                else:
                    raise RequestException(f"Unexpected response status: {response.status_code}")
            except RequestException as e:
                if attempt < max_retries:
                    sleep(backoff_factor * (attempt + 1))  # 逐步增加等待时间，避免频繁重试
                    continue
                else:
                    logger.error(f"在尝试{max_retries}次发出POST连接后，请求失败：{e}")

    def write_post(self, wrote_count):
        """将爬到的信息通过POST发出"""
        data = {}
        data['user'] = self.user
        weibo_info = self.weibo[wrote_count:]
        if data.get('weibo'):
            data['weibo'] += weibo_info
        else:
            data['weibo'] = weibo_info

        if data:
            self.send_post_request_with_token(self.post_config["api_url"], data, self.post_config["api_token"], 3, 2)
            logger.info(u'%d条微博通过POST发送到 %s', len(data['weibo']), self.post_config["api_url"])
        else:
            logger.info(u'没有获取到微博，略过API POST')


    def info_to_mongodb(self, collection, info_list):
        """将爬取的信息写入MongoDB数据库"""
        try:
            import pymongo
        except ImportError:
            logger.warning("系统中可能没有安装pymongo库，请先运行 pip install pymongo ，再运行程序")
            sys.exit()
        try:
            from pymongo import MongoClient

            client = MongoClient(self.mongodb_URI)
            db = client["weibo"]
            collection = db[collection]
            if len(self.write_mode) > 1:
                new_info_list = copy.deepcopy(info_list)
            else:
                new_info_list = info_list
            for info in new_info_list:
                if not collection.find_one({"id": info["id"]}):
                    collection.insert_one(info)
                else:
                    collection.update_one({"id": info["id"]}, {"$set": info})
        except pymongo.errors.ServerSelectionTimeoutError:
            logger.warning("系统中可能没有安装或启动MongoDB数据库，请先根据系统环境安装或启动MongoDB，再运行程序")
            sys.exit()

    def weibo_to_mongodb(self, wrote_count):
        """将爬取的微博信息写入MongoDB数据库"""
        self.info_to_mongodb("weibo", self.weibo[wrote_count:])
        logger.info("%d条微博写入MongoDB数据库完毕", self.got_count)

    def mysql_create(self, connection, sql):
        """创建MySQL数据库或表"""
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql)
        finally:
            connection.close()

    def mysql_create_database(self, mysql_config, sql):
        """创建MySQL数据库"""
        try:
            import pymysql
        except ImportError:
            logger.warning("系统中可能没有安装pymysql库，请先运行 pip install pymysql ，再运行程序")
            sys.exit()
        try:
            if self.mysql_config:
                mysql_config = self.mysql_config
            connection = pymysql.connect(**mysql_config)
            self.mysql_create(connection, sql)
        except pymysql.OperationalError:
            logger.warning("系统中可能没有安装或正确配置MySQL数据库，请先根据系统环境安装或配置MySQL，再运行程序")
            sys.exit()

    def mysql_create_table(self, mysql_config, sql):
        """创建MySQL表"""
        import pymysql

        if self.mysql_config:
            mysql_config = self.mysql_config
        mysql_config["db"] = "weibo"
        connection = pymysql.connect(**mysql_config)
        self.mysql_create(connection, sql)

    def mysql_insert(self, mysql_config, table, data_list):
        """
        向MySQL表插入或更新数据

        Parameters
        ----------
        mysql_config: map
            MySQL配置表
        table: str
            要插入的表名
        data_list: list
            要插入的数据列表

        Returns
        -------
        bool: SQL执行结果
        """
        import pymysql

        if len(data_list) > 0:
            keys = ", ".join(data_list[0].keys())
            values = ", ".join(["%s"] * len(data_list[0]))
            if self.mysql_config:
                mysql_config = self.mysql_config
            mysql_config["db"] = "weibo"
            connection = pymysql.connect(**mysql_config)
            cursor = connection.cursor()
            sql = """INSERT INTO {table}({keys}) VALUES ({values}) ON
                     DUPLICATE KEY UPDATE""".format(
                table=table, keys=keys, values=values
            )
            update = ",".join(
                [" {key} = values({key})".format(key=key) for key in data_list[0]]
            )
            sql += update
            try:
                cursor.executemany(sql, [tuple(data.values()) for data in data_list])
                connection.commit()
            except Exception as e:
                connection.rollback()
                logger.exception(e)
            finally:
                connection.close()

    def weibo_to_mysql(self, wrote_count):
        """将爬取的微博信息写入MySQL数据库"""
        mysql_config = {
            "host": "localhost",
            "port": 3306,
            "user": "root",
            "password": "123456",
            "charset": "utf8mb4",
        }
        # 创建'weibo'表
        create_table = """
                CREATE TABLE IF NOT EXISTS weibo (
                id varchar(20) NOT NULL,
                bid varchar(12) NOT NULL,
                user_id varchar(20),
                screen_name varchar(30),
                text text,
                article_url varchar(100),
                topics varchar(200),
                at_users varchar(1000),
                pics varchar(3000),
                video_url varchar(1000),
                live_photo_url varchar(1000),
                location varchar(100),
                created_at DATETIME,
                source varchar(30),
                attitudes_count INT,
                comments_count INT,
                reposts_count INT,
                retweet_id varchar(20),
                edited BOOLEAN DEFAULT 0,
                edit_count INT DEFAULT 0,
                PRIMARY KEY (id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
        self.mysql_create_table(mysql_config, create_table)

        # 要插入的微博列表
        weibo_list = []
        # 要插入的转发微博列表
        retweet_list = []
        if len(self.write_mode) > 1:
            info_list = copy.deepcopy(self.weibo[wrote_count:])
        else:
            info_list = self.weibo[wrote_count:]
        for w in info_list:
            w["created_at"] = w["full_created_at"]
            del w["full_created_at"]

            if "retweet" in w:
                r = w["retweet"]
                r["retweet_id"] = ""
                r["created_at"] = r["full_created_at"]
                del r["full_created_at"]
                retweet_list.append(r)
                w["retweet_id"] = r["id"]
                del w["retweet"]
            else:
                w["retweet_id"] = ""
            weibo_list.append(w)
        # 在'weibo'表中插入或更新微博数据
        self.mysql_insert(mysql_config, "weibo", retweet_list)
        self.mysql_insert(mysql_config, "weibo", weibo_list)
        logger.info("%d条微博写入MySQL数据库完毕", self.got_count)

    def weibo_to_sqlite(self, wrote_count):
        con = self.get_sqlite_connection()
        weibo_list = []
        retweet_list = []
        info_list = copy.deepcopy(self.weibo[wrote_count:])
        for w in info_list:
            if "retweet" in w:
                w["retweet"]["retweet_id"] = ""
                retweet_list.append(w["retweet"])
                w["retweet_id"] = w["retweet"]["id"]
                del w["retweet"]
            else:
                w["retweet_id"] = ""
            weibo_list.append(w)

        comment_max_count = self.comment_max_download_count
        repost_max_count = self.repost_max_download_count
        download_comment = self.download_comment and comment_max_count > 0
        download_repost = self.download_repost and repost_max_count > 0

        count = 0
        for weibo in weibo_list:
            self.sqlite_insert_weibo(con, weibo)
            if (download_comment) and (weibo["comments_count"] > 0):
                self.get_weibo_comments(
                    weibo, comment_max_count, self.sqlite_insert_comments
                )
                count += 1
                if count % 20:
                    sleep(random.randint(3, 6))
            if (download_repost) and (weibo["reposts_count"] > 0):
                self.get_weibo_reposts(
                    weibo, repost_max_count, self.sqlite_insert_reposts
                )
                count += 1
                if count % 20:
                    sleep(random.randint(3, 6))

        for weibo in retweet_list:
            self.sqlite_insert_weibo(con, weibo)
        con.close()

    def export_comments_to_csv_for_current_user(self):
        """将当前用户相关的评论从 SQLite 导出到该用户目录下的 CSV 文件"""
        # 仅在启用了 sqlite 写入且开启下载评论时导出
        if "sqlite" not in self.write_mode or not self.download_comment:
            return
        try:
            db_path = self.get_sqlte_path()
            if not os.path.exists(db_path):
                logger.warning("导出评论失败，未找到SQLite数据库: %s", db_path)
                return

            # 当前用户的 ID，用于筛选属于该用户微博的评论
            user_id = str(self.user_config.get("user_id", ""))
            if not user_id:
                logger.warning("导出评论失败，当前用户ID为空")
                return

            # 用户结果目录，与微博 CSV 同级，例如 weibo/胡歌/ 或 weibo/1223178222/
            csv_path = self.get_filepath("csv")
            user_dir = os.path.dirname(csv_path)
            if not os.path.isdir(user_dir):
                os.makedirs(user_dir)
            # 使用用户昵称作为文件名的一部分，避免再出现纯数字 user_id
            screen_name = self.user.get("screen_name") or user_id
            safe_screen_name = re.sub(r'[\\/:*?"<>|]', "_", str(screen_name))
            out_path = os.path.join(user_dir, f"{safe_screen_name}_comments.csv")

            con = sqlite3.connect(db_path)
            cur = con.cursor()

            # 只导出当前用户微博下的评论
            sql = """
                SELECT
                    c.id,
                    c.weibo_id,
                    c.created_at,
                    c.user_screen_name,
                    c.text,
                    c.pic_url,
                    c.like_count
                FROM comments c
                JOIN weibo w ON c.weibo_id = w.id
                WHERE w.user_id = ?
                ORDER BY c.weibo_id, c.id
            """
            rows = cur.execute(sql, (user_id,)).fetchall()
            con.close()

            if not rows:
                logger.info("用户 %s 没有可导出的评论记录，跳过生成评论 CSV", user_id)
                return

            header = [
                "id",
                "weibo_id",
                "created_at",
                "user_screen_name",
                "text",
                "pic_url",
                "like_count",
            ]

            # 1）导出当前用户的汇总评论文件：<用户昵称>_comments.csv
            with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(header)
                writer.writerows(rows)

            # 2）按每条微博拆分导出：<用户昵称>_<weibo_id>_comments.csv
            #    满足“用户昵称 + weiboId + comments”的文件命名要求
            comments_by_weibo = {}
            for row in rows:
                weibo_id = row[1]
                comments_by_weibo.setdefault(weibo_id, []).append(row)

            for weibo_id, weibo_rows in comments_by_weibo.items():
                per_weibo_path = os.path.join(
                    user_dir, f"{safe_screen_name}_{weibo_id}_comments.csv"
                )
                with open(per_weibo_path, "w", newline="", encoding="utf-8-sig") as f:
                    writer = csv.writer(f)
                    writer.writerow(header)
                    writer.writerows(weibo_rows)

            logger.info(
                "共导出 %d 条评论到用户汇总 CSV: %s，并按每条微博拆分生成 %d 个评论 CSV",
                len(rows),
                out_path,
                len(comments_by_weibo),
            )
        except Exception as e:
            logger.exception(e)

    def sqlite_insert_comments(self, weibo, comments):
        if not comments or len(comments) == 0:
            return
        con = self.get_sqlite_connection()
        for comment in comments:
            data = self.parse_sqlite_comment(comment, weibo)
            self.sqlite_insert(con, data, "comments")
            if "comments" in comment and isinstance(comment["comments"], list):
                for c in comment["comments"]:
                    data = self.parse_sqlite_comment(c, weibo)
                    self.sqlite_insert(con, data, "comments")
        con.close()

    def sqlite_insert_reposts(self, weibo, reposts):
        if not reposts or len(reposts) == 0:
            return
        con = self.get_sqlite_connection()
        for repost in reposts:
            data = self.parse_sqlite_repost(repost, weibo)
            self.sqlite_insert(con, data, "reposts")

        con.close()

    def parse_sqlite_comment(self, comment, weibo):
        if not comment:
            return
        sqlite_comment = OrderedDict()
        sqlite_comment["id"] = comment["id"]

        self._try_get_value("bid", "bid", sqlite_comment, comment)
        self._try_get_value("root_id", "rootid", sqlite_comment, comment)
        self._try_get_value("created_at", "created_at", sqlite_comment, comment)
        sqlite_comment["weibo_id"] = weibo["id"]

        sqlite_comment["user_id"] = comment["user"]["id"]
        sqlite_comment["user_screen_name"] = comment["user"]["screen_name"]
        self._try_get_value(
            "user_avatar_url", "avatar_hd", sqlite_comment, comment["user"]
        )
        if self.remove_html_tag:
            sqlite_comment["text"] = re.sub('<[^<]+?>', '', comment["text"]).replace('\n', '').strip()
        else:
            sqlite_comment["text"] = comment["text"]
        
        sqlite_comment["pic_url"] = ""
        if comment.get("pic"):
            sqlite_comment["pic_url"] = comment["pic"]["large"]["url"]
        if sqlite_comment["pic_url"]:
            pic_url = sqlite_comment["pic_url"]

            # 评论图片目录：weibo/<用户目录>/<用户昵称>_comments_img
            csv_path = self.get_filepath("csv")
            user_dir = os.path.dirname(csv_path)
            if not os.path.isdir(user_dir):
                os.makedirs(user_dir)
            screen_name = self.user.get("screen_name") or str(
                self.user_config.get("user_id", "")
            )
            safe_screen_name = re.sub(r'[\\/:*?"<>|]', "_", str(screen_name))
            pic_path = os.path.join(user_dir, f"{safe_screen_name}_comments_img")
            if not os.path.exists(pic_path):
                os.makedirs(pic_path)

            # 文件名包含 微博用户昵称 + weibo_id + 评论用户昵称 + comments
            # 为避免重名，如果已存在则在末尾追加 _1/_2/... 序号
            weibo_id = sqlite_comment["weibo_id"]
            comment_user = sqlite_comment.get("user_screen_name", "")
            safe_comment_user = re.sub(r'[\\/:*?"<>|]', "_", str(comment_user))
            base_name = "{screen_name}_{weibo_id}_{comment_user}_comments".format(
                screen_name=safe_screen_name,
                weibo_id=weibo_id,
                comment_user=safe_comment_user,
            )
            pic_name = base_name + ".jpg"
            idx = 1
            while os.path.exists(os.path.join(pic_path, pic_name)):
                pic_name = f"{base_name}_{idx}.jpg"
                idx += 1
            pic_full_path = os.path.join(pic_path, pic_name)
            if not os.path.exists(pic_full_path):
                try:
                    response = self.session.get(pic_url, timeout=10)
                    with open(pic_full_path, "wb") as f:
                        f.write(response.content)
                    logger.info("评论图片下载成功: %s", pic_full_path)
                except Exception as e:
                    logger.warning("下载评论图片失败: %s", e)
        self._try_get_value("like_count", "like_count", sqlite_comment, comment)
        return sqlite_comment

    def parse_sqlite_repost(self, repost, weibo):
        if not repost:
            return
        sqlite_repost = OrderedDict()
        sqlite_repost["id"] = repost["id"]

        self._try_get_value("bid", "bid", sqlite_repost, repost)
        self._try_get_value("created_at", "created_at", sqlite_repost, repost)
        sqlite_repost["weibo_id"] = weibo["id"]

        sqlite_repost["user_id"] = repost["user"]["id"]
        sqlite_repost["user_screen_name"] = repost["user"]["screen_name"]
        self._try_get_value(
            "user_avatar_url", "profile_image_url", sqlite_repost, repost["user"]
        )
        text = repost.get("raw_text")
        if text:
            text = text.split("//", 1)[0]
        if text is None or text == "" or text == "Repost":
            text = "转发微博"
        sqlite_repost["text"] = text
        self._try_get_value("like_count", "attitudes_count", sqlite_repost, repost)
        return sqlite_repost

    def _try_get_value(self, source_name, target_name, dict, json):
        dict[source_name] = ""
        value = json.get(target_name)
        if value:
            dict[source_name] = value

    def sqlite_insert_weibo(self, con: sqlite3.Connection, weibo: dict):
        sqlite_weibo = self.parse_sqlite_weibo(weibo)
        self.sqlite_insert(con, sqlite_weibo, "weibo")

    def parse_sqlite_weibo(self, weibo):
        if not weibo:
            return
        sqlite_weibo = OrderedDict()
        sqlite_weibo["user_id"] = weibo["user_id"]
        sqlite_weibo["id"] = weibo["id"]
        sqlite_weibo["bid"] = weibo["bid"]
        sqlite_weibo["screen_name"] = weibo["screen_name"]
        sqlite_weibo["text"] = weibo["text"]
        sqlite_weibo["article_url"] = weibo["article_url"]
        sqlite_weibo["topics"] = weibo["topics"]
        sqlite_weibo["pics"] = weibo["pics"]
        sqlite_weibo["video_url"] = weibo["video_url"]
        sqlite_weibo["live_photo_url"] = weibo["live_photo_url"]
        sqlite_weibo["location"] = weibo["location"]
        sqlite_weibo["created_at"] = weibo["full_created_at"]
        sqlite_weibo["source"] = weibo["source"]
        sqlite_weibo["attitudes_count"] = weibo["attitudes_count"]
        sqlite_weibo["comments_count"] = weibo["comments_count"]
        sqlite_weibo["reposts_count"] = weibo["reposts_count"]
        sqlite_weibo["retweet_id"] = weibo["retweet_id"]
        sqlite_weibo["at_users"] = weibo["at_users"]
        sqlite_weibo["edited"] = weibo.get("edited", False)
        sqlite_weibo["edit_count"] = weibo.get("edit_count", 0)
        return sqlite_weibo

    def user_to_sqlite(self):
        con = self.get_sqlite_connection()
        self.sqlite_insert_user(con, self.user)
        con.close()

    def sqlite_insert_user(self, con: sqlite3.Connection, user: dict):
        sqlite_user = self.parse_sqlite_user(user)
        self.sqlite_insert(con, sqlite_user, "user")

    def parse_sqlite_user(self, user):
        if not user:
            return
        sqlite_user = OrderedDict()
        sqlite_user["id"] = user["id"]
        sqlite_user["nick_name"] = user["screen_name"]
        sqlite_user["gender"] = user["gender"]
        sqlite_user["follower_count"] = user["followers_count"]
        sqlite_user["follow_count"] = user["follow_count"]
        sqlite_user["birthday"] = user["birthday"]
        sqlite_user["location"] = user["location"]
        sqlite_user["ip_location"] = user.get("ip_location", "")         
        sqlite_user["edu"] = user["education"]
        sqlite_user["company"] = user["company"]
        sqlite_user["reg_date"] = user["registration_time"]
        sqlite_user["main_page_url"] = user["profile_url"]
        sqlite_user["avatar_url"] = user["avatar_hd"]
        sqlite_user["bio"] = user["description"]
        return sqlite_user

    def sqlite_insert(self, con: sqlite3.Connection, data: dict, table: str):
        if not data:
            return
        cur = con.cursor()
        keys = ",".join(data.keys())
        values = ",".join(["?"] * len(data))
        sql = """INSERT OR REPLACE INTO {table}({keys}) VALUES({values})
                """.format(
            table=table, keys=keys, values=values
        )
        cur.execute(sql, list(data.values()))
        con.commit()

    def get_sqlite_connection(self):
        path = self.get_sqlte_path()
        create = False
        if not os.path.exists(path):
            create = True

        con = sqlite3.connect(path)

        if create == True:
            self.create_sqlite_table(connection=con)

        return con

    def create_sqlite_table(self, connection: sqlite3.Connection):
        sql = self.get_sqlite_create_sql()
        cur = connection.cursor()
        cur.executescript(sql)
        connection.commit()

    def get_sqlte_path(self):
        return "./weibo/weibodata.db"

    def get_sqlite_create_sql(self):
        create_sql = """
                CREATE TABLE IF NOT EXISTS user (
                    id varchar(64) NOT NULL
                    ,nick_name varchar(64) NOT NULL
                    ,gender varchar(6)
                    ,follower_count integer
                    ,follow_count integer
                    ,birthday varchar(10)
                    ,location varchar(32)
                    ,ip_location varchar(32)
                    ,edu varchar(32)
                    ,company varchar(32)
                    ,reg_date DATETIME
                    ,main_page_url text
                    ,avatar_url text
                    ,bio text
                    ,PRIMARY KEY (id)
                );

                CREATE TABLE IF NOT EXISTS weibo (
                    id varchar(20) NOT NULL
                    ,bid varchar(12) NOT NULL
                    ,user_id varchar(20)
                    ,screen_name varchar(30)
                    ,text varchar(2000)
                    ,article_url varchar(100)
                    ,topics varchar(200)
                    ,at_users varchar(1000)
                    ,pics varchar(3000)
                    ,video_url varchar(1000)
                    ,live_photo_url varchar(1000)
                    ,location varchar(100)
                    ,created_at DATETIME
                    ,source varchar(30)
                    ,attitudes_count INT
                    ,comments_count INT
                    ,reposts_count INT
                    ,retweet_id varchar(20)
                    ,edited BOOLEAN DEFAULT 0
                    ,edit_count INT DEFAULT 0                    
                    ,PRIMARY KEY (id)
                );

                CREATE TABLE IF NOT EXISTS bins (
                    id integer PRIMARY KEY AUTOINCREMENT
                    ,ext varchar(10) NOT NULL /*file extension*/
                    ,data blob NOT NULL
                    ,weibo_id varchar(20)
                    ,comment_id varchar(20)
                    ,path text
                    ,url text
                );

                CREATE TABLE IF NOT EXISTS comments (
                    id varchar(20) NOT NULL
                    ,bid varchar(20) NOT NULL
                    ,weibo_id varchar(32) NOT NULL
                    ,root_id varchar(20) 
                    ,user_id varchar(20) NOT NULL
                    ,created_at varchar(20)
                    ,user_screen_name varchar(64) NOT NULL
                    ,user_avatar_url text
                    ,text varchar(1000)
                    ,pic_url text
                    ,like_count integer
                    ,PRIMARY KEY (id)
                );

                CREATE TABLE IF NOT EXISTS reposts (
                    id varchar(20) NOT NULL
                    ,bid varchar(20) NOT NULL
                    ,weibo_id varchar(32) NOT NULL
                    ,user_id varchar(20) NOT NULL
                    ,created_at varchar(20)
                    ,user_screen_name varchar(64) NOT NULL
                    ,user_avatar_url text
                    ,text varchar(1000)
                    ,like_count integer
                    ,PRIMARY KEY (id)
                );
                """
        return create_sql

    def update_user_config_file(self, user_config_file_path):
        """更新用户配置文件"""
        with open(user_config_file_path, "rb") as f:
            try:
                lines = f.read().splitlines()
                lines = [line.decode("utf-8-sig") for line in lines]
            except UnicodeDecodeError:
                logger.error("%s文件应为utf-8编码，请先将文件编码转为utf-8再运行程序", user_config_file_path)
                sys.exit()
            for i, line in enumerate(lines):
                info = line.split(" ")
                if len(info) > 0 and info[0].isdigit():
                    if self.user_config["user_id"] == info[0]:
                        if len(info) == 1:
                            info.append(self.user["screen_name"])
                            info.append(self.start_date)
                        if len(info) == 2:
                            info.append(self.start_date)
                        if len(info) > 2:
                            info[2] = self.start_date
                        lines[i] = " ".join(info)
                        break
        with codecs.open(user_config_file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def write_markdown(self, wrote_count):
        """将爬到的信息写入markdown文件"""
        # 按日期分组微博
        weibo_by_date = self.group_weibo_by_date(wrote_count)

        # 先下载图片（如果需要）
        if self.original_pic_download:
            self.download_markdown_images(wrote_count)

        # 为每个日期生成markdown文件
        for date, weibo_list in weibo_by_date.items():
            self.generate_markdown_file(date, weibo_list)

        logger.info("%d条微博写入markdown文件完毕", self.got_count - wrote_count)

    def group_weibo_by_date(self, wrote_count):
        """按日期分组微博"""
        weibo_by_date = {}
        for w in self.weibo[wrote_count:]:
            # 获取微博发布日期（YYYY-MM-DD格式）
            created_at = w.get("created_at", "")
            if not created_at:
                continue

            # 解析日期，提取 YYYY-MM-DD 部分
            try:
                date_obj = datetime.strptime(created_at, DTFORMAT)
                date_str = date_obj.strftime("%Y-%m-%d")

                if date_str not in weibo_by_date:
                    weibo_by_date[date_str] = []
                weibo_by_date[date_str].append(w)
            except ValueError:
                logger.warning(f"无法解析微博日期: {created_at}")
                continue

        return weibo_by_date

    def download_markdown_images(self, wrote_count):
        """为Markdown格式下载图片，使用指定的命名规则"""
        # 获取用户目录
        file_dir = self.get_filepath("markdown")
        img_dir = os.path.join(file_dir, "img")
        if not os.path.isdir(img_dir):
            os.makedirs(img_dir)

        # 下载图片
        for w in self.weibo[wrote_count:]:
            # 处理原创微博图片
            if w.get("pics"):
                self._download_weibo_images(w, img_dir, is_retweet=False)

            # 处理转发微博图片
            if not self.only_crawl_original and w.get("retweet"):
                retweet = w["retweet"]
                if retweet.get("pics"):
                    self._download_weibo_images(retweet, img_dir, is_retweet=True)

    def _download_weibo_images(self, weibo, img_dir, is_retweet=False):
        """下载单条微博的图片"""
        created_at = weibo.get("created_at", "")
        if not created_at:
            return

        try:
            time_obj = datetime.strptime(created_at, DTFORMAT)
            date_str = time_obj.strftime("%Y-%m-%d")
            time_str = time_obj.strftime("%H:%M:%S")
        except ValueError:
            return

        pics = weibo["pics"].split(",")
        for i, pic_url in enumerate(pics):
            if not pic_url:
                continue

            # 生成图片文件名：YYYY-MM-DD_HH-MM-SS.jpg
            # 如果同一条微博有多张图片，在文件名后加 _1, _2 等后缀
            base_filename = f"{date_str}_{time_str.replace(':', '-')}"
            if len(pics) > 1:
                img_filename = f"{base_filename}_{i+1}.jpg"
            else:
                img_filename = f"{base_filename}.jpg"

            img_path = os.path.join(img_dir, img_filename)

            # 下载图片
            self.download_one_file(pic_url, img_path, "img", weibo["id"], created_at)

    def generate_markdown_file(self, date, weibo_list):
        """生成单个日期的markdown文件（增量模式）"""
        # 获取用户目录
        file_dir = self.get_filepath("markdown")

        # 创建markdown文件路径
        md_file_path = os.path.join(file_dir, f"{date}.md")

        # 获取用户名
        username = self.user.get("screen_name", "未知用户")

        # 读取已有文件中的微博ID，用于去重（比时间戳更可靠）
        existing_weibo_ids = set()
        existing_content = ""
        if os.path.exists(md_file_path):
            try:
                with open(md_file_path, "r", encoding="utf-8") as f:
                    existing_content = f.read()
                    # 使用正则表达式提取所有 <!-- weibo_id: xxx --> 格式的微博ID
                    weibo_id_pattern = r"<!-- weibo_id: (\d+) -->"
                    matches = re.findall(weibo_id_pattern, existing_content)
                    existing_weibo_ids = set(matches)
                logger.info(f"已读取现有MD文件，包含 {len(existing_weibo_ids)} 条微博记录")
            except Exception as e:
                logger.warning(f"读取现有MD文件失败: {e}，将创建新文件")
                existing_content = ""
                existing_weibo_ids = set()

        # 过滤出新的微博（不在已有文件中的）
        new_weibo_list = []
        for w in weibo_list:
            weibo_id = str(w.get("id", ""))
            if weibo_id and weibo_id not in existing_weibo_ids:
                new_weibo_list.append(w)

        # 如果没有新微博，直接返回
        if not new_weibo_list:
            logger.info(f"日期 {date} 没有新微博需要写入")
            return

        # 构建新微博的markdown内容
        new_md_content = ""
        for w in new_weibo_list:
            # 获取时间（HH:MM:SS格式）
            created_at = w.get("created_at", "")
            if not created_at:
                continue

            try:
                time_obj = datetime.strptime(created_at, DTFORMAT)
                time_str = time_obj.strftime("%H:%M:%S")
                date_str = time_obj.strftime("%Y-%m-%d")
            except ValueError:
                time_str = "00:00:00"
                date_str = date

            # 添加时间标题和微博ID（用于增量模式去重）
            weibo_id = w.get("id", "")
            new_md_content += f"### {time_str}\n<!-- weibo_id: {weibo_id} -->\n"

            # 处理转发微博
            if not self.only_crawl_original and w.get("retweet"):
                # 原创部分
                text = w.get("text", "").strip()
                if text:
                    new_md_content += f"{text}\n\n"

                # 转发部分
                retweet = w["retweet"]
                retweet_text = retweet.get("text", "").strip()
                if retweet_text:
                    new_md_content += f"> 转发: {retweet_text}\n\n"

                # 转发微博图片
                if retweet.get("pics"):
                    pics = retweet["pics"].split(",")
                    # 使用转发微博的时间
                    retweet_created_at = retweet.get("created_at", created_at)
                    try:
                        retweet_time_obj = datetime.strptime(retweet_created_at, DTFORMAT)
                        retweet_date_str = retweet_time_obj.strftime("%Y-%m-%d")
                        retweet_time_str = retweet_time_obj.strftime("%H:%M:%S")
                    except ValueError:
                        retweet_date_str = date_str
                        retweet_time_str = time_str

                    for i, pic_url in enumerate(pics):
                        if pic_url:
                            base_filename = f"{retweet_date_str}_{retweet_time_str.replace(':', '-')}"
                            if len(pics) > 1:
                                img_filename = f"{base_filename}_{i+1}.jpg"
                            else:
                                img_filename = f"{base_filename}.jpg"
                            new_md_content += f"![image](img/{img_filename})\n\n"
            else:
                # 原创微博
                text = w.get("text", "").strip()
                if text:
                    new_md_content += f"{text}\n\n"

                # 原创微博图片
                if w.get("pics"):
                    pics = w["pics"].split(",")
                    for i, pic_url in enumerate(pics):
                        if pic_url:
                            base_filename = f"{date_str}_{time_str.replace(':', '-')}"
                            if len(pics) > 1:
                                img_filename = f"{base_filename}_{i+1}.jpg"
                            else:
                                img_filename = f"{base_filename}.jpg"
                            new_md_content += f"![image](img/{img_filename})\n\n"

            # 添加分隔线
            new_md_content += "---\n\n"

        # 写入文件（增量模式）
        try:
            if existing_content:
                # 追加到已有内容末尾
                final_content = existing_content.rstrip() + "\n\n" + new_md_content
            else:
                # 创建新文件，添加标题
                final_content = f"## {date} [{username}] 微博存档\n\n" + new_md_content

            with open(md_file_path, "w", encoding="utf-8") as f:
                f.write(final_content)
            logger.info(f"Markdown文件已更新: {md_file_path}，新增 {len(new_weibo_list)} 条微博")
        except Exception as e:
            logger.error(f"生成Markdown文件失败: {e}")

    def write_data(self, wrote_count):
        """将爬到的信息写入文件或数据库"""
        if self.got_count > wrote_count:
            if "csv" in self.write_mode:
                self.write_csv(wrote_count)
            if "json" in self.write_mode:
                self.write_json(wrote_count)
            if "post" in self.write_mode:
                self.write_post(wrote_count)
            if "mysql" in self.write_mode:
                self.weibo_to_mysql(wrote_count)
            if "mongo" in self.write_mode:
                self.weibo_to_mongodb(wrote_count)
            if "sqlite" in self.write_mode:
                self.weibo_to_sqlite(wrote_count)
            if "markdown" in self.write_mode:
                self.write_markdown(wrote_count)

            # 图片下载逻辑：如果使用markdown模式，图片已在write_markdown中下载
            # 否则按原有逻辑下载
            if self.original_pic_download and "markdown" not in self.write_mode:
                self.download_files("img", "original", wrote_count)
            if self.original_video_download:
                self.download_files("video", "original", wrote_count)
            if self.original_live_photo_download:
                self.download_files("live_photo", "original", wrote_count)
            # 下载转发微博文件（如果不禁爬转发）
            if not self.only_crawl_original:
                if self.retweet_pic_download and "markdown" not in self.write_mode:
                    self.download_files("img", "retweet", wrote_count)
                if self.retweet_video_download:
                    self.download_files("video", "retweet", wrote_count)
                if self.retweet_live_photo_download:
                    self.download_files("live_photo", "retweet", wrote_count)

    def get_pages(self):
        """获取全部微博"""
        try:
            # 用户id不可用
            if self.get_user_info() != 0:
                return
            logger.info("准备搜集 {} 的微博".format(self.user["screen_name"]))

            # 防封禁：初始化爬取统计
            if self.anti_ban_enabled:
                self.crawl_stats["start_time"] = time.time()
                cfg = self.anti_ban_config
                logger.info("🛡️ 防封禁模式已启用")
                logger.info("┌────────────────────────────────────┐")
                logger.info("│ 每会话最大微博数: %-17d│", cfg['max_weibo_per_session'])
                logger.info("│ 批次大小: %-8d 批次延迟: %3d秒 │", cfg['batch_size'], cfg['batch_delay'])
                logger.info("│ 请求延迟: %d-%d秒                   │", cfg['request_delay_min'], cfg['request_delay_max'])
                logger.info("│ 最大会话时间: %-7d秒            │", cfg['max_session_time'])
                logger.info("│ 最大API错误数: %-20d│", cfg['max_api_errors'])
                logger.info("└────────────────────────────────────┘")

            if const.MODE == "append" and (
                "first_crawler" not in self.__dict__ or self.first_crawler is False
            ):
                # 本次运行的某用户首次抓取，用于标记最新的微博id
                self.first_crawler = True
            since_date = datetime.strptime(self.user_config["since_date"], DTFORMAT)
            today = datetime.today()
            if since_date <= today:    # since_date 若为未来则无需执行
                page_count = self.get_page_count()
                wrote_count = 0
                page1 = 0
                random_pages = random.randint(1, 5)
                self.start_date = datetime.now().strftime(DTFORMAT)
                pages = range(self.start_page, page_count + 1)
                for page in tqdm(pages, desc="Progress"):
                    is_end = self.get_one_page(page)
                    
                    # 防封禁：检查是否需要休息
                    if is_end == "need_rest":
                        # 先写入已爬取的数据
                        self.write_data(wrote_count)
                        wrote_count = self.got_count
                        
                        # 执行休息
                        self.perform_anti_ban_rest()
                        
                        # 重置统计，继续爬取
                        self.reset_crawl_stats()
                        continue
                    
                    if is_end:
                        break

                    # 防封禁：检查批次延迟
                    if self.anti_ban_enabled:
                        self.check_batch_delay()

                    if page % 20 == 0:  # 每爬20页写入一次文件
                        self.write_data(wrote_count)
                        wrote_count = self.got_count

                    # 防封禁：保留原有延迟逻辑，但可根据配置调整
                    if self.anti_ban_enabled:
                        # 如果启用了防封禁，使用更保守的延迟
                        if (page - page1) % random_pages == 0 and page < page_count:
                            delay = random.randint(8, 12)  # 更保守的延迟
                            sleep(delay)
                            page1 = page
                            random_pages = random.randint(1, 5)
                    else:
                        # 原有逻辑
                        if (page - page1) % random_pages == 0 and page < page_count:
                            sleep(random.randint(6, 10))
                            page1 = page
                            random_pages = random.randint(1, 5)

                self.write_data(wrote_count)  # 将剩余不足20页的微博写入文件

            # 防封禁：输出统计信息
            if self.anti_ban_enabled:
                session_time = time.time() - self.crawl_stats["start_time"]
                logger.info(f"防封禁统计: 微博={self.crawl_stats['weibo_count']}, 请求={self.crawl_stats['request_count']}, 错误={self.crawl_stats['api_errors']}, 耗时={int(session_time)}秒")

            logger.info("微博爬取完成，共爬取%d条微博", self.got_count)
        except Exception as e:
            logger.exception(e)

    def get_user_config_list(self, file_path):
        """获取文件中的微博id信息"""
        with open(file_path, "rb") as f:
            try:
                lines = f.read().splitlines() 
                lines = [line.decode("utf-8-sig") for line in lines]
            except UnicodeDecodeError:
                logger.error("%s文件应为utf-8编码，请先将文件编码转为utf-8再运行程序", file_path)
                sys.exit()
            user_config_list = []
            # 分行解析配置，添加到user_config_list
            for line in lines:
                info = line.strip().split(" ")    # 去除字符串首尾空白字符
                if len(info) > 0 and info[0].isdigit():
                    user_config = {}
                    user_config["user_id"] = info[0]
                    # 根据配置文件行的字段数确定 since_date 的值
                    if len(info) == 3:
                        if self.is_datetime(info[2]):
                            user_config["since_date"] = info[2]
                        elif self.is_date(info[2]):
                            user_config["since_date"] = "{}T00:00:00".format(info[2])
                        elif info[2].isdigit():
                            since_date = date.today() - timedelta(int(info[2]))
                            user_config["since_date"] = since_date.strftime(DTFORMAT)
                        else:
                            logger.error("since_date 格式不正确，请确认配置是否正确")
                            sys.exit()
                        logger.info(f"用户 {user_config['user_id']} 使用文件中的起始时间: {user_config['since_date']}")
                    else:
                        user_config["since_date"] = self.since_date
                        logger.info(f"用户 {user_config['user_id']} 使用配置文件的起始时间: {user_config['since_date']}")
                    # 若超过3个字段，则第四个字段为 query_list                    
                    if len(info) > 3:
                        user_config["query_list"] = info[3].split(",")
                    else:
                        user_config["query_list"] = self.query_list
                    if user_config not in user_config_list:
                        user_config_list.append(user_config)
        return user_config_list

    def initialize_info(self, user_config):
        """初始化爬虫信息"""
        self.weibo = []
        self.user = {}
        self.user_config = user_config
        self.got_count = 0
        self.weibo_id_list = []

    def start(self):
        """运行爬虫"""
        try:
            for user_config in self.user_config_list:
                if len(user_config["query_list"]):
                    for query in user_config["query_list"]:
                        self.query = query
                        self.initialize_info(user_config)
                        self.get_pages()
                else:
                    self.initialize_info(user_config)
                    self.get_pages()

                # 当前用户所有微博和评论抓取完毕后，再导出该用户的评论 CSV
                self.export_comments_to_csv_for_current_user()

                logger.info("信息抓取完毕")
                logger.info("*" * 100)
                if self.user_config_file_path and self.user:
                    self.update_user_config_file(self.user_config_file_path)
        except Exception as e:
            logger.exception(e)


def handle_config_renaming(config, oldName, newName):
    if oldName in config and newName not in config:
        config[newName] = config[oldName]
        del config[oldName]

def get_config():
    """获取配置文件信息（支持JSON5格式）"""
    config_path = os.path.split(os.path.realpath(__file__))[0] + os.sep + "config.json"
    if not os.path.isfile(config_path):
        logger.warning(
            "当前路径：%s 不存在配置文件config.json",
            (os.path.split(os.path.realpath(__file__))[0] + os.sep),
        )
        sys.exit()
    try:
        with open(config_path, encoding="utf-8") as f:
            config_content = f.read()
            # 首先尝试使用JSON5解析（支持注释）
            try:
                config = json5.loads(config_content)
            except Exception as json5_error:
                # 如果JSON5解析失败，尝试标准JSON解析
                try:
                    config = json.loads(config_content)
                    logger.info("使用标准JSON格式解析配置文件")
                except Exception as json_error:
                    logger.error(f"JSON5解析失败: {json5_error}")
                    logger.error(f"标准JSON解析也失败: {json_error}")
                    logger.error("配置文件格式不正确，请检查语法")
                    sys.exit()

            # 重命名一些key, 但向前兼容
            handle_config_renaming(config, oldName="filter", newName="only_crawl_original")
            handle_config_renaming(config, oldName="result_dir_name", newName="user_id_as_folder_name")
            return config
    except Exception as e:
        logger.error(f"读取配置文件失败: {e}")
        logger.error("请确保config.json存在且格式正确")
        sys.exit()


def main():
    try:
        config = get_config()
        wb = Weibo(config)
        wb.start()  # 爬取微博信息
        if const.NOTIFY["NOTIFY"]:
            push_deer("更新了一次微博")
    except Exception as e:
        if const.NOTIFY["NOTIFY"]:
            push_deer("weibo-crawler运行出错，错误为{}".format(e))
        logger.exception(e)


if __name__ == "__main__":
    main()
