#!/usr/bin/env python
# -*- coding: utf-8 -*-

import copy
import json
import logging
import logging.config
import math
import os
import random
import re
import shutil
import sys
import warnings
import webbrowser
from collections import OrderedDict
from datetime import date, datetime, timedelta
from time import sleep
from urllib.parse import parse_qs, unquote, urlparse

import json5
import requests
from lxml import etree
from requests.adapters import HTTPAdapter  # noqa: F401  # 再导出：util/config/config_loader 用 from weibo import HTTPAdapter
from requests.exceptions import RequestException

import const
from util.core import csvutil
from util.core import archive_index
from util.report import archive_check
from util.report import deleted_weibos
from util.report import media_check_report
from util.report import write_failure_report
from util.report import invisible_archive
from util.fetch import weibo_parse
from util.config import config_file
from util.report import crawl_summary
from util.media import media_check
from util.comment import comment_fetch
from util.core import file_lock
from util.core import markdown_blocks
from util.core import archive_state
from util.report import change_report
from util.report import reporting
from util.media import not_downloaded
from util.config import config_paths
from util.media import media_download
from util.media import media_time
from util.http import http_client
from util.http import weibo_json
from util.comment import comment_retry
from util.comment import comment_backfill
from util.comment import comment_report
from util.comment import comment_images
from util.core import atomic_io
from util.core import run_loop
from util.edit import edit_updates
from util.markdown import markdown_render
from util.comment import comment_worker
from util.config import config_loader
from util.media import media_jobs
from util.markdown import markdown_file
from util.fetch import fetch_pages
from util.storage import csv_json as storage
from util.storage import sqlite as storage_sqlite
from util.fetch import user_info
from util.comment import comment_cookie
from util.comment import comment_export
from util.core import write_pipeline
from util.core import errors
from util.legacy import backends as legacy_backends
from util.misc.notify import push_deer

warnings.filterwarnings("ignore")

_BASE_DIR = os.path.split(os.path.realpath(__file__))[0]


def _init_logging(base_dir):
    """初始化日志。

    - 配置文件：``<base>/config/logging.conf``（兼容根目录旧位置）；
    - 日志目录：默认 ``<base>/log``，不再取决于进程当前工作目录；
    - ``WEIBO_LOG_DIR``：把文件日志重定向到指定目录（测试用，避免写仓库 log/）；
    - ``WEIBO_LOG_DISABLE=1``：不写文件，只输出到控制台。
    """
    logging_path = os.path.join(base_dir, "config", "logging.conf")
    if not os.path.isfile(logging_path):
        logging_path = os.path.join(base_dir, "logging.conf")
    if not os.path.isfile(logging_path):
        logging.basicConfig(level=logging.INFO)
        return
    redirect_dir = os.environ.get("WEIBO_LOG_DIR")
    disable_file = os.environ.get("WEIBO_LOG_DISABLE") == "1"

    # logging.conf 的 handler 用相对路径（log/all.log），fileConfig 按当前工作目录
    # 解析；先切到项目根再加载，保证从任意目录运行都落到 <base>/log。
    previous_cwd = os.getcwd()
    try:
        os.chdir(base_dir)
        os.makedirs("log", exist_ok=True)
        logging.config.fileConfig(logging_path)
    finally:
        os.chdir(previous_cwd)

    if not (disable_file or redirect_dir):
        return

    loggers = (logging.getLogger(), logging.getLogger("weibo"))
    handlers = {}
    for logger_obj in loggers:
        for handler in logger_obj.handlers:
            if isinstance(handler, logging.FileHandler):
                handlers[id(handler)] = handler
    for handler in handlers.values():
        try:
            handler.close()
        except OSError:
            pass
        if disable_file:
            for logger_obj in loggers:
                if handler in logger_obj.handlers:
                    logger_obj.removeHandler(handler)
        else:
            name = os.path.basename(getattr(handler, "baseFilename", "app.log"))
            handler.baseFilename = os.path.join(redirect_dir, name)
            try:
                handler.stream = handler._open()
            except OSError:
                handler.stream = sys.stderr


_init_logging(_BASE_DIR)
logger = logging.getLogger("weibo")

# 日期时间格式
from util.core.time_format import DTFORMAT

# 写入 CSV/数据库时允许进入结果列的微博字段（顺序与 get_result_headers 对应）。
# 显式白名单可避免附加字段（例如 LLM 分析结果 llm_analysis）把后续列整体挤偏
# 。
WRITE_INFO_KEYS = (
    "id",
    "bid",
    "text",
    "article_url",
    "pics",
    "video_url",
    "live_photo_url",
    "location",
    "created_at",
    "source",
    "attitudes_count",
    "comments_count",
    "reposts_count",
    "topics",
    "at_users",
    "full_created_at",
    "edited",
    "edit_count",
)


def resolve_cookie(env_value, cookie_config, project_root):
    """解析 Cookie 配置，返回 ``(cookie_string, cookie_file_path)``。

    优先级：环境变量 ``WEIBO_COOKIE`` > ``config.cookie``。
    ``config.cookie`` 可以是字面 Cookie 串，或以 ``.txt`` 结尾的文件路径；
    文件相对路径按项目根解析（不是当前工作目录），多行文件会按行/分号
    拆分后重新拼接。

    ``cookie_file_path`` 为绝对路径或 None；抓取结束会把最新 Cookie 写回该文件。
    """
    cookie_string = env_value or cookie_config
    cookie_file_path = None
    if not (isinstance(cookie_config, str) and cookie_config.endswith(".txt")):
        if env_value:
            logger.info("使用环境变量WEIBO_COOKIE中的Cookie")
        return cookie_string, cookie_file_path

    cookie_path = config_paths.resolve_config_file(project_root, cookie_config)
    cookie_file_path = cookie_path
    if env_value:
        # 环境变量严格优先：忽略文件内容，但仍记录路径，
        # 抓取结束后 save_cookies_to_file() 会把最新 Cookie 写回该文件。
        logger.info(
            "使用环境变量 WEIBO_COOKIE 中的 Cookie（config.cookie 指向 %s，本次忽略文件内容）",
            cookie_path,
        )
        return cookie_string, cookie_file_path
    if not os.path.isfile(cookie_path):
        logger.warning(
            "Cookie文件 %s 不存在，将以无 Cookie 状态运行；"
            "请检查路径或改用环境变量 WEIBO_COOKIE",
            cookie_path,
        )
        return "", cookie_file_path
    with open(cookie_path, "r", encoding="utf-8") as f:
        raw_cookie = f.read().strip()
    # 兼容多行 Cookie 文件（每行 key=value 或 key=value; ...）
    cookie_string = "; ".join(
        part.strip()
        for line in raw_cookie.splitlines()
        for part in line.split(";")
        if part.strip()
    )
    if cookie_string:
        logger.info("从Cookie文件 %s 读取Cookie", cookie_path)
    else:
        logger.warning("Cookie文件 %s 为空，将以无 Cookie 状态运行", cookie_path)
    return cookie_string, cookie_file_path


class Weibo(object):
    def __init__(self, config):
        """Weibo类初始化（实现见 util/config/config_loader.py）。"""
        config_loader.apply(self, config)

    def validate_config(self, config):
        """配置校验（实现见 util/config/config_loader.py）。"""
        return config_loader.validate(self, config)

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
        """请求 m.weibo.cn API 并返回 (json, status)（实现见 util/http/http_client.py）。"""
        return http_client.fetch_json(
            self.session,
            "https://m.weibo.cn/api/container/getIndex?",
            params=params,
            headers=self.headers,
            logger=logger,
        )

    # ---- 防封禁（实现见 util/config/config_loader.py；这里保持一层薄转发）
    def get_random_headers(self):
        """随机请求头（防封禁）。"""
        return config_loader.get_random_headers(self)

    def calculate_dynamic_delay(self):
        """动态请求延迟（防封禁）。"""
        return config_loader.calculate_dynamic_delay(self)

    def should_pause_session(self):
        """是否应暂停当前会话（防封禁）。"""
        return config_loader.should_pause_session(self)

    def check_batch_delay(self):
        """批次延迟检查（防封禁）。"""
        return config_loader.check_batch_delay(self)

    def update_crawl_stats(self, weibo_count=0, request_count=0, api_error=False):
        """更新爬取统计（防封禁）。"""
        return config_loader.update_crawl_stats(
            self, weibo_count, request_count, api_error
        )

    def reset_crawl_stats(self):
        """重置爬取统计（防封禁）。"""
        return config_loader.reset_crawl_stats(self)

    def perform_anti_ban_rest(self):
        """执行防封禁休息（防封禁）。"""
        return config_loader.perform_anti_ban_rest(self)

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
        """请求并解析一页微博 JSON（实现见 util/http/weibo_json.py）。"""
        return weibo_json.get_weibo_json(self, page)

    def user_to_csv(self):
        """将爬取到的用户信息写入csv文件"""
        file_dir = self._output_root_dir()
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
        result_data = [list(self.user.values())]
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

    def user_to_database(self):
        """将用户信息写入文件/数据库（只读模式下整体跳过）。

        MySQL / MongoDB 后端（实现见 util/legacy/backends.py，未在真实数据库验证；
        失败只记失败报告、不阻断其他后端）。
        """
        if getattr(self, "readonly_mode", False):
            return
        self.user_to_csv()
        if "mysql" in self.write_mode:
            self._run_write_step("MySQL 用户写入", self.user_to_mysql)
        if "mongo" in self.write_mode or "mongodb" in self.write_mode:
            self._run_write_step("MongoDB 用户写入", self.user_to_mongodb)
        if "sqlite" in self.write_mode:
            self.user_to_sqlite()

    def user_to_mysql(self):
        """将用户信息写入 MySQL（上游后端，未验证；依赖见 requirements-legacy.txt）。"""
        return legacy_backends.user_to_mysql(self)

    def user_to_mongodb(self):
        """将用户信息写入 MongoDB（上游后端，未验证）。"""
        return legacy_backends.user_to_mongodb(self)

    def weibo_to_mysql(self, wrote_count):
        """将微博写入 MySQL（上游后端，未验证；失败返回 False 不抛异常）。"""
        return legacy_backends.weibo_to_mysql(self, wrote_count)

    def weibo_to_mongodb(self, wrote_count):
        """将微博写入 MongoDB（上游后端，未验证）。"""
        return legacy_backends.weibo_to_mongodb(self, wrote_count)

    def write_post(self, wrote_count):
        """把本轮微博通过 POST 推送到接口（上游后端，未验证）。"""
        return legacy_backends.write_post(self, wrote_count)

    def info_to_mongodb(self, collection, info_list):
        """MongoDB 通用写入（保留上游方法名，供 backends 内部/外部调用）。"""
        return legacy_backends.info_to_mongodb(self, collection, info_list)

    def mysql_create(self, connection, sql):
        """执行 MySQL 建库/建表 SQL（保留上游方法名）。"""
        return legacy_backends.mysql_create(self, connection, sql)

    def mysql_create_database(self, mysql_config, sql):
        """创建 MySQL 数据库（保留上游方法名）。"""
        return legacy_backends.mysql_create_database(self, mysql_config, sql)

    def mysql_create_table(self, mysql_config, sql):
        """创建 MySQL 表（保留上游方法名）。"""
        return legacy_backends.mysql_create_table(self, mysql_config, sql)

    def mysql_insert(self, mysql_config, table, data_list):
        """向 MySQL 表插入/更新（保留上游方法名）。"""
        return legacy_backends.mysql_insert(self, mysql_config, table, data_list)

    def get_user_info(self):
        """获取并解析用户信息（实现见 util/fetch/user_info.py）。"""
        return user_info.get_user_info(self)

    def get_long_weibo(self, id):
        """获取长微博；失败返回 None，由调用方回退到截断正文的 parse_weibo。

        请求带 timeout；详情页异常不会冒泡到单条解析（由调用方隔离）。
        """
        url = "https://m.weibo.cn/detail/%s" % id
        logger.info(f"""URL: {url} """)
        for i in range(5):
            sleep(random.uniform(1.0, 2.5))
            try:
                response = self.session.get(
                    url, headers=self.headers, verify=False, timeout=10
                )
                html = response.text
                html = html[html.find('"status":') :]
                html = html[: html.rfind('"call"')]
                html = html[: html.rfind(",")]
                html = "{" + html + "}"
                js = json.loads(html, strict=False)
            except (RequestException, ValueError, TypeError, AttributeError) as e:
                logger.warning("长微博详情页获取失败（第 %d/5 次）: %s", i + 1, e)
                continue
            weibo_info = js.get("status")
            if weibo_info:
                try:
                    return self.parse_weibo(weibo_info)
                except Exception as e:  # noqa: BLE001 兜底：单点失败不阻断主流程
                    logger.warning("长微博内容解析失败，回退截断正文: %s", e)
                    return None
        logger.warning("长微博详情页获取失败，回退为截断正文: %s", id)
        return None

    def get_pics(self, weibo_info):
        """获取微博原始图片url"""
        pic_list = []
        if weibo_info.get("pics"):
            pic_info = weibo_info["pics"]
            for pic in pic_info:
                if not isinstance(pic, dict) or not pic.get('large'):
                    continue
                # 跳过视频类型（多视频微博中视频以 type=video 存在 pics 中）
                if pic.get('type') == 'video':
                    continue
                url = pic['large']['url']
                # 将 URL 中的非原图尺寸标识替换为 large，确保获取原图
                url = re.sub(
                    r'/(mw\d+|bmiddle|thumb\d+|orj\d+|woriginal)/',
                    '/large/', url
                )
                pic_list.append(url)

        # 兼容正文里的“查看图片”类型链接，这类内容有时不落在 pics 数组里
        for url in self.get_inline_image_urls(weibo_info):
            if url not in pic_list:
                pic_list.append(url)

        pics = ",".join(pic_list) if pic_list else ""
        return pics

    def normalize_inline_url(self, href):
        """规范化正文中的跳转链接，优先还原 sinaurl 包裹的真实地址"""
        if not href:
            return ""

        href = href.strip()
        if href.startswith("//"):
            return "https:" + href
        if href.startswith("/"):
            return "https://m.weibo.cn" + href

        parsed = urlparse(href)
        if "sinaurl" in parsed.path:
            query = parse_qs(parsed.query)
            target = query.get("u", [""])[0]
            if target:
                return unquote(target)
        return href

    def is_inline_image_url(self, url):
        """判断正文中的链接是否直接指向图片资源"""
        if not url:
            return False
        lower_url = url.lower().split("?")[0]
        return lower_url.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif"))

    def get_link_urls(self, selector):
        """获取正文中的普通链接，保留真实 URL 以便后续检索"""
        link_urls = []
        for a in selector.xpath("//a"):
            href = a.xpath("@href")
            if not href:
                continue

            link_text = a.xpath("string(.)").strip()
            raw_href = href[0]

            # @用户/话题/页内跳转不作为普通链接保存
            if raw_href.startswith("/n/") or raw_href.startswith("#"):
                continue
            if len(link_text) > 2 and link_text[0] == "#" and link_text[-1] == "#":
                continue

            normalized_url = self.normalize_inline_url(raw_href)
            if not normalized_url or self.is_inline_image_url(normalized_url):
                continue

            if normalized_url not in link_urls:
                link_urls.append(normalized_url)
        return link_urls

    def get_inline_image_urls(self, weibo_info):
        """提取正文中以内嵌链接形式出现的图片 URL"""
        text_body = weibo_info.get("text", "")
        if not text_body:
            return []

        selector = etree.HTML(f"{text_body}<hr>" if text_body.isspace() else text_body)
        image_urls = []
        for a in selector.xpath("//a"):
            href = a.xpath("@href")
            if not href:
                continue
            normalized_url = self.normalize_inline_url(href[0])
            if self.is_inline_image_url(normalized_url) and normalized_url not in image_urls:
                image_urls.append(normalized_url)
        return image_urls


    def get_live_photo_url(self, weibo_info):
        """获取Live Photo视频URL及其在过滤后pics数组中的位置索引

        从 pics 数组中提取 type='livephoto' 的 videoSrc 字段，
        并使用与 get_pics() 相同的过滤逻辑，使 live_photo 的文件名索引
        与对应的 still image 文件名索引保持一致。

        返回 dict: {"url": 分号分隔的URL字符串, "indices": [1-based位置列表]}
        """
        pic_info = weibo_info.get("pics") or []
        urls = []
        indices = []

        if pic_info:
            filtered_pos = 0  # 在过滤后的 pics 列表中的 1-based 位置
            for pic in pic_info:
                # 与 get_pics() 保持完全一致的过滤逻辑
                if not isinstance(pic, dict) or not pic.get('large'):
                    continue
                if pic.get('type') == 'video':
                    continue
                filtered_pos += 1

                # 检查该 pic 是否是 livephoto 类型并有 videoSrc
                if pic.get('type') == 'livephoto' and pic.get('videoSrc'):
                    urls.append(pic['videoSrc'])
                    indices.append(filtered_pos)

        return {
            "url": ";".join(urls) if urls else "",
            "indices": indices  # 1-based 位置列表
        }

    def get_video_url(self, weibo_info):
        """获取微博普通视频URL"""
        video_urls = []
        # 1. 从 pics 中提取多视频（多视频微博中视频以 type=video 存在 pics 中，
        #    视频URL在 videoSrc 字段）
        if weibo_info.get("pics"):
            for pic in weibo_info["pics"]:
                if (isinstance(pic, dict) and pic.get("type") == "video"
                        and pic.get("videoSrc")):
                    video_urls.append(pic["videoSrc"])
        # 2. 如果 pics 中没有视频，回退到 page_info（单视频兼容）
        if not video_urls and weibo_info.get("page_info"):
            if weibo_info["page_info"].get("type") == "video":
                media_info = (weibo_info["page_info"].get("urls")
                             or weibo_info["page_info"].get("media_info"))
                if media_info:
                    url = (media_info.get("mp4_720p_mp4") or
                           media_info.get("mp4_hd_mp4") or
                           media_info.get("mp4_hd_url") or
                           media_info.get("hevc_mp4_hd") or
                           media_info.get("mp4_sd_url") or
                           media_info.get("mp4_ld_mp4") or
                           media_info.get("stream_url_hd") or
                           media_info.get("stream_url"))
                    if url:
                        video_urls.append(url)
        return ";".join(video_urls)

    def write_exif_time(self, file_path, time_str):
        """写入 JPG EXIF 元数据（实现见 util/media/media_time.py）。"""
        return media_time.write_exif_time(
            file_path, time_str, getattr(self, "write_time_in_exif", 0), logger
        )

    def set_file_time(self, file_path, time_str):
        """修改文件系统时间（实现见 util/media/media_time.py）。"""
        return media_time.set_file_time(
            file_path, time_str, getattr(self, "change_file_time", 0), logger
        )

    def _user_report_dir_name(self):
        """报告按用户分目录时使用的目录名（实现见 util/config/config_paths.py）。"""
        return config_paths.account_dir_name(
            self.user,
            self.user_config,
            getattr(self, "user_id_as_folder_name", 0),
            sanitize=True,
        )

    def _report_output_dir(self, configured, default="report"):
        """报告/记录目录：``<报告根目录>/<当前用户目录名>/``（实现见 util/config/config_paths.py）。"""
        project_dir = _BASE_DIR
        return config_paths.report_dir(
            project_dir,
            self.output_directory,
            configured,
            self._user_report_dir_name(),
            default,
        )

    def _not_downloaded_path(self, file_path):
        """下载失败记录的位置（实现见 util/media/not_downloaded.py）。"""
        return not_downloaded.record_path(
            file_path,
            getattr(self, "not_downloaded_record_dir", ""),
            self._report_output_dir,
        )

    def _clear_not_downloaded_record(self, error_file, weibo_id, url):
        """文件已下载成功时，从 not_downloaded.txt 删除对应的旧失败记录。"""
        not_downloaded.clear_record(error_file, weibo_id, url)

    def _media_session_factory(self):
        """媒体下载复用同一个 requests.Session。

        整轮媒体下载复用同一个会话（只用到 get/stream/headers），避免每个文件
        新建连接。
        """
        if getattr(self, "_download_session", None) is None:
            self._download_session = requests.Session()
        return self._download_session

    def download_one_file(self, url, file_path, type, weibo_id, created_at):
        """下载单个文件(图片/视频)，返回最终保存路径；失败返回 None。

        核心下载逻辑见 ``util/media/media_download.py``；这里只传回调
        （原子写、EXIF/时间、sqlite 插入、失败记录）并维护失败计数。
        """
        record_not_downloaded = getattr(self, "not_downloaded_record", 0)
        error_file = (
            self._not_downloaded_path(file_path) if record_not_downloaded else None
        )
        sqlite_exist = (
            self.sqlite_exist_file(file_path) if "sqlite" in self.write_mode else False
        )

        def _finalize(path, detected_extension):
            if detected_extension in (".jpg", ".jpeg"):
                try:
                    self.write_exif_time(path, created_at)
                except Exception as e:  # noqa: BLE001
                    logger.error("写入EXIF失败: %s", e)
            try:
                self.set_file_time(path, created_at)
            except Exception as e:  # noqa: BLE001
                logger.error("修改文件系统时间失败: %s", e)

        def _on_success_sqlite(path, data):
            try:
                self.insert_file_sqlite(path, weibo_id, url, data)
            except Exception as e:  # noqa: BLE001
                logger.warning("写入 sqlite 失败: %s", e)

        result = media_download.download_one_file(
            url,
            file_path,
            self.headers,
            type=type,
            clear_record=(
                lambda: self._clear_not_downloaded_record(error_file, weibo_id, url)
                if error_file
                else None
            ),
            atomic_write_bytes=self._atomic_write_bytes,
            finalize=_finalize,
            on_success_sqlite=(
                _on_success_sqlite
                if ("sqlite" in self.write_mode and not sqlite_exist)
                else None
            ),
            validate_file=media_check.validate_file,
            check_content_length=media_check.check_content_length,
            validate_bytes=media_check.validate_bytes,
            image_extensions=media_check.IMAGE_EXTENSIONS,
            video_extensions=media_check.VIDEO_EXTENSIONS,
            session_factory=self._media_session_factory,
            sleep_func=sleep,
            logger=logger,
        )
        if result is None:
            self.media_failure_count = getattr(self, "media_failure_count", 0) + 1
            if error_file:
                try:
                    with open(error_file, "ab") as handle:
                        handle.write(f"{weibo_id}:{file_path}:{url}\n".encode("utf-8"))
                except OSError as e:  # noqa: BLE001
                    logger.warning("写入下载失败记录失败: %s", e)
            return None
        return result

    def sqlite_exist_file(self, url):
        """实现见 util/storage/sqlite.py。"""
        return storage_sqlite.sqlite_exist_file(self, url)

    def insert_file_sqlite(self, file_path, weibo_id, url, binary):
        """实现见 util/storage/sqlite.py。"""
        return storage_sqlite.insert_file_sqlite(self, file_path, weibo_id, url, binary)

    def _get_timestamp_prefix(self, created_at):
        """根据微博发布时间生成人类可读的文件名前缀
        格式：YYYY-MM-DD_HH-MM-SS，与 _download_weibo_images 保持一致
        """
        try:
            time_obj = datetime.strptime(created_at, DTFORMAT)
            date_str = time_obj.strftime("%Y-%m-%d")
            time_str = time_obj.strftime("%H:%M:%S")
            return f"{date_str}_{time_str.replace(':', '-')}"
        except (ValueError, KeyError):
            # 回退：使用旧的截断格式（不再引用未定义的 w，避免 NameError）
            return created_at[:11].replace("-", "") or "unknown_time"

    def handle_download(self, file_type, file_dir, urls, w):
        """实现见 util/media/media_jobs.py。"""
        return media_jobs.handle_download(self, file_type, file_dir, urls, w)

    def get_download_file_names(self, file_type, urls, w):
        """生成下载文件名列表（实现见 util/media/media_jobs.py）。"""
        return media_jobs.get_download_file_names(self, file_type, urls, w)

    def download_files(self, file_type, weibo_type, wrote_count):
        """按类型下载媒体文件（实现见 util/media/media_jobs.py）。"""
        return media_jobs.download_files(self, file_type, weibo_type, wrote_count)

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
        """字符串转换为整数（兼容 1.2万+、1.5亿、千分位、空值等）。

        带单位（万/亿）、千分位与加号的数值都能正确解析。
        """
        if isinstance(string, bool):
            return int(string)
        if isinstance(string, (int, float)):
            return int(string)
        text = str(string or "").strip().replace(",", "").replace("+", "")
        if not text:
            return 0
        for suffix, unit in (("万", 10000), ("亿", 100000000)):
            if text.endswith(suffix):
                value = text[: -len(suffix)]
                try:
                    return int(float(value) * unit)
                except ValueError:
                    return 0
        try:
            return int(text)
        except ValueError:
            try:
                return int(float(text))
            except ValueError:
                return 0

    def _parse_absolute_date(self, text):
        """解析绝对时间：兼容 ISO、常见英文格式与显式 locale 无关的 %c 等价格式。"""
        raw = (text or "").strip()
        if not raw:
            return None
        candidates = [raw]
        if "+" in raw:
            candidates.append(raw.replace("+0800", "").strip())
            candidates.append(raw.replace("+0800", "+08:00").strip())
        formats = [
            DTFORMAT,
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%a %b %d %H:%M:%S %z %Y",
            "%a %b %d %H:%M:%S %Y",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%f%z",
        ]
        for candidate in candidates:
            for fmt in formats:
                try:
                    return datetime.strptime(candidate, fmt)
                except (TypeError, ValueError):
                    continue
        return None

    def standardize_date(self, created_at):
        """标准化微博发布时间；无法解析时回退为当前时间并记录日志。

        支持「刚刚 / N 分钟前 / N 小时前 / 昨天」等相对时间与常见绝对格式；
        解析失败按当前时间处理，不会抛异常。
        """
        text = str(created_at or "").strip()
        ts = None
        try:
            if "刚刚" in text:
                ts = datetime.now()
            elif "分钟前" in text:
                minute = text[: text.find("分钟前")]
                ts = datetime.now() - timedelta(minutes=int(minute))
            elif "分钟" in text:
                minute = text[: text.find("分钟")]
                ts = datetime.now() - timedelta(minutes=int(minute))
            elif "小时前" in text:
                hour = text[: text.find("小时前")]
                ts = datetime.now() - timedelta(hours=int(hour))
            elif "小时" in text:
                hour = text[: text.find("小时")]
                ts = datetime.now() - timedelta(hours=int(hour))
            elif "昨天" in text:
                ts = datetime.now() - timedelta(days=1)
            else:
                ts = self._parse_absolute_date(text)
        except (TypeError, ValueError) as e:
            logger.warning("微博发布时间解析失败: %r（%s）", created_at, e)
        if ts is None:
            logger.warning("微博发布时间无法识别，按当前时间处理: %r", created_at)
            ts = datetime.now()

        created_at = ts.strftime(DTFORMAT)
        full_created_at = ts.strftime("%Y-%m-%d %H:%M:%S")
        return created_at, full_created_at

    def standardize_info(self, weibo):
        """标准化信息：去掉零宽字符等不可见字符。

        纯字符串清理，不依赖终端编码。
        """
        for k, v in list(weibo.items()):
            if isinstance(v, str):
                weibo[k] = v.replace("\u200b", "").replace("\ufeff", "")
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
        live_photo_data = self.get_live_photo_url(weibo_info)
        weibo["live_photo_url"] = live_photo_data["url"]  # Live Photo视频URL
        weibo["live_photo_indices"] = live_photo_data["indices"]  # Live Photo在过滤后pics中的1-based索引
        weibo["links"] = self.get_link_urls(selector)
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

    def get_one_weibo(self, info):
        """单条微博解析（实现见 util/fetch/weibo_parse.py）。"""
        return weibo_parse.get_one_weibo(self, info)

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

    def _get_weibo_comments_cookie(self, weibo, cur_count, max_count, max_id, on_downloaded):
        """实现见 util/comment/comment_cookie.py。"""
        return comment_cookie._get_weibo_comments_cookie(self, weibo, cur_count, max_count, max_id, on_downloaded)

    def _get_weibo_comments_nocookie(self, weibo, cur_count, max_count, page, on_downloaded):
        """实现见 util/comment/comment_cookie.py。"""
        return comment_cookie._get_weibo_comments_nocookie(self, weibo, cur_count, max_count, page, on_downloaded)

    def _get_weibo_reposts_cookie(self, weibo, cur_count, max_count, page, on_downloaded):
        """实现见 util/comment/comment_cookie.py。"""
        return comment_cookie._get_weibo_reposts_cookie(self, weibo, cur_count, max_count, page, on_downloaded)

    def get_one_page(self, page):
        """get_one_page（实现见 util/fetch/fetch_pages.py）。"""
        return fetch_pages.get_one_page(self, page)

    def get_page_count(self):
        """获取微博页数。

        先校验配置再除法，配置为字符串/0/None 时返回兜底值而不是抛异常。
        """
        try:
            weibo_count = self.user["statuses_count"]
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
            return None
        page_weibo_count = self.page_weibo_count
        if (
            isinstance(page_weibo_count, bool)
            or not isinstance(page_weibo_count, int)
            or page_weibo_count <= 0
        ):
            logger.error(
                "config.json 中 page_weibo_count 必须是正整数（当前值: %r），无法计算页数",
                page_weibo_count,
            )
            return None
        try:
            return int(math.ceil(weibo_count / page_weibo_count))
        except (TypeError, ValueError, ZeroDivisionError) as e:
            logger.error("计算微博页数失败（statuses_count=%r）: %s", weibo_count, e)
            return None

    def get_write_info(self, wrote_count):
        """获取要写入的微博信息。

        只输出 `WRITE_INFO_KEYS` 白名单内的字段，保证 CSV 列与固定表头一致。
        """
        write_info = []
        for w in self.weibo[wrote_count:]:
            wb = OrderedDict()
            for k, v in w.items():
                if k not in WRITE_INFO_KEYS:
                    continue
                if k == "id":
                    v = str(v) + "\t"
                wb[k] = v
            if not self.only_crawl_original:
                if w.get("retweet"):
                    wb["is_original"] = False
                    retweet = w["retweet"]
                    # 表头在「是否原创」后还有「源用户id / 源用户昵称」两列
                    # （见 get_result_headers），数据源是转发 dict 的
                    # user_id/screen_name。WRITE_INFO_KEYS 刻意不含这两个键
                    # （避免原创行被附加字段挤偏），所以这里显式补两列；
                    # 否则含转发的行比表头少 2 列、其后「源微博*」整体左移
                    # 。
                    wb["retweet_user_id"] = retweet.get("user_id", "")
                    wb["retweet_screen_name"] = retweet.get("screen_name", "")
                    for k2, v2 in retweet.items():
                        if k2 not in WRITE_INFO_KEYS:
                            continue
                        if k2 == "id":
                            v2 = str(v2) + "\t"
                        wb["retweet_" + k2] = v2
                else:
                    wb["is_original"] = True
            write_info.append(wb)
        return write_info

    def get_filepath(self, type):
        """获取结果文件路径。

        ``output_directory`` 支持绝对路径（模板注释的承诺）：用统一的
        ``_output_root_dir()`` 解析，避免把绝对路径再拼到项目目录后面，
        导致数据目录与报告目录分裂。
        """
        try:
            dir_name = str((self.user or {}).get("screen_name") or "")
            if not dir_name:
                # 用户信息未获取（例如接口失败被跳过）：退回用户 ID，避免 KeyError
                # 噪音。
                dir_name = str((self.user_config or {}).get("user_id") or "unknown")
            if self.user_id_as_folder_name:
                dir_name = str(self.user_config["user_id"])
            file_dir = os.path.join(self._output_root_dir(), dir_name)
            if type in ("img", "video", "live_photo"):
                file_dir = os.path.join(file_dir, type)
            if not getattr(self, "readonly_mode", False) and not os.path.isdir(file_dir):
                os.makedirs(file_dir)
            if type in ("img", "video", "live_photo", "markdown"):
                # markdown 返回目录路径，文件名在 generate_markdown_file 中指定
                return file_dir
            return os.path.join(file_dir, str(self.user_config["user_id"]) + "." + type)
        except Exception as e:  # noqa: BLE001 兜底：单点失败不阻断主流程
            logger.exception("获取文件路径失败（type=%s）: %s", type, e)

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

    def _read_csv_ids(self, file_path):
        """实现见 util/storage/csv_json.py。"""
        return storage._read_csv_ids(self, file_path)

    def write_csv(self, wrote_count):
        """实现见 util/storage/csv_json.py。"""
        return storage.write_csv(self, wrote_count)

    def csv_helper(self, headers, result_data, file_path):
        """实现见 util/storage/csv_json.py。"""
        return storage.csv_helper(self, headers, result_data, file_path)

    def update_json_data(self, data, weibo_info):
        """实现见 util/storage/csv_json.py。"""
        return storage.update_json_data(self, data, weibo_info)

    def _atomic_write_text(self, path, text):
        """原子写文本（实现见 util/core/atomic_io.py）。"""
        atomic_io.atomic_write_text(path, text)

    def _atomic_write_bytes(self, path, data):
        """原子写二进制（实现见 util/core/atomic_io.py）。"""
        atomic_io.atomic_write_bytes(path, data)

    def write_json(self, wrote_count):
        """实现见 util/storage/csv_json.py。"""
        return storage.write_json(self, wrote_count)

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

    def weibo_to_sqlite(self, wrote_count):
        """写入 SQLite；评论/转发抓取异常按单条微博隔离，连接必定关闭。

        单条微博的评论请求/解析异常按条隔离，不会中断后续写入；连接始终关闭。
        """
        con = self.get_sqlite_connection()
        try:
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
                if download_comment and (weibo.get("comments_count") or 0) > 0:
                    try:
                        self.get_weibo_comments(
                            weibo, comment_max_count, self.sqlite_insert_comments
                        )
                    except Exception as e:  # noqa: BLE001 兜底：单点失败不阻断主流程
                        logger.warning(
                            "抓取评论失败，跳过该微博（%s）: %s", weibo.get("id"), e
                        )
                    count += 1
                    if count % 20 == 0:
                        sleep(random.randint(3, 6))
                if download_repost and (weibo.get("reposts_count") or 0) > 0:
                    try:
                        self.get_weibo_reposts(
                            weibo, repost_max_count, self.sqlite_insert_reposts
                        )
                    except Exception as e:  # noqa: BLE001 兜底：单点失败不阻断主流程
                        logger.warning(
                            "抓取转发失败，跳过该微博（%s）: %s", weibo.get("id"), e
                        )
                    count += 1
                    if count % 20 == 0:
                        sleep(random.randint(3, 6))

            for weibo in retweet_list:
                self.sqlite_insert_weibo(con, weibo)
        finally:
            con.close()

    def export_comments_to_csv_for_current_user(self):
        """导出评论 CSV（实现见 util/comment/comment_export.py）。"""
        return comment_export.export_comments_to_csv_for_current_user(self)

    def sqlite_insert_comments(self, weibo, comments):
        """实现见 util/storage/sqlite.py。"""
        return storage_sqlite.sqlite_insert_comments(self, weibo, comments)

    def sqlite_insert_reposts(self, weibo, reposts):
        """实现见 util/storage/sqlite.py。"""
        return storage_sqlite.sqlite_insert_reposts(self, weibo, reposts)

    def parse_sqlite_comment(self, comment, weibo):
        """实现见 util/storage/sqlite.py。"""
        return storage_sqlite.parse_sqlite_comment(self, comment, weibo)

    def parse_sqlite_repost(self, repost, weibo):
        """实现见 util/storage/sqlite.py。"""
        return storage_sqlite.parse_sqlite_repost(self, repost, weibo)

    def _try_get_value(self, source_name, target_name, target_dict, source):
        """从源字段取值；源对象缺失/字段缺失时留空，不抛异常。

        参数名不能用 ``dict``/``json``：会遮蔽内建类型与模块。
        """
        target_dict[source_name] = ""
        value = source.get(target_name) if isinstance(source, dict) else None
        if value:
            target_dict[source_name] = value

    def sqlite_insert_weibo(self, con, weibo):
        """实现见 util/storage/sqlite.py。"""
        return storage_sqlite.sqlite_insert_weibo(self, con, weibo)

    def parse_sqlite_weibo(self, weibo):
        """实现见 util/storage/sqlite.py。"""
        return storage_sqlite.parse_sqlite_weibo(self, weibo)

    def user_to_sqlite(self):
        con = self.get_sqlite_connection()
        self.sqlite_insert_user(con, self.user)
        con.close()

    def sqlite_insert_user(self, con, user):
        """实现见 util/storage/sqlite.py。"""
        return storage_sqlite.sqlite_insert_user(self, con, user)

    def parse_sqlite_user(self, user):
        """实现见 util/storage/sqlite.py。"""
        return storage_sqlite.parse_sqlite_user(self, user)

    def sqlite_insert(self, con, data, table):
        ""
        return storage_sqlite.sqlite_insert(self, con, data, table)

    def get_sqlite_connection(self):
        """实现见 util/storage/sqlite.py。"""
        return storage_sqlite.get_sqlite_connection(self)

    def create_sqlite_table(self, connection):
        """实现见 util/storage/sqlite.py。"""
        return storage_sqlite.create_sqlite_table(self, connection)

    def get_sqlte_path(self):
        """实现见 util/storage/sqlite.py。"""
        return storage_sqlite.get_sqlte_path(self)

    def get_sqlite_create_sql(self):
        """实现见 util/storage/sqlite.py。"""
        return storage_sqlite.get_sqlite_create_sql(self)

    def update_user_config_file(self, user_config_file_path):
        """更新用户列表第三列（实现见 util/config/config_file.py）。"""
        return config_file.update_user_config_file(self, user_config_file_path)

    def write_markdown(self, wrote_count):
        """将爬到的信息写入markdown文件"""
        # 按配置分组微博
        weibo_by_group = self.group_weibo_by_config(wrote_count)

        # 先下载图片（如果需要）：原创与转发图片开关各自独立生效。
        need_images = bool(self.original_pic_download) or (
            not self.only_crawl_original and bool(self.retweet_pic_download)
        )
        if need_images:
            self.download_markdown_images(wrote_count)

        # 为每个分组生成markdown文件
        for group_key, weibo_list in weibo_by_group.items():
            self.generate_markdown_file(group_key, weibo_list)

        logger.info("%d条微博写入markdown文件完毕", self.got_count - wrote_count)

    def _load_comment_user_filter(self):
        """加载评论者白名单：外部 txt 优先，文件不存在或为空时回退 config 内联配置。

        配置相关文件默认放 ``config/`` 目录（裸文件名优先 ``config/`` 下，兼容根目录）；
        格式见 ``config/comment_user_filter.txt.example``：每行「微博账号 评论者1 评论者2 ...」。
        """
        cached = getattr(self, "_comment_user_filter_cache", None)
        if cached is not None:
            return cached
        mapping: dict[str, set[str]] = {}
        file_name = str(getattr(self, "comment_user_filter_file", "") or "").strip()
        if file_name:
            base_dir = os.path.dirname(os.path.realpath(__file__))
            path = config_paths.resolve_config_file(base_dir, file_name)
            mapping = comment_fetch.parse_user_filter_file(path)
            if mapping:
                logger.info(
                    "评论者白名单：从文件 %s 加载 %d 个账号", path, len(mapping)
                )
        if not mapping:
            raw = getattr(self, "comment_user_filter", None)
            if isinstance(raw, dict) and raw:
                for account_key, allowed_users in raw.items():
                    if not isinstance(allowed_users, (list, tuple)):
                        continue
                    allowed = {
                        str(item).strip()
                        for item in allowed_users
                        if str(item).strip()
                    }
                    key_text = str(account_key).strip()
                    if key_text and allowed:
                        mapping[key_text] = allowed
                if mapping:
                    logger.info(
                        "评论者白名单：使用 config 内联配置，%d 个账号", len(mapping)
                    )
        self._comment_user_filter_cache = mapping
        return mapping

    def _comment_allowed_users(self):
        """返回当前微博账号配置的评论者白名单；未配置或配置为空时返回 None。

        账号不在白名单中、或该账号的白名单为空，都表示不限制（保留全部评论）。
        """
        conf = self._load_comment_user_filter()
        if not conf:
            return None
        user_id = str(self.user_config.get("user_id") or "").strip()
        screen_name = str(self.user.get("screen_name") or "").strip()
        for account_key, allowed_users in conf.items():
            key_text = str(account_key).strip()
            if not key_text or key_text not in {user_id, screen_name}:
                continue
            if isinstance(allowed_users, (set, list, tuple)):
                allowed = {
                    str(item).strip() for item in allowed_users if str(item).strip()
                }
                if allowed:
                    return allowed
            return None
        return None

    def _comment_retry_path(self):
        """评论失败重试清单路径（实现见 util/comment/comment_retry.py）。"""
        base_dir = self._report_output_dir(
            getattr(self, "comment_summary_dir", "report")
        )
        user_id = str(self.user_config.get("user_id", "unknown"))
        return comment_retry.retry_path(base_dir, user_id)

    def _load_comment_retry(self):
        """读取上次保存的评论失败重试清单（实现见 util/comment/comment_retry.py）。"""
        return comment_retry.load(self._comment_retry_path(), logger)

    def _save_comment_retry(self, retry_map):
        """原子写评论失败重试清单（实现见 util/comment/comment_retry.py）。"""
        comment_retry.save(
            self._comment_retry_path(), retry_map, self._atomic_write_text, logger
        )

    def _merge_comment_block(self, md_path, weibo_id, new_block_text, original_text=None):
        """合并评论块（实现见 util/comment/comment_backfill.py）。"""
        return comment_backfill.merge_comment_block(
            md_path, weibo_id, new_block_text, original_text=original_text
        )

    def write_markdown_comments_for_new_weibo(self):
        """对本轮新写入/待重试/待刷新的微博抓评论并写回（实现见 util/comment/comment_worker.py）。"""
        return comment_worker.run(self)

    def _download_comment_images(self, result, md_path):
        """下载评论图片到 md 同级的「评论图片」目录（规划逻辑见 util/comment/comment_images.py）。"""
        image_dir = os.path.join(
            os.path.dirname(md_path),
            self.markdown_file_dir.get("comment_image_dir", "评论图片"),
        )
        comments = [
            c
            for c in comment_images.iter_comments(result.roots)
            if getattr(c, "pic_url", "")
        ]
        if not comments:
            return  # 没有评论图片时不创建空目录
        os.makedirs(image_dir, exist_ok=True)
        for comment in comments:
            target = comment_images.image_target(image_dir, comment)
            if target is None:
                continue
            _file_name, path = target
            saved_path = self.download_one_file(
                comment.pic_url,
                path,
                "img",
                comment.weibo_id,
                comment.created_at or "",
            )
            if saved_path:
                comment.pic_file = os.path.basename(saved_path)

    def _emoji_store(self):
        """评论表情本地化存储：下载到 ``weibo_emoji_dir``，md 内改写为相对内链。"""
        store = getattr(self, "_emoji_store_obj", None)
        if store is not None:
            return store
        from util.comment import comment_emoji

        configured = getattr(self, "weibo_emoji_dir", "weibo_data/emoji")
        emoji_dir = comment_emoji.resolve_emoji_dir(configured, _BASE_DIR)

        def _download(url, dest):
            saved = self.download_one_file(url, dest, "img", "emoji", "")
            if not saved:
                return False
            if os.path.abspath(saved) != os.path.abspath(dest) and os.path.isfile(saved):
                try:
                    os.replace(saved, dest)
                except OSError:
                    return os.path.isfile(saved)
            return os.path.isfile(dest)

        store = comment_emoji.EmojiStore(
            emoji_dir,
            _download,
            logger_=logger,
            inline_size=int(getattr(self, "comment_emoji_inline_size", 0) or 0),
        )
        self._emoji_store_obj = store
        return store

    def generate_comment_summary_report(self, results):
        """生成评论下载情况报告。"""
        if not results:
            return
        try:
            base_dir = self._report_output_dir(
                getattr(self, "comment_summary_dir", "report")
            )
            user_id = str(self.user_config.get("user_id", "unknown"))
            screen_name = str(self.user.get("screen_name") or user_id)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            report_path = os.path.join(
                base_dir,
                reporting.report_filename(
                    reporting.PREFIX_COMMENT_SUMMARY,
                    user_id,
                    screen_name,
                    timestamp=timestamp,
                ),
            )
            os.makedirs(base_dir, exist_ok=True)
            self._atomic_write_text(
                report_path,
                comment_report.render_comment_summary(
                    results, screen_name, user_id
                ),
            )
            logger.info("评论下载情况报告已生成: %s", report_path)
        except Exception as e:  # noqa: BLE001
            logger.exception("生成评论下载情况报告失败: %s", e)

    def group_weibo_by_config(self, wrote_count):
        """按配置分组微博"""
        weibo_by_group = {}
        for w in self.weibo[wrote_count:]:
            # 获取微博发布日期（YYYY-MM-DD格式）
            created_at = w.get("created_at", "")
            if not created_at:
                continue

            # 解析日期
            try:
                date_obj = datetime.strptime(created_at, DTFORMAT)

                if self.markdown_split_by in ["day", "day_by_month"]:
                    group_key = date_obj.strftime("%Y-%m-%d")
                elif self.markdown_split_by == "month":
                    group_key = date_obj.strftime("%Y-%m")
                elif self.markdown_split_by == "year":
                    group_key = date_obj.strftime("%Y")
                elif self.markdown_split_by == "all":
                    group_key = "all"
                else:
                    group_key = date_obj.strftime("%Y-%m-%d")

                if group_key not in weibo_by_group:
                    weibo_by_group[group_key] = []
                weibo_by_group[group_key].append(w)
            except ValueError:
                logger.warning(f"无法解析微博日期: {created_at}")
                continue

        # 每个分组内按 markdown_sort_order 排序
        descending = self.markdown_sort_order != "old_to_new"
        for key in weibo_by_group:
            weibo_by_group[key].sort(key=lambda w: int(w.get("id", 0)), reverse=descending)

        return weibo_by_group

    def _finalize_crawl_success(self, last_page, page_count):
        """根据翻页结果判定本轮抓取是否顺利完成。

        结束语义：
        - append 模式命中上次微博 ID：按设计完成；
        - 接口返回无更多数据（api_ended）：按设计完成（平台可能因可见性/上限少返回，
          在 reason 中注明；不再判失败导致每轮重抓、报告永不生成）；
        - 其余没有明确结束信号的提前结束：仍判失败，等下一轮重试。
        """
        cov = self.crawl_success
        if cov.get("success"):
            return
        errors = cov.get("page_errors") or []
        if errors:
            cov["success"] = False
            cov["reason"] = f"第{errors[0]}页请求异常"
            return
        if self.start_page > 1:
            cov["success"] = False
            cov["reason"] = f"从第{self.start_page}页开始，未从范围开头抓取"
            return
        if cov.get("hit_since_date"):
            cov["success"] = True
            cov["reason"] = "命中起始时间下界，本轮抓取顺利完成"
            return
        if cov.get("append_done"):
            cov["success"] = True
            cov["reason"] = "append 模式命中上次微博 ID，本轮增量抓取完成"
            return
        if cov.get("api_ended"):
            cov["success"] = True
            if page_count and last_page < page_count:
                cov["reason"] = (
                    f"接口返回无更多数据（第{last_page}/{page_count}页），按设计完成；"
                    "平台可能因可见性/上限少返回部分微博"
                )
                if page_count - last_page >= 2:
                    logger.warning(
                        "接口在第 %d/%d 页返回无更多数据，与推算页数差距较大；"
                        "请留意是否因无 cookie、可见性限制或平台上限导致少抓。",
                        last_page,
                        page_count,
                    )
            else:
                cov["reason"] = "接口返回无更多数据，本轮抓取顺利完成"
            return
        if page_count == 0 or last_page >= page_count:
            cov["success"] = True
            cov["reason"] = "已扫完所有页，本轮抓取顺利完成"
            return
        cov["success"] = False
        cov["reason"] = f"提前结束于第{last_page}/{page_count}页"

    def _snapshot_archived_ids(self):
        """抓取前记录核对范围内的主存档 ID 与文件路径。

        供本轮存档核对（weibo_archive_check）与本轮抓取情况报告
        （weibo_crawl_summary）共用；两者任一开启时都会构建。
        """
        self.archived_ids_before = None
        self.archived_entries_before = None
        self.archive_scan_stats = None
        self.crawl_summary_baseline_note = ""
        if not (self.archive_check_enabled or getattr(self, "crawl_summary_enabled", 0)):
            return
        if "markdown" not in self.write_mode:
            self.crawl_summary_baseline_note = "write_mode 不含 markdown"
            logger.info("抓取前存档快照：write_mode 不含 markdown，跳过")
            return
        if self.user_config.get("query_list"):
            self.crawl_summary_baseline_note = "关键词(query)模式"
            logger.info("抓取前存档快照：关键词(query)模式跳过")
            return
        try:
            start = datetime.strptime(self.user_config["since_date"], DTFORMAT)
            end = datetime.now()
            md_dir = self.get_filepath("markdown")
            entries, stats = archive_index.collect_archive_entries(
                md_dir, start, end, self._archive_exclude_dirs()
            )
            self.archived_entries_before = entries
            self.archived_ids_before = {
                wid: dt for wid, (dt, _path) in entries.items()
            }
            self.archive_scan_stats = stats
            self.check_start_dt = start
            self.check_end_dt = end
            logger.info(
                "抓取前存档快照：核对范围内主存档 %d 条（扫描 %d 个文件，跳过 %d 个）",
                len(entries), stats.files_scanned, stats.files_skipped,
            )
        except Exception as e:  # noqa: BLE001 兜底：单点失败不阻断主流程
            self.crawl_summary_baseline_note = "抓取前存档扫描失败"
            # 快照失败会静默跳过两份报告；这里打堆栈并写入失败报告。
            if errors.is_programming_error(e):
                logger.exception(
                    "抓取前存档快照出现程序性错误（疑似代码缺陷）；本轮不生成"
                    "存档核对报告/抓取情况报告: %s",
                    e,
                )
            else:
                logger.exception(
                    "抓取前存档快照失败，本轮不生成存档核对报告/抓取情况报告: %s", e
                )
            self._record_write_failure(
                "存档快照", "抓取前扫描", errors.describe_exception(e)
            )
            self.archived_ids_before = None

    def generate_archive_check_report(self):
        """生成本轮存档核对报告（实现见 util/report/archive_check.py）。"""
        return archive_check.generate(self)

    def generate_deleted_weibos_report(self):
        """生成本轮确认已删除微博清单（实现见 util/report/deleted_weibos.py）。"""
        return deleted_weibos.generate(self)

    def generate_crawl_summary_report(self):
        """生成本轮抓取情况报告（实现见 util/report/crawl_summary.py）。"""
        return crawl_summary.generate(self)

    def _record_crawl_summary_row(self, completed, reason=""):
        """记录一行跨账号汇总数据（成功与未完成都记录）。"""
        if not self.user:
            return
        try:
            rows = getattr(self, "crawl_summary_rows", None)
            if not isinstance(rows, list):
                rows = []
                self.crawl_summary_rows = rows
            user_id = str(self.user_config.get("user_id", "unknown"))
            screen_name = str(self.user.get("screen_name") or user_id)
            original = int(getattr(self, "got_count", 0) or len(self.weibo or []))
            retweet = int(getattr(self, "filtered_retweet_count", 0) or 0)
            platform = int(self.user.get("statuses_count") or 0)
            rows.append(
                {
                    "user_id": user_id,
                    "screen_name": screen_name,
                    "original_count": original,
                    "filtered_retweet": retweet,
                    "enumerated_total": original + retweet,
                    "platform_weibo_count": platform,
                    "diff": (platform - (original + retweet)) if platform else 0,
                    "completed": bool(completed),
                    "reason": str(reason or ""),
                }
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("记录抓取汇总行失败: %s", e)

    def generate_crawl_summary_total_report(self):
        """生成跨账号「本轮抓取情况汇总（全量实测）」。

        表格列：用户 / 枚举到原创 / 跳过转发 / 枚举总数 / 平台微博数（含转发）/ 差值。
        放在报告根目录（report/ 下，不按用户分），一轮运行一份。
        """
        if not getattr(self, "crawl_summary_enabled", 0):
            return
        rows = getattr(self, "crawl_summary_rows", None)
        if not rows:
            logger.info("本轮抓取情况汇总：无数据，跳过")
            return
        try:
            base_dir = self._report_output_dir(
                getattr(self, "crawl_summary_dir", "report")
            )
            root_dir = os.path.dirname(base_dir.rstrip("/\\")) or base_dir
            os.makedirs(root_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            report_path = os.path.join(
                root_dir,
                reporting.total_report_filename(
                    reporting.PREFIX_CRAWL_SUMMARY, ext="md", timestamp=timestamp
                ),
            )
            self._atomic_write_text(
                report_path, crawl_summary.render_crawl_summary_total_markdown(rows)
            )
            logger.info(
                "本轮抓取情况汇总已生成: %s（%d 个账号）", report_path, len(rows)
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("生成本轮抓取情况汇总失败: %s", e)

    def generate_write_failure_report(self):
        """实现见 util/report/write_failure_report.py。"""
        return write_failure_report.generate_write_failure_report(self)

    def _load_not_downloaded_map(self):
        """读取 not_downloaded.txt（实现见 util/media/not_downloaded.py）。

        返回 {本地绝对路径: (微博ID, URL)}，用于媒体完整性报告附「微博ID + 原始URL」。
        同一用户内按需缓存一次：媒体完整性报告与隔离区媒体补各调用一次，
        两次之间只读不写记录，复用可省一次整棵用户目录 walk。
        """
        cached = getattr(self, "_not_downloaded_map_cache", None)
        if cached is not None:
            return cached
        try:
            user_dir = self.get_filepath("markdown")
            configured = getattr(self, "not_downloaded_record_dir", "")
            report_dir = self._report_output_dir(
                configured
                if str(configured).strip() not in ("", "media")
                else "report"
            )
            result = not_downloaded.load_map(user_dir, report_dir)
        except Exception as e:  # noqa: BLE001
            logger.warning("读取 not_downloaded.txt 失败: %s", e)
            result = {}
        self._not_downloaded_map_cache = result
        return result

    def generate_media_check_report(self):
        """实现见 util/report/media_check_report.py。"""
        return media_check_report.generate_media_check_report(self)

    def download_markdown_images(self, wrote_count):
        """回填 markdown 媒体文件名（实现见 util/media/media_jobs.py）。"""
        return media_jobs.download_markdown_images(self, wrote_count)

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
        local_images = weibo.setdefault("_local_images", {})
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

            # 下载图片；保存成功后记录实际文件名（扩展名可能与预估不同）
            saved_path = self.download_one_file(
                pic_url, img_path, "img", weibo["id"], created_at
            )
            if saved_path:
                local_images[pic_url] = os.path.basename(saved_path)

    def get_markdown_image_filenames(self, weibo, fallback_created_at=""):
        """根据微博时间和图片数量生成 Markdown 中使用的本地图片文件名"""
        created_at = weibo.get("created_at") or fallback_created_at
        if not created_at or not weibo.get("pics"):
            return []

        try:
            time_obj = datetime.strptime(created_at, DTFORMAT)
            date_str = time_obj.strftime("%Y-%m-%d")
            time_str = time_obj.strftime("%H:%M:%S")
        except ValueError:
            return []

        pics = [pic for pic in weibo["pics"].split(",") if pic]
        local_images = weibo.get("_local_images") or {}
        file_names = []
        for i, pic_url in enumerate(pics):
            actual_name = local_images.get(pic_url)
            if actual_name:
                file_names.append(actual_name)
                continue
            base_filename = f"{date_str}_{time_str.replace(':', '-')}"
            if len(pics) > 1:
                file_names.append(f"{base_filename}_{i+1}.jpg")
            else:
                file_names.append(f"{base_filename}.jpg")
        return file_names

    def render_markdown_text(self, text, links=None, image_filenames=None, image_dir=None):
        """将正文中的占位词替换为 Markdown 链接或图片。

        ``image_dir``：图片链接使用的目录名；不传时用
        ``markdown_file_dir.original_image_dir``。转发正文需要传
        ``retweet_image_dir``，否则「查看图片」占位会指向原创图目录。
        """
        if not text:
            return "", list(links or []), list(image_filenames or [])

        remaining_links = list(links or [])
        remaining_images = list(image_filenames or [])
        img_dir = image_dir or self.markdown_file_dir.get("original_image_dir", "img")
        rendered_lines = []

        for line in text.splitlines():
            stripped = line.strip()
            replacement = line

            if stripped == "网页链接" and remaining_links:
                if self.markdown_preview.get("link_preview", 0):
                    replacement = f"![网页链接]({remaining_links.pop(0)})"
                else:
                    replacement = f"[网页链接]({remaining_links.pop(0)})"
            elif stripped == "查看图片" and remaining_images:
                if self.markdown_preview.get("inline_image_preview", 1):
                    replacement = f"![{stripped}]({img_dir}/{remaining_images.pop(0)})"
                else:
                    replacement = f"[{stripped}]({img_dir}/{remaining_images.pop(0)})"

            rendered_lines.append(replacement)

        rendered_text = "\n".join(rendered_lines).strip()
        return rendered_text, remaining_links, remaining_images

    def format_blockquote(self, text, label=None):
        """将多行文本格式化为 Markdown 引用块"""
        if not text:
            return ""

        lines = text.splitlines()
        if not lines:
            return ""

        quoted_lines = []
        first_line = lines[0]
        if label:
            quoted_lines.append(f"> {label}: {first_line}")
        else:
            quoted_lines.append(f"> {first_line}")

        for line in lines[1:]:
            quoted_lines.append(">" if not line else f"> {line}")

        return "\n".join(quoted_lines)

    def _render_media_items(self, image_filenames, live_photo_files, live_photo_prefix, live_photo_label_prefix, image_dir_name, blockquote=False, image_preview=1, live_photo_preview=1):
        """实现见 util/markdown/markdown_render.py。"""
        return markdown_render.render_media_items(self, image_filenames, live_photo_files, live_photo_prefix, live_photo_label_prefix, image_dir_name, blockquote, image_preview, live_photo_preview)

    def _archive_state_path(self):
        """状态索引路径（默认用户目录下 archive_state_<用户ID>.json）。"""
        user_id = str(self.user_config.get("user_id") or "unknown")
        user_dir = self.get_filepath("markdown")
        configured = getattr(self, "archive_state_path", "")
        return archive_state.resolve_state_path(user_dir, user_id, configured)

    def _archive_exclude_dirs(self):
        """核对/状态扫描要排除的目录：默认排除项 + 用户配置的不可见目录。"""
        return tuple(
            set(archive_index.DEFAULT_EXCLUDE_DIRS)
            | set(getattr(self, "invisible_weibo_dirs", ()) or ())
        )

    def _ensure_archive_state(self):
        """加载状态索引；首次（或索引缺失）时从现有 md 重建。

        每用户只读一次并缓存到 ``self._archive_state``——一轮内
        ``comment_worker`` / ``edit_updates`` / ``change_report`` /
        ``invisible_archive`` 共用同一份 dict。共享同一对象也避免「后写覆盖前写」
        丢字段；各模块的 save_state 保留（内容始终是全量）。
        """
        cached = getattr(self, "_archive_state", None)
        if cached is not None:
            return cached
        path = self._archive_state_path()
        state, warning = archive_state.load_state(path)
        if warning:
            self._record_write_failure("状态索引读取", path, warning)
        if not state.get("weibos"):
            user_dir = self.get_filepath("markdown")
            try:
                entries, _stats = archive_index.collect_archive_entries(
                    user_dir, None, None, self._archive_exclude_dirs()
                )
            except Exception as exc:  # noqa: BLE001
                self._record_write_failure("状态索引重建", user_dir, str(exc))
                entries = {}
            rebuilt = archive_state.empty_state(
                str(self.user_config.get("user_id") or "")
            )
            for wid, (dt, full_path) in entries.items():
                archive_state.upsert_weibo(
                    rebuilt,
                    wid,
                    created_at=dt.strftime(DTFORMAT) if dt else "",
                    md_path=os.path.relpath(full_path, user_dir),
                    touch=False,
                )
            logger.info("状态索引首次建立：%d 条微博（扫描自本地存档）", len(entries))
            state = rebuilt
        self._state_path = path
        self._archive_state = state
        return state

    def _locate_block_file(self, weibo_id, item, created_at):
        """定位某条微博所在的 md 文件：先看状态索引，再按日期推算。"""
        user_dir = self.get_filepath("markdown")
        md_path = str((item or {}).get("md_path") or "")
        if md_path:
            full = md_path if os.path.isabs(md_path) else os.path.join(user_dir, md_path)
            if os.path.isfile(full):
                rel = md_path if not os.path.isabs(md_path) else full
                return full, rel
        try:
            dt = datetime.strptime(created_at, DTFORMAT)
        except (TypeError, ValueError):
            return None, ""
        mode = self.markdown_split_by
        if mode == "day_by_month":
            rel = os.path.join(dt.strftime("%Y-%m"), dt.strftime("%Y-%m-%d") + ".md")
        elif mode == "day":
            rel = dt.strftime("%Y-%m-%d") + ".md"
        elif mode == "month":
            rel = dt.strftime("%Y-%m") + ".md"
        elif mode == "year":
            rel = dt.strftime("%Y") + ".md"
        else:
            rel = f"{self.user.get('screen_name', 'weibo')}.md"
        full = os.path.join(user_dir, rel)
        if os.path.isfile(full):
            return full, rel
        return None, ""

    def _save_edit_snapshot(
        self, weibo_id, block, version_edit_at, created_at, visibility, md_file=None
    ):
        """保存旧版快照（实现见 util/edit/edit_updates.py）。"""
        return edit_updates.save_edit_snapshot(
            self, weibo_id, block, version_edit_at, created_at, visibility, md_file
        )

    def _update_block_body(self, block, rendered_body, edit_at):
        """用最新内容重建块正文，并保留尾部（---）与评论块。"""
        marker = f"<!-- weibo_id: {block.weibo_id} -->"
        parts = ["\n", marker, "\n"]
        if edit_at:
            parts += [f"<!-- edited_at: {edit_at} -->", "\n"]
        parts += ["\n", rendered_body.rstrip("\n"), "\n\n"]
        if not block.comments:
            tail_match = re.search(r"\n(---\s*\n*)$", block.body)
            if tail_match:
                parts.append(tail_match.group(0).lstrip("\n"))
        block.body = "".join(parts)
        block.edited_at = edit_at or block.edited_at
        block.raw = markdown_blocks.rebuild_block_text(block)

    def _content_signature(self, text):
        """提取「平台语义内容」签名，用于编辑比较（忽略渲染格式差异）。

        返回 (正文纯文本, 媒体文件名元组(去扩展名), 链接 URL 元组)：
        - 忽略 `[网页链接]` / `![网页链接]` 等预览前缀差异；
        - 忽略 `.mp4` / `.mov` 等媒体扩展名差异；
        - 忽略 marker / edited_at / 评论块 / 空行 / 分隔线；
        - 只有正文文字、媒体文件集合、链接集合真正变化才算编辑。
        """
        prose = []
        media = []
        links = []
        for line in (text or "").split("\n"):
            stripped = line.strip()
            if not stripped or stripped.startswith("<!--") or stripped == "---":
                continue
            # 引用块（转发内容）去掉前缀后再识别媒体/链接行
            while stripped.startswith(">"):
                stripped = stripped[1:].strip()
            if not stripped:
                continue
            match = re.match(
                r"^!?\[(?:网页链接|视频|Live Photo[^\]]*)\]\((https?://[^)]+)\)$",
                stripped,
            )
            if match:
                links.append(match.group(1))
                continue
            match = re.match(r"^!?\[[^\]]*\]\(([^)]+)\)$", stripped)
            if match:
                media.append(os.path.splitext(match.group(1))[0])
                continue
            prose.append(stripped)
        return (
            "\n".join(prose),
            tuple(sorted(media)),
            tuple(sorted(links)),
        )

    def _normalize_block_body(self, body):
        """去掉 marker / edited_at / 尾部 --- 并归一化空白，用于「md 旧正文 vs 最新正文」比较。"""
        lines = []
        for line in (body or "").split("\n"):
            if markdown_blocks.WEIBO_ID_RE.search(line):
                continue
            if markdown_blocks.EDITED_AT_RE.search(line):
                continue
            lines.append(line)
        text = "\n".join(lines)
        text = re.sub(r"\n---\s*$", "", text.strip())
        return text.strip()

    def _fetch_edit_at(self, weibo_id):
        """补查单条详情接口，拿平台「最后编辑时间」（edit_at）。

        全量走列表接口不返回 edit_at；只有检测到「内容变化」的少量微博才调用
        本方法（通常个位数到几十条/轮）。失败/风控/接口变动返回 None，由调用方
        在 md 与报告里标注「平台未提供（详情接口变动）」。
        """
        url = f"https://m.weibo.cn/statuses/show?id={weibo_id}"
        try:
            response = self.session.get(url, headers=self.headers, timeout=10)
            if getattr(response, "status_code", 200) != 200:
                return None
            payload = response.json()
            if not isinstance(payload, dict):
                return None
            data = payload.get("data")
            if not isinstance(data, dict):
                return None
            value = str(data.get("edit_at") or "").strip()
            return value or None
        except Exception as e:  # noqa: BLE001 兜底：详情接口失败不阻断
            # 打日志区分「平台未提供 edit_at」与「详情接口失败/代码缺陷」，
            # 避免归因误导。
            logger.info(
                "补查编辑时间失败（weibo_id=%s）：%s；本次按「平台未提供」处理",
                weibo_id,
                errors.describe_exception(e),
            )
            return None

    def _apply_edit_updates(self, wrote_count):
        """检测已存档微博的编辑（实现见 util/edit/edit_updates.py）。"""
        return edit_updates.apply(self, wrote_count)

    def _render_weibo_block_body(self, w, created_at):
        """渲染单条微博块正文（实现见 util/markdown/markdown_render.py）。"""
        return markdown_render.render_block_body(self, w, created_at)

    def generate_markdown_file(self, group_key, weibo_list):
        """生成单个 markdown 文件（实现见 util/markdown/markdown_file.py）。"""
        return markdown_file.generate_markdown_file(self, group_key, weibo_list)

    def _backup_markdown(self, md_file_path):
        """写 md 前备份为 ``.bak.<YYYYMMDD>[_N]``（同一天多份编号递增）。

        备份实现见 util/core/atomic_io.py，这里只判断开关。
        """
        if not getattr(self, "markdown_write_backup", 1):
            return None
        return atomic_io.backup_file(md_file_path, logger)

    def _record_write_failure(self, stage, target, error):
        """记录一次写入/读取失败。

        失败不再只写日志：收集到 self.run_failures，由本轮「写入失败报告」
        （generate_write_failure_report）呈现，并进入本轮抓取情况报告的固定字段。
        不抛异常、不阻断主流程，只保证失败可见、可追溯。
        """
        item = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "stage": str(stage or "未知步骤"),
            "target": str(target or ""),
            "error": str(error or ""),
        }
        failures = getattr(self, "run_failures", None)
        if not isinstance(failures, list):
            failures = []
            self.run_failures = failures
        failures.append(item)
        return item

    def _run_write_step(self, name, func, *args):
        """执行一个写入/下载子步骤：单个后端失败只记日志，不阻断其他后端。

        单个后端抛异常不会中断 write_data 剩余分支；失败同时记录到
        run_failures，并写入本轮写入失败报告。
        """
        try:
            return func(*args)
        except Exception as e:  # noqa: BLE001 兜底：单点失败不阻断主流程
            programming_error = errors.is_programming_error(e)
            if programming_error:
                logger.exception(
                    "%s 出现程序性错误（疑似代码缺陷，请附堆栈反馈）: %s", name, e
                )
            else:
                logger.exception("%s 失败，已跳过该步骤，其余步骤继续: %s", name, e)
            self._record_write_failure(
                name,
                "程序性错误" if programming_error else "",
                errors.describe_exception(e),
            )
            return None

    def write_data(self, wrote_count):
        """将爬到的信息写入各后端（实现见 util/core/write_pipeline.py）。"""
        return write_pipeline.write_data(self, wrote_count)

    def _copy_to_isolated(self, archive_root, weibo_id, item, user_dir):
        """把一条已确认不可见的微博块（及媒体）复制到隔离区；成功返回 True。"""
        try:
            md_file, _rel = self._locate_block_file(weibo_id, item, item.get("created_at", ""))
            if not md_file:
                return False
            with open(md_file, "r", encoding="utf-8") as handle:
                text = handle.read()
            doc = markdown_blocks.parse_markdown(text)
            block = doc.find(weibo_id)
            if block is None:
                return False
            created = str(item.get("created_at") or "")
            day = created[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", created) else datetime.now().strftime("%Y-%m-%d")
            month_dir = os.path.join(archive_root, day[:7])
            os.makedirs(month_dir, exist_ok=True)
            target = os.path.join(month_dir, f"{day}.md")
            existing = ""
            if os.path.isfile(target):
                with open(target, "r", encoding="utf-8") as handle:
                    existing = handle.read()
            if re.search(r"weibo_id:\s*" + re.escape(weibo_id) + r"\b", existing):
                return True
            header = "" if existing else f"## {day} [已不可见] 微博存档\n\n"
            self._atomic_write_text(target, (existing.rstrip() + "\n\n" if existing else header) + block.raw.rstrip() + "\n\n---\n")
            # 媒体复制（块内相对链接）
            for match in re.finditer(r"\]\(([^)]+)\)", block.raw):
                link = match.group(1)
                if link.startswith(("http://", "https://")):
                    continue
                src = os.path.normpath(os.path.join(os.path.dirname(md_file), link))
                if not os.path.isfile(src):
                    continue
                dst = os.path.join(month_dir, link)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                try:
                    if not os.path.exists(dst):
                        shutil.copy2(src, dst)
                except OSError:
                    pass
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("隔离区复制失败 微博=%s: %s", weibo_id, e)
            return False

    def _backfill_isolated_media(self, archive_root):
        """隔离区媒体补全——对隔离区 md 引用但缺失的媒体，按 not_downloaded.txt 的 URL 补下。

        返回补下成功的文件数。只处理能在 not_downloaded 记录里找到原始 URL 的条目；
        平台已删除（404）的媒体无法补，保持缺失。
        """
        if not os.path.isdir(archive_root):
            return 0
        url_map = self._load_not_downloaded_map()
        if not url_map:
            return 0
        backfilled = 0
        failed = 0
        for dirpath, _dirs, files in os.walk(archive_root):
            for name in files:
                if not name.endswith(".md") or name.startswith("._"):
                    continue
                md_path = os.path.join(dirpath, name)
                try:
                    text = open(md_path, encoding="utf-8").read()
                except OSError:
                    continue
                for match in re.finditer(r"\]\(([^)]+)\)", text):
                    link = match.group(1)
                    if link.startswith(("http://", "https://")):
                        continue
                    target = os.path.normpath(os.path.join(dirpath, link))
                    if os.path.isfile(target):
                        continue
                    info = url_map.get(os.path.abspath(target))
                    if not info:
                        continue
                    try:
                        response = self.session.get(
                            info[1], headers=self.headers, timeout=30, stream=True
                        )
                        if getattr(response, "status_code", 200) != 200:
                            continue
                        os.makedirs(os.path.dirname(target), exist_ok=True)
                        with open(target, "wb") as handle:
                            for chunk in response.iter_content(chunk_size=65536):
                                if chunk:
                                    handle.write(chunk)
                        backfilled += 1
                    except Exception as e:  # noqa: BLE001 兜底：单文件失败不阻断
                        # 记录失败原因，避免代码缺陷被静默吞掉。
                        failed += 1
                        logger.warning(
                            "隔离区媒体补全失败：%s（%s）",
                            target,
                            errors.describe_exception(e),
                        )
        if backfilled or failed:
            logger.info(
                "隔离区媒体补全：补下 %d 个文件，失败 %d 个", backfilled, failed
            )
        return backfilled

    def _remove_isolated_copy(self, archive_root, weibo_id):
        """恢复可见时删除隔离区副本（含空文件清理）。"""
        if not os.path.isdir(archive_root):
            return
        pattern = re.compile(r"weibo_id:\s*" + re.escape(weibo_id) + r"\b")
        for dirpath, _dirs, files in os.walk(archive_root):
            for name in files:
                if not name.endswith(".md") or name.startswith("._"):
                    continue
                path = os.path.join(dirpath, name)
                try:
                    text = open(path, encoding="utf-8").read()
                except OSError:
                    continue
                if not pattern.search(text):
                    continue
                doc = markdown_blocks.parse_markdown(text)
                doc.blocks = [b for b in doc.blocks if b.weibo_id != weibo_id]
                if doc.blocks:
                    self._atomic_write_text(path, doc.render())
                else:
                    try:
                        os.remove(path)
                    except OSError:
                        pass

    def _review_invisible_archive(self):
        """隔离区审核（实现见 util/report/invisible_archive.py）。"""
        return invisible_archive.review_invisible_archive(self)

    def _generate_change_report(self):
        """生成本轮变化报告（实现见 util/report/change_report.py）。"""
        return change_report.generate(self)

    def get_pages(self):
        """get_pages（实现见 util/fetch/fetch_pages.py）。"""
        return fetch_pages.get_pages(self)

    def _parse_user_list_since_date(self, value):
        """解析用户列表第三列的 since_date；无法识别返回 None。

        支持 YYYY-MM-DD、YYYY-MM-DDTHH:MM:SS、整数（N 天前）。
        """
        if not isinstance(value, str):
            return None
        value = value.strip()
        if not value:
            return None
        if self.is_datetime(value):
            return value
        if self.is_date(value):
            return "{}T00:00:00".format(value)
        if value.isdigit():
            return (date.today() - timedelta(int(value))).strftime(DTFORMAT)
        return None

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
            for line_no, line in enumerate(lines, 1):
                # split() 按任意空白切分：兼容多空格/制表符对齐
                info = line.strip().split()
                if len(info) > 0 and info[0].isdigit():
                    user_config = {}
                    user_config["user_id"] = info[0]
                    # 第三列是用户自己的 since_date；不是日期则回退全局配置。
                    # 支持「ID 昵称 日期 [query_list]」四列格式。
                    parsed_since = (
                        self._parse_user_list_since_date(info[2])
                        if len(info) >= 3
                        else None
                    )
                    if parsed_since:
                        user_config["since_date"] = parsed_since
                        user_config["since_date_source"] = "user_list"
                        logger.info(f"用户 {user_config['user_id']} 使用文件中的起始时间: {user_config['since_date']}")
                    else:
                        user_config["since_date"] = self.since_date
                        user_config["since_date_source"] = "config"
                        if len(info) >= 3 and str(info[2]).strip():
                            logger.warning(
                                "用户列表第 %d 行第三列不是日期（%s），该用户改用全局 since_date",
                                line_no,
                                info[2],
                            )
                        else:
                            logger.info(f"用户 {user_config['user_id']} 使用配置文件的起始时间: {user_config['since_date']}")
                    # end_date 统一使用全局配置
                    user_config["end_date"] = self.end_date
                    # 第四个字段为 query_list
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
        # 本轮真正新写入 Markdown 的微博：weibo_id -> md 路径，供评论回填使用。
        # 注意：这里不重置，由 start() 在每个用户开始时初始化一次——同一用户的
        # 多个关键词 query 循环必须共享它，否则只有最后一个关键词的微博会抓评论
        # 。
        if getattr(self, "new_written_weibo", None) is None:
            self.new_written_weibo = {}

    def save_cookies_to_file(self):
        """将 Session 中最新的 Cookie 写回文件"""
        if not self.cookie_file_path:
            return

        try:
            # 将 requests.cookies.RequestsCookieJar 转换为字符串格式 "key=value; key2=value2"
            cookie_list = []
            for cookie in self.session.cookies:
                cookie_list.append(f"{cookie.name}={cookie.value}")

            cookie_string = "; ".join(cookie_list)

            # 原子写：避免进程中断留下半截 cookie 文件（下一个进程读不到有效 cookie）
            atomic_io.atomic_write_text(self.cookie_file_path, cookie_string)

            logger.info(f"最新的 Cookie 已写回文件: {self.cookie_file_path}")
        except Exception as e:  # noqa: BLE001 兜底：单点失败不阻断主流程
            logger.error(f"保存 Cookie 到文件失败: {e}")

    def _output_root_dir(self):
        """数据输出目录的绝对路径（主爬与回填共用，用于并发锁）。"""
        return file_lock.resolve_output_dir(_BASE_DIR, self.output_directory)

    def _acquire_run_lock(self):
        """获取「抓取/回填互斥锁」；被其他进程占用时返回 None（本轮跳过）。

        主爬的 markdown 写入是整文件覆盖写，评论回填是读全文再原子替换；
        两者并发会互相覆盖（评论块丢失 / 新微博丢失），因此用同一把锁互斥。
        """
        path = file_lock.lock_path_for_output(self._output_root_dir())
        lock = file_lock.FileLock(path)
        try:
            lock.acquire()
        except file_lock.LockBusy as exc:
            logger.warning(
                "已有微博抓取或评论回填进程在运行（%s）；为避免同时改写存档，本轮跳过",
                exc,
            )
            return None
        logger.info("已获取抓取/回填互斥锁: %s", path)
        return lock

    def _reset_run_state(self, user_config):
        """每个用户开始前重置存档核对/统计状态（原 start 内联逻辑）。"""
        self.crawl_success = {
            "attempted": False,
            "success": False,
            "reason": "未开始",
        }
        self.archived_ids_before = None
        self.archived_entries_before = None
        self.archive_scan_stats = None
        self.crawl_summary_baseline_note = ""
        self.check_start_dt = None
        self.check_end_dt = None
        self.crawled_ids_this_run = set()
        self.known_invisible_ids = set()
        # 抓取阶段是否已加载过「已确认不可见」清单；写盘后核对成功时直接复用，
        # 未加载（如测试/脚本直接调报告）时回退重新扫描。
        self._known_invisible_loaded = False
        # 加载失败时置位；核对时回退重算，避免用空集做核对。
        self._known_invisible_load_failed = False
        # not_downloaded.txt 映射按用户缓存（媒体报告与隔离区补各用一次）。
        self._not_downloaded_map_cache = None
        # 状态索引按用户缓存一次；换用户必须清空，避免串数据。
        self._archive_state = None
        self.skipped_invisible_count = 0
        self.filtered_retweet_count = 0
        self.media_failure_count = 0
        self.run_failures = []
        self.edit_changes = []
        self.visibility_changes = []
        # 评论回填目标按「用户」重置（不是按 query）：多关键词 query 循环
        # 共用同一份 new_written_weibo
        self.new_written_weibo = {}
        self._edit_captured_ids = set()

    def _process_user(self, user_config):
        """单个用户的抓取 + 评论 + 报告流程（原 start 内联逻辑）。"""
        # query 列表为空时等价于单次「无关键词」抓取（原两分支逐行重复）
        for query in user_config["query_list"] or [""]:
            self.query = query
            self.initialize_info(user_config)
            self.get_pages()
            # 累积本轮爬到的 ID（query 模式仍记录，便于排查）
            self.crawled_ids_this_run.update(
                str(w.get("id")) for w in self.weibo if w.get("id")
            )

        # 兜底：非 query 模式下丢弃所有不属于目标账号的微博，防止推荐/广告卡片
        # 绕过 fetch_pages 的作者过滤被写入存档。
        is_query_mode = bool(user_config.get("query_list"))
        if not is_query_mode and self.weibo and self.user:
            target_uid = str(self.user.get("id") or "")
            if target_uid:
                foreign = [
                    w for w in self.weibo
                    if str(w.get("user_id") or "") != target_uid
                ]
                if foreign:
                    foreign_ids = {str(w.get("id")) for w in foreign}
                    self.weibo = [
                        w for w in self.weibo
                        if str(w.get("id")) not in foreign_ids
                    ]
                    self.weibo_id_list = [
                        wid for wid in self.weibo_id_list
                        if str(wid) not in foreign_ids
                    ]
                    self.crawled_ids_this_run.difference_update(foreign_ids)
                    logger.warning(
                        "兜底过滤 %d 条非本人微博（疑似推荐/广告卡片）: %s",
                        len(foreign),
                        [w.get("id") for w in foreign][:10],
                    )

        # 评论写入 Markdown：对本轮新写入的微博抓评论（含二级回复）并回填对应微博块
        self.write_markdown_comments_for_new_weibo()

        # 当前用户所有微博和评论抓取完毕后，再导出该用户的评论 CSV
        self.export_comments_to_csv_for_current_user()

        # 生成存档核对报告（本轮未抓到的已存档微博 + 已抓到但未保存到本地的微博）
        self.generate_archive_check_report()
        # 生成用户目录下的「已删除微博.txt」（本轮确认已删除的微博ID+日期）
        self.generate_deleted_weibos_report()
        # 生成本轮抓取情况报告（抓到多少、其中已抓取过多少、新抓到多少、新微博 ID/日期）
        self.generate_crawl_summary_report()
        # 生成媒体完整性报告（markdown 引用的本地媒体是否存在/为空/明显损坏）
        self.generate_media_check_report()
        # 生成本轮写入失败报告（后端写盘失败、Markdown 读取失败等；无失败不生成）
        self.generate_write_failure_report()
        # 隔离区审核（全量轮判定不可见→复制/恢复）
        self._review_invisible_archive()
        # 生成本轮变化报告（编辑/可见性/疑似不可见/恢复可见）
        self._generate_change_report()

        logger.info("信息抓取完毕")
        logger.info("*" * 100)
        is_query_mode = bool(user_config.get("query_list"))
        if self.user_config_file_path and self.user:
            # 普通（个人时间线）运行：只有本轮抓取顺利完成才推进上次成功抓取时间；
            # query（关键词）模式页数天然少于 statuses_count 推算值，不按检查范围门控。
            if self.start_date and (is_query_mode or self.crawl_success.get("success")):
                self.update_user_config_file(self.user_config_file_path)
            else:
                logger.warning(
                    "本轮抓取未顺利完成（%s），不推进用户列表上次成功抓取时间，下一轮将重试",
                    self.crawl_success.get("reason", "未知"),
                )

    def start(self):
        """运行爬虫（与评论回填互斥，避免并发改写同一批 markdown）。

        逐用户循环骨架见 util/core/run_loop.py；这里只做锁与异常处理。
        """
        lock = self._acquire_run_lock()
        if lock is None:
            return
        try:
            run_loop.run_users(
                self.user_config_list,
                before_each=self._reset_run_state,
                process=self._process_user,
            )
        except Exception as e:  # noqa: BLE001 兜底：单点失败不阻断主流程
            if errors.is_programming_error(e):
                # 代码缺陷（漏 import、属性不存在等）：明确标记并进失败报告。
                logger.exception(
                    "本轮抓取因程序性错误中断（疑似代码缺陷，请附堆栈反馈）: %s", e
                )
                self._record_write_failure(
                    "本轮运行", "程序性错误", errors.describe_exception(e)
                )
            else:
                logger.exception("本轮抓取异常中断: %s", e)
        finally:
            # 即使某个账号异常中断，也生成已完成的跨账号汇总（不丢数据）
            try:
                self.generate_crawl_summary_total_report()
            except Exception as exc:  # noqa: BLE001
                logger.warning("生成抓取汇总报告失败: %s", exc)
            self.save_cookies_to_file()
            # 释放「抓取/回填互斥锁」，不把它带出本轮运行（进程退出也会自动释放）
            lock.release()


def handle_config_renaming(config, oldName, newName):
    if oldName in config and newName not in config:
        config[newName] = config[oldName]
        del config[oldName]

def get_config():
    """获取配置文件信息（支持JSON5格式）

    配置文件优先级：
    1. ``config/config.json``（用户已从模板复制并重命名；兼容根目录旧位置 ``config.json``）
    2. ``config/config.json.example``（版本控制中的默认配置模板；兼容根目录旧位置）
    """
    base_dir = _BASE_DIR + os.sep
    # 配置在 config/ 下（根目录旧位置仅兼容）；与 --update-config 共用
    # config_paths.config_files（避免两套规则漂移）。
    config_path, example_path = config_paths.config_files(base_dir)

    # 优先使用 config.json，若不存在则使用 config.json.example
    if not os.path.isfile(config_path):
        if os.path.isfile(example_path):
            logger.warning(
                "未找到 config.json。正在使用 config.json.example（模板配置）。\n"
                "建议把模板复制为 config.json 并填写自己的 Cookie 等配置：\n"
                "  cp config/config.json.example config/config.json"
            )
            config_path = example_path
        else:
            logger.warning(
                "当前路径：%s 不存在配置文件 config.json 和 config.json.example",
                base_dir,
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

            # 配置自动适配：以 config.json.example 为模板，补齐缺失的新配置项（不自动写回文件）
            if config_path != example_path and os.path.isfile(example_path):
                try:
                    from util.config import config_migrate

                    template = config_migrate.load_config_file(example_path)
                    result = config_migrate.migrate_config(
                        config, template, config_migrate.DEFAULT_RENAME_MAP
                    )
                    if result.added_keys or result.renamed_keys:
                        logger.info(
                            "配置已自动适配：%s",
                            config_migrate.format_summary(result).replace("\n", "；"),
                        )
                        logger.info(
                            "请手动运行以下命令把适配结果写回配置文件（自动备份，尽量保留注释）："
                            "python weibo.py --update-config"
                        )
                    if result.unknown_keys:
                        logger.warning(
                            "配置中存在模板没有的配置项（已保留，不会删除）：%s",
                            ", ".join(result.unknown_keys),
                        )
                    config = result.merged
                except Exception as e:  # noqa: BLE001 兜底：单点失败不阻断主流程
                    logger.warning("配置自动适配失败，继续使用原配置：%s", e)

            return config
    except Exception as e:  # noqa: BLE001 兜底：单点失败不阻断主流程
        logger.error(f"读取配置文件失败: {e}")
        logger.error("请确保config.json存在且格式正确")
        sys.exit()


def _run_config_update(write: bool) -> int:
    """实现 --check-config / --update-config。

    - 预览模式（write=False）：只打印差异，不修改文件；
    - 写回模式（write=True）：先备份，再优先按原文件格式写回（保留注释与
      排版）；无法安全保留原格式时自动回退为规范化 JSON 写入并给出提示。
    """
    base_dir = _BASE_DIR + os.sep
    # 优先 config/（兼容根目录旧位置；与 get_config 共用同一解析）
    user_path, template_path = config_paths.config_files(base_dir)
    try:
        from util.config import config_migrate
    except Exception as e:  # noqa: BLE001 兜底：单点失败不阻断主流程
        logger.error("配置迁移模块不可用: %s", e)
        return 1

    if not os.path.isfile(user_path):
        logger.error(
            "未找到 config.json；请先执行：cp config/config.json.example config/config.json，并填写 cookie 等配置"
        )
        return 1
    if not os.path.isfile(template_path):
        logger.error("未找到 config/config.json.example 模板")
        return 1

    try:
        user_config = config_migrate.load_config_file(user_path)
        template = config_migrate.load_config_file(template_path)
        result = config_migrate.migrate_config(
            user_config, template, config_migrate.DEFAULT_RENAME_MAP
        )
        print(config_migrate.format_summary(result))
        if write:
            with open(user_path, "r", encoding="utf-8-sig") as user_file:
                original_text = user_file.read()
            with open(template_path, "r", encoding="utf-8-sig") as template_file:
                template_text = template_file.read()
            backup_path, preserved = config_migrate.write_config_text_with_backup(
                user_path,
                result,
                original_text=original_text,
                template_text=template_text,
            )
            print(f"已写回配置文件：{user_path}")
            print(f"原文件备份：{backup_path}")
            if preserved:
                print("已保留原文件的注释与排版。")
            else:
                print(
                    "原文件格式无法安全保留注释，已改用规范化 JSON 写入（备份中保留原文件）。"
                )
        else:
            print(
                "（预览模式：未修改文件；执行 --update-config 可写回，自动备份，"
                "并尽量保留原文件的注释与排版。）"
            )
        return 0
    except Exception as e:  # noqa: BLE001 兜底：单点失败不阻断主流程
        logger.error("配置更新失败: %s", e)
        return 1


def main():
    import argparse

    parser = argparse.ArgumentParser(description="微博爬虫")
    parser.add_argument(
        "--check-config", action="store_true", help="预览配置差异（不写文件）"
    )
    parser.add_argument(
        "--update-config", action="store_true", help="备份并写回配置更新"
    )
    args, _ = parser.parse_known_args()
    if args.check_config or args.update_config:
        sys.exit(_run_config_update(args.update_config))

    try:
        config = get_config()
        wb = Weibo(config)
        wb.start()  # 爬取微博信息
        if const.NOTIFY["NOTIFY"]:
            push_deer("更新了一次微博")
    except Exception as e:  # noqa: BLE001 兜底：单点失败不阻断主流程
        if const.NOTIFY["NOTIFY"]:
            push_deer("weibo-crawler运行出错，错误为{}".format(e))
        logger.exception("运行出错: %s", e)


if __name__ == "__main__":
    main()
