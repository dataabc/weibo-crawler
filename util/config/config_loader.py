"""配置加载与校验。

``apply`` 等价原 ``Weibo.__init__`` 主体（读取配置、初始化属性、请求头、
用户列表等）；``validate`` 等价原 ``Weibo.validate_config``（配置类型/取值
校验，错误时 sys.exit）。搬运自 weibo.py，行为保持不变。
"""

from __future__ import annotations

import logging
import os
import random
import re
import sys
import time
from datetime import date, datetime, timedelta
from time import sleep

import const
import requests

from util.config import config_paths
from util.config import config_schema
from util.core import archive_index
from util.core import project
from util.core.time_format import DTFORMAT
from util.http import http_client
from util.misc.llm_analyzer import LLMAnalyzer

logger = logging.getLogger("weibo")


def _make_http_adapter():
    ""
    from weibo import HTTPAdapter as _HTTPAdapter

    return _HTTPAdapter(max_retries=5)


def _resolve_cookie(env_value, cookie_config, project_root):
    """延迟导入 weibo.resolve_cookie（避免 weibo ↔ config_loader 循环导入）。"""
    from weibo import resolve_cookie

    return resolve_cookie(env_value, cookie_config, project_root)


def apply(wb, config) -> None:
    """Weibo类初始化"""
    wb.validate_config(config)
    # 关键配置键的软校验（只记警告，不阻断；未知键忽略）
    for _issue in config_schema.validate_config(config):
        logger.warning("配置校验: %s", _issue)
    wb.only_crawl_original = config["only_crawl_original"]  # 取值范围为0、1,程序默认值为0,代表要爬取用户的全部微博,1代表只爬取用户的原创微博
    wb.remove_html_tag = config[
        "remove_html_tag"
    ]  # 取值范围为0、1, 0代表不移除微博中的html tag, 1代表移除
    since_date = config["since_date"]
    # since_date 若为整数，则取该天数之前的日期；若为 yyyy-mm-dd，则增加时间
    if isinstance(since_date, int):
        since_date = date.today() - timedelta(since_date)
        since_date = since_date.strftime(DTFORMAT)
    elif wb.is_date(since_date):
        since_date = "{}T00:00:00".format(since_date)
    elif wb.is_datetime(since_date):
        pass
    else:
        logger.error("since_date 格式不正确，请确认配置是否正确")
        sys.exit()
    wb.since_date = since_date  # 起始时间：爬取发布日期从该值到现在的微博，格式 YYYY-MM-DDTHH:MM:SS
    end_date = config.get("end_date", "")
    # end_date 为空字符串时不限制截止时间
    if end_date:
        if isinstance(end_date, int):
            end_date = date.today() - timedelta(end_date)
            end_date = end_date.strftime(DTFORMAT)
        elif wb.is_date(end_date):
            end_date = "{}T23:59:59".format(end_date)
        elif wb.is_datetime(end_date):
            pass
        else:
            logger.error("end_date 格式不正确，请确认配置是否正确")
            sys.exit()
    wb.end_date = end_date  # 截止时间，为空则不限制
    wb.start_page = config.get("start_page", 1)  # 开始爬的页，如果中途被限制而结束可以用此定义开始页码
    wb.write_mode = config[
        "write_mode"
    ]
    # 只读模式：全量核对等「只读」脚本用，不写 users.csv/数据库、不创建用户目录
    wb.readonly_mode = bool(config.get("_readonly_mode", False))  # 结果信息保存类型，为list形式，可包含csv、mongo和mysql三种类型
    wb.markdown_split_by = config.get("markdown_split_by", "day") # markdown文件分割方式，day/day_by_month/month/year/all
    wb.markdown_sort_order = config.get("markdown_sort_order", "new_to_old") # markdown中微博排序方式，new_to_old/old_to_new
    wb.markdown_image_order = config.get("markdown_image_order", "live_photo_first") # markdown中图片与Live Photo的排列顺序，live_photo_first/image_first_interleaved
    # 写 md 前备份
    wb.markdown_write_backup = config.get("markdown_write_backup", 1)
    # 编辑快照目录与状态索引路径（空 = 默认用户目录）
    wb.edit_snapshot_dir = config.get("edit_snapshot_dir", "编辑历史")
    wb.archive_state_path = config.get("archive_state_path", "")
    wb.edit_changes = []
    # markdown 媒体预览配置，默认值
    default_preview = {
        "link_preview": 0,
        "original_video_preview": 0,
        "retweet_video_preview": 0,
        "original_image_preview": 1,
        "retweet_image_preview": 1,
        "original_live_photo_preview": 0,
        "retweet_live_photo_preview": 0,
        "inline_image_preview": 1,
    }
    md_preview = config.get("markdown_preview", {})
    # 合并用户配置，缺失的 key 使用默认值
    wb.markdown_preview = {**default_preview, **md_preview} if isinstance(md_preview, dict) else default_preview
    # 是否把「位置」类网页链接（weibo.com/p/100101…）写进 Markdown（默认不写）
    wb.markdown_show_location_link = int(config.get("markdown_show_location_link", 0) or 0)

    # markdown 中各类媒体文件的相对路径目录名
    default_md_dir = {
        "original_image_dir": "img",
        "retweet_image_dir": "img",
        "original_video_dir": "原创微博视频",
        "retweet_video_dir": "转发微博视频",
        "original_live_photo_dir": "原创微博Live Photo视频",
        "retweet_live_photo_dir": "转发微博Live Photo视频",
        "comment_image_dir": "评论图片",
    }
    md_file_dir = config.get("markdown_file_dir", {})
    wb.markdown_file_dir = {**default_md_dir, **md_file_dir} if isinstance(md_file_dir, dict) else default_md_dir

    # 下载后本地存储的文件夹名称
    default_dl_folder = {
        "original_image_folder": "原创微博图片",
        "retweet_image_folder": "转发微博图片",
        "original_video_folder": "原创微博视频",
        "retweet_video_folder": "转发微博视频",
        "original_live_photo_folder": "原创微博Live Photo视频",
        "retweet_live_photo_folder": "转发微博Live Photo视频",
        "comment_image_folder": "评论图片",
    }
    dl_folder = config.get("download_folder_name", {})
    wb.download_folder_name = {**default_dl_folder, **dl_folder} if isinstance(dl_folder, dict) else default_dl_folder
    # 评论图片目录：未显式配置 markdown_file_dir.comment_image_dir 时，
    # 回退使用 download_folder_name.comment_image_folder。
    if (
        isinstance(md_file_dir, dict)
        and "comment_image_dir" not in md_file_dir
        and isinstance(dl_folder, dict)
        and dl_folder.get("comment_image_folder")
    ):
        wb.markdown_file_dir["comment_image_dir"] = str(
            dl_folder["comment_image_folder"]
        )
    wb.original_pic_download = config[
        "original_pic_download"
    ]  # 取值范围为0、1, 0代表不下载原创微博图片,1代表下载
    wb.retweet_pic_download = config[
        "retweet_pic_download"
    ]  # 取值范围为0、1, 0代表不下载转发微博图片,1代表下载
    wb.original_video_download = config[
        "original_video_download"
    ]  # 取值范围为0、1, 0代表不下载原创微博视频,1代表下载
    wb.retweet_video_download = config[
        "retweet_video_download"
    ]  # 取值范围为0、1, 0代表不下载转发微博视频,1代表下载

    # 新增Live Photo视频下载配置
    wb.original_live_photo_download = config.get("original_live_photo_download", 0)
    wb.retweet_live_photo_download = config.get("retweet_live_photo_download", 0)

    wb.download_comment = config["download_comment"]  # 1代表下载评论,0代表不下载
    wb.comment_max_download_count = config[
        "comment_max_download_count"
    ]  # 如果设置了下评论，每条微博评论数会限制在这个值内
    wb.comment_pic_download = config.get("comment_pic_download", 0)  # 1代表下载评论图片,0代表不下载
    # 微博表情图片存储目录（评论表情下载到这里，md 内用相对内链引用）
    wb.weibo_emoji_dir = config.get("weibo_emoji_dir", "weibo_data/emoji")
    # 评论表情在 Markdown 里的显示高度（像素）；0 = 保持原图尺寸
    wb.comment_emoji_inline_size = int(
        config.get("comment_emoji_inline_size", 0) or 0
    )
    # 单条微博评论抓取的请求预算上限
    wb.comment_max_requests = int(config.get("comment_max_requests", 300) or 300)
    # 评论（含二级回复）相关配置
    wb.comment_reply_max_download_count = config.get(
        "comment_reply_max_download_count", 0
    )  # 每条一级评论最多抓多少条二级回复；0 = 不限制
    wb.comment_show_like_count = config.get("comment_show_like_count", 0)  # 是否显示点赞数
    wb.comment_show_reply_target = config.get("comment_show_reply_target", 1)  # 是否显示「回复 某人」
    wb.comment_emoji_replace = config.get(
        "comment_emoji_replace", 1
    )  # 0 = 保留表情外链图片；1 = 不显示图片，优先表情名、回退外链
    wb.comment_include_latest_stream = config.get(
        "comment_include_latest_stream", 0
    )  # 是否用旧接口补充「最新流」一级评论
    wb.comment_request_delay_min = config.get("comment_request_delay_min", 1.0)  # 评论请求间隔下限（秒）
    wb.comment_request_delay_max = config.get("comment_request_delay_max", 2.0)  # 评论请求间隔上限（秒）
    wb.comment_summary_enabled = config.get("weibo_comment_summary", 0)  # 是否生成评论下载情况报告
    # 评论刷新范围（all=每轮刷新本轮抓到的所有已存档微博；recent=只刷最近 N 天；off=只刷新微博）
    wb.comment_refresh_scope = config.get("comment_refresh_scope", "all")
    wb.comment_refresh_recent_days = int(
        config.get("comment_refresh_recent_days", 30) or 30
    )
    wb.comment_refresh_limit = int(config.get("comment_refresh_limit", 300) or 300)
    # 变化报告（默认开启）与疑似不可见判定轮数
    wb.weibo_change_report = config.get("weibo_change_report", 1)
    # 消费端（util/report/change_report.py）读取的是 wb.change_report_dir。
    wb.change_report_dir = config.get("weibo_change_report_dir", "report")
    wb.invisible_confirm_rounds = int(
        config.get("invisible_confirm_rounds", 2) or 2
    )
    # 隔离区（已不可见）目录；只复制不搬移，恢复可见时删除副本
    wb.invisible_archive_dir = config.get("invisible_archive_dir", "已不可见")
    wb.invisible_archive_enabled = config.get("invisible_archive_enabled", 1)
    wb.invisible_review = {}
    wb.comment_summary_dir = config.get("weibo_comment_summary_dir", "report")  # 报告目录
    # 按微博账号配置的评论者白名单：{微博账号ID或昵称: [评论者ID或昵称, ...]}
    # 账号未配置或数组为空 = 不限制，保留全部评论。
    # 优先读外部 txt（comment_user_filter_file），文件为空时用下面的内联配置。
    wb.comment_user_filter_file = config.get(
        "comment_user_filter_file", "config/comment_user_filter.txt"
    )  # 相对路径解析见 config_paths.resolve_config_file（优先 config/）
    raw_user_filter = config.get("comment_user_filter", {})
    wb.comment_user_filter = raw_user_filter if isinstance(raw_user_filter, dict) else {}
    wb.comment_markdown_backup = config.get("comment_markdown_backup", 0)  # 写评论块前是否备份.md（默认不备份）
    wb.download_repost = config["download_repost"]  # 1代表下载转发,0代表不下载
    wb.repost_max_download_count = config[
        "repost_max_download_count"
    ]  # 如果设置了下转发，每条微博转发数会限制在这个值内
    wb.user_id_as_folder_name = config.get(
        "user_id_as_folder_name", 0
    )  # 结果目录名，取值为0或1，决定结果文件存储在用户昵称文件夹里还是用户id文件夹里
    wb.write_time_in_exif = config.get(
        "write_time_in_exif", 0
    )  # 是否开启微博时间写入EXIF，取值范围为0、1, 0代表不开启, 1代表开启
    wb.change_file_time = config.get(
        "change_file_time", 0
    )  # 是否修改文件时间，取值范围为0、1, 0代表不开启, 1代表开启
    wb.output_directory = config.get(
        "output_directory", "weibo"
    )  # 输出目录配置，默认为"weibo"

    # 是否生成「本轮存档核对报告」：报告本地有、本轮平台未返回的微博（可能已删除/不可见）
    # 以及本轮已爬到但未保存到本地的微博；不再生成旧的「本次未重爬」报告
    wb.archive_check_enabled = config.get("weibo_archive_check", 0)
    # 抓取结束后在用户目录生成「已删除微博.txt」（确认已删除的微博ID+日期）
    wb.weibo_deleted_report = int(config.get("weibo_deleted_report", 1) or 0)
    # 是否生成「本轮抓取情况报告」：每次抓取顺利完成时，记录本轮抓到多少条、
    # 其中已抓取过多少条、新抓到多少条，以及新抓到的微博 ID/日期
    wb.crawl_summary_enabled = config.get("weibo_crawl_summary", 0)
    # 是否写 not_downloaded.txt 下载失败记录（0 = 不写，也不清理旧记录）
    wb.not_downloaded_record = config.get("not_downloaded_record", 0)
    # 报告输出目录：相对 output_directory；也支持绝对路径。
    # 默认 "report" → <output_directory>/report/
    wb.archive_check_dir = config.get("weibo_archive_check_dir", "report")
    wb.crawl_summary_dir = config.get("weibo_crawl_summary_dir", "report")
    # 跨账号「本轮全量实测」汇总行
    wb.crawl_summary_rows = []
    wb.archive_check_full_dir = config.get(
        "weibo_archive_check_full_dir", "report"
    )
    # 下载失败记录位置：空或 "media" = 跟随媒体文件所在目录（默认）；
    # "report" = <output_directory>/report/；其他值 = 自定义目录（相对 output_directory 或绝对路径）
    wb.not_downloaded_record_dir = config.get("not_downloaded_record_dir", "")
    # 是否生成「媒体完整性报告」：扫描 markdown 引用的本地媒体，检查缺失/空文件/明显损坏
    wb.media_check_enabled = config.get("weibo_media_check", 0)
    wb.media_check_dir = config.get("weibo_media_check_dir", "report")
    # 不计入活跃存档的目录（默认「已不可见」：用户已核实不可见的微博档案；旧目录名「已删除」仍兼容）
    exclude_dirs = config.get(
        "invisible_weibo_dirs",
        list(archive_index.DEFAULT_EXCLUDE_DIRS),
    )
    # 与默认排除项（已不可见/已删除）合并：避免只配「已不可见」时
    # 人工「已删除」目录被当成活跃存档扫描（核对/媒体报告/状态索引）。
    wb.invisible_weibo_dirs = tuple(
        set(exclude_dirs) | set(archive_index.DEFAULT_EXCLUDE_DIRS)
    )

    # Cookie 支持：优先级 ① 环境变量 WEIBO_COOKIE ② config.cookie
    # （config.cookie 可以是字面 Cookie 串，或以 .txt 结尾的文件路径）。
    # 环境变量优先；文件路径按项目根解析，不受当前工作目录影响。
    cookie_string, wb.cookie_file_path = _resolve_cookie(
        os.environ.get("WEIBO_COOKIE"),
        config.get("cookie"),
        project.project_root(),
    )

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

    wb.headers = {
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
    wb.mysql_config = config.get("mysql_config")  # MySQL数据库连接配置，可以不填
    wb.mongodb_URI = config.get("mongodb_URI")  # MongoDB数据库连接字符串，可以不填
    wb.post_config = config.get("post_config")  # post_config，可以不填
    wb.page_weibo_count = config.get("page_weibo_count")  # page_weibo_count，爬取一页的微博数，默认10页
    wb.sqlite_db_path = config.get("sqlite_db_path", "weibodata.db")  # SQLite数据库路径

    # 初始化 LLM 分析器
    wb.llm_analyzer = LLMAnalyzer(config) if config.get("llm_config") else None

    user_id_list = config["user_id_list"]
    requests_session = requests.Session()
    requests_session.cookies.update(core_cookies)

    wb.session = requests_session
    try:
        # 请求只带 SUB
        # 服务器下发适配 m.weibo.cn 的新指纹
        wb.session.get("https://m.weibo.cn", headers=wb.headers, timeout=10)
        logger.info("Session 预热成功，服务器已下发最新指纹。")

    except Exception as e:  # noqa: BLE001 兜底：失败不阻断
        #请求失败时，启用备份
        logger.warning(f"Session 预热失败 ({e})，正在启用备份 Cookie...")
        wb.session.cookies.update(backup_cookies) # 把旧指纹装进去救急

    adapter = _make_http_adapter()
    wb.session.mount('http://', adapter)
    wb.session.mount('https://', adapter)
    # 避免卡住
    if isinstance(user_id_list, list):
        random.shuffle(user_id_list)

    query_list = config.get("query_list") or []
    if isinstance(query_list, str):
        query_list = query_list.split(",")
    wb.query_list = query_list
    if not isinstance(user_id_list, list):
        user_id_list = config_paths.resolve_config_file(
            project.project_root(), user_id_list
        )
        wb.user_config_file_path = user_id_list  # 用户配置文件路径
        user_config_list = wb.get_user_config_list(user_id_list)
    else:
        wb.user_config_file_path = ""
        user_config_list = [
            {
                "user_id": user_id,
                "since_date": wb.since_date,
                "since_date_source": "config",
                "end_date": wb.end_date,
                "query_list": query_list,
            }
            for user_id in user_id_list
        ]

    wb.user_config_list = user_config_list  # 要爬取的微博用户的user_config列表
    wb.user_config = {}  # 用户配置,包含用户id和since_date
    wb.start_date = ""  # 获取用户第一条微博时的日期
    wb.query = ""
    wb.user = {}  # 存储目标微博用户信息
    wb.got_count = 0  # 存储爬取到的微博数
    wb.weibo = []  # 存储爬取到的所有微博信息
    wb.weibo_id_list = []  # 存储爬取到的所有微博id
    wb.long_sleep_count_before_each_user = 0 #每个用户前的长时间sleep避免被ban
    wb.store_binary_in_sqlite = config.get("store_binary_in_sqlite", 0)

    # 防封禁配置初始化
    # 反封禁配置：与默认值合并，避免用户只写 {"enabled": true} 时
    # get_pages 的横幅与各限速方法直接下标 KeyError。
    default_anti_ban = {
        "enabled": False,
        "max_weibo_per_session": 500,
        "batch_size": 50,
        "batch_delay": 30,
        "request_delay_min": 8,
        "request_delay_max": 15,
        "max_session_time": 600,
        "max_api_errors": 5,
        "rest_time_min": 180,
        "random_rest_probability": 0.01,
        "user_agents": [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0"
        ],
        "accept_languages": ["zh-CN,zh;q=0.9,en;q=0.8"],
        "referer_list": ["https://m.weibo.cn/", "https://weibo.com/"],
    }
    raw_anti_ban = config.get("anti_ban_config")
    if not isinstance(raw_anti_ban, dict):
        raw_anti_ban = {}
    wb.anti_ban_config = {**default_anti_ban, **raw_anti_ban}
    wb.anti_ban_enabled = bool(wb.anti_ban_config.get("enabled", False))

    # 爬取状态跟踪
    wb.crawl_stats = {
        "weibo_count": 0,      # 已爬取微博数
        "request_count": 0,    # 已发送请求数
        "api_errors": 0,       # API错误数
        "start_time": None,    # 开始时间
        "batch_count": 0,      # 当前批次计数
        "last_batch_time": None # 上次批次时间
    }

    # 存档核对状态（每个用户开始时重置）
    wb.crawl_success = {
        "attempted": False,
        "success": False,
        "reason": "未开始",
    }
    wb.archived_ids_before = None      # 抓取前核对范围内的存档 ID
    wb.archived_entries_before = None  # 抓取前核对范围内的存档条目 {id: (日期, 路径)}
    wb.archive_scan_stats = None      # 抓取前扫描统计
    wb.crawl_summary_baseline_note = ""  # 抓取前存档基准不可用的原因
    wb.check_start_dt = None        # 检查范围起点（用户实际 since_date）
    wb.check_end_dt = None          # 检查范围终点（本轮抓取开始时刻）
    wb.crawled_ids_this_run = set()  # 本用户本轮爬到/枚举到的全部 ID
    wb.known_invisible_ids = set()          # 已确认删除、隐藏或人工忽略的 ID：不写入、不补回

    # 本轮抓取情况报告的计数（每个用户开始时重置）
    wb.skipped_invisible_count = 0   # 因已确认不可见而跳过写入的条数
    wb.filtered_retweet_count = 0    # 因只抓原创而过滤掉的转发条数
    wb.media_failure_count = 0       # 本轮媒体下载失败的文件数
    # 本轮「写入/读取失败」记录。
    # 每项：{"time", "stage", "target", "error"}；由 generate_write_failure_report 呈现。
    wb.run_failures = []

def calculate_dynamic_delay(wb) -> object:
    """计算动态延迟时间"""
    if not wb.anti_ban_enabled:
        return 0

    config = wb.anti_ban_config
    base_delay = config.get("request_delay_min", 8)

    # 根据请求次数增加延迟
    request_count = wb.crawl_stats["request_count"]
    if request_count > 100:
        base_delay += 5
    if request_count > 300:
        base_delay += 10

    # 根据爬取时间增加延迟
    if wb.crawl_stats["start_time"]:
        time_elapsed = time.time() - wb.crawl_stats["start_time"]
        if time_elapsed > 300:  # 5分钟
            base_delay += 5

    # 随机波动
    max_delay = config.get("request_delay_max", 15)
    return random.uniform(base_delay, max_delay)

def should_pause_session(wb) -> object:
    """检查是否应该暂停当前会话"""
    if not wb.anti_ban_enabled:
        return False, ""

    config = wb.anti_ban_config
    current_time = time.time()

    # 条件1：达到数量阈值
    max_weibo = config.get("max_weibo_per_session", 500)
    if wb.crawl_stats["weibo_count"] >= max_weibo:
        return True, f"达到单次运行最大微博数({max_weibo})"

    # 条件2：运行时间过长
    if wb.crawl_stats["start_time"]:
        session_time = current_time - wb.crawl_stats["start_time"]
        max_time = config.get("max_session_time", 600)
        if session_time > max_time:
            return True, f"单次运行时间过长({int(session_time)}秒)"

    # 条件3：API错误率过高
    max_errors = config.get("max_api_errors", 5)
    if wb.crawl_stats["api_errors"] >= max_errors:
        return True, f"API错误过多({wb.crawl_stats['api_errors']}次)"

    # 条件4：随机概率（模拟用户休息）
    random_prob = config.get("random_rest_probability", 0.01)
    if random.random() < random_prob:
        return True, "随机休息"

    return False, ""

def check_batch_delay(wb) -> None:
    """检查是否需要批次延迟"""
    if not wb.anti_ban_enabled:
        return

    config = wb.anti_ban_config
    batch_size = config.get("batch_size", 50)
    batch_delay = config.get("batch_delay", 30)

    # 检查是否达到批次大小
    if wb.crawl_stats["batch_count"] >= batch_size:
        current_time = time.time()

        # 检查距离上次批次的时间
        if wb.crawl_stats["last_batch_time"]:
            time_since_last_batch = current_time - wb.crawl_stats["last_batch_time"]
            if time_since_last_batch < batch_delay:
                # 如果距离上次批次时间太短，等待补足
                wait_time = batch_delay - time_since_last_batch
                logger.info(f"批次延迟: 等待 {wait_time:.1f} 秒")
                sleep(wait_time)

        logger.info(f"批次延迟: 等待 {batch_delay} 秒")
        sleep(batch_delay)

        # 重置批次计数
        wb.crawl_stats["batch_count"] = 0
        wb.crawl_stats["last_batch_time"] = time.time()

def get_random_headers(wb) -> object:
    """获取随机请求头（实现见 util/http_client.py）。"""
    return http_client.random_headers(
        getattr(wb, "anti_ban_enabled", 0),
        getattr(wb, "anti_ban_config", {}),
        wb.headers,
    )

def update_crawl_stats(wb, weibo_count=0, request_count=0, api_error=False) -> None:
    """更新爬取统计"""
    if not wb.anti_ban_enabled:
        return

    if weibo_count > 0:
        wb.crawl_stats["weibo_count"] += weibo_count
        wb.crawl_stats["batch_count"] += weibo_count

    if request_count > 0:
        wb.crawl_stats["request_count"] += request_count

    if api_error:
        wb.crawl_stats["api_errors"] += 1

def reset_crawl_stats(wb) -> None:
    """重置爬取统计（休息后调用）"""
    wb.crawl_stats = {
        "weibo_count": 0,
        "request_count": 0,
        "api_errors": 0,
        "start_time": time.time(),
        "batch_count": 0,
        "last_batch_time": None
    }
    logger.info("爬取统计已重置，继续爬取")

def perform_anti_ban_rest(wb) -> None:
    """执行防封禁休息"""
    if not wb.anti_ban_enabled:
        return

    config = wb.anti_ban_config
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


def validate(wb, config) -> None:
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
        "comment_pic_download",
        "comment_show_like_count",
        "comment_show_reply_target",
        "comment_emoji_replace",
        "comment_include_latest_stream",
        "comment_markdown_backup",
        "download_repost",
        "weibo_archive_check",
        "weibo_crawl_summary",
        "weibo_archive_check_full",
        "not_downloaded_record",
        "weibo_media_check",
        "weibo_comment_summary",
    ]
    for argument in argument_list:
        # 使用 get() 获取值，新增字段默认为0
        value = config.get(argument, 0)
        if value != 0 and value != 1:
            logger.warning("%s值应为0或1,请重新输入", argument)
            sys.exit()

    # 验证报告输出目录配置：必须是字符串（相对 output_directory 或绝对路径）
    for argument in (
        "weibo_archive_check_dir",
        "weibo_crawl_summary_dir",
        "weibo_archive_check_full_dir",
        "not_downloaded_record_dir",
        "weibo_media_check_dir",
        "weibo_comment_summary_dir",
    ):
        value = config.get(argument, "")
        if not isinstance(value, str):
            logger.warning("%s值应为字符串目录路径,请重新输入", argument)
            sys.exit()

    # 验证 invisible_weibo_dirs
    exclude_dirs = config.get(
        "invisible_weibo_dirs",
        list(archive_index.DEFAULT_EXCLUDE_DIRS),
    )
    if not isinstance(exclude_dirs, list) or not all(
        isinstance(d, str) for d in exclude_dirs
    ):
        logger.warning("invisible_weibo_dirs值应为字符串数组，请重新输入")
        sys.exit()

    # 验证query_list
    query_list = config.get("query_list") or []
    if (not isinstance(query_list, list)) and (not isinstance(query_list, str)):
        logger.warning("query_list值应为list类型或字符串,请重新输入")
        sys.exit()

    # 验证 page_weibo_count（每页微博数）：必须是 1-100 的整数
    page_weibo_count = config.get("page_weibo_count", 10)
    if isinstance(page_weibo_count, bool) or not isinstance(page_weibo_count, int):
        logger.warning("page_weibo_count 应为整数（每页微博数），请重新输入")
        sys.exit()
    if page_weibo_count < 1 or page_weibo_count > 100:
        logger.warning("page_weibo_count 应在 1-100 之间，请重新输入")
        sys.exit()

    # 验证 comment_max_requests（评论抓取请求预算上限）
    comment_max_requests = config.get("comment_max_requests", 300)
    if (
        isinstance(comment_max_requests, bool)
        or not isinstance(comment_max_requests, int)
        or not 1 <= comment_max_requests <= 10000
    ):
        logger.warning("comment_max_requests 应为 1-10000 的整数，请重新输入")
        sys.exit()

    # 验证write_mode：mysql/mongo/mongodb/post 是合法取值（实现见 util/legacy/backends.py，
    # 未在真实服务验证）；真正的无效值仍然报错退出。
    supported_write_modes = [
        "csv", "json", "sqlite", "markdown", "mysql", "mongo", "mongodb", "post",
    ]
    if not isinstance(config["write_mode"], list):
        sys.exit("write_mode值应为list类型")
    for mode in config["write_mode"]:
        if mode not in supported_write_modes:
            logger.warning(
                "%s为无效模式，请从%s中挑选一个或多个作为write_mode",
                mode,
                "、".join(supported_write_modes),
            )
            sys.exit()
    # 验证运行模式
    if "sqlite" not in config["write_mode"] and const.MODE == "append":
        logger.warning("append模式下请将sqlite加入write_mode中")
        sys.exit()

    # 验证markdown_split_by
    markdown_split_by = config.get("markdown_split_by", "day")
    if markdown_split_by not in ["day", "day_by_month", "month", "year", "all"]:
        logger.warning("markdown_split_by值应为day、day_by_month、month、year或all,请重新输入")
        sys.exit()

    # 验证markdown_image_order
    markdown_image_order = config.get("markdown_image_order", "live_photo_first")
    if markdown_image_order not in ["live_photo_first", "image_first_interleaved"]:
        logger.warning("markdown_image_order值应为live_photo_first或image_first_interleaved,请重新输入")
        sys.exit()

    # 验证markdown_sort_order
    markdown_sort_order = config.get("markdown_sort_order", "new_to_old")
    if markdown_sort_order not in ["new_to_old", "old_to_new"]:
        logger.warning("markdown_sort_order值应为new_to_old或old_to_new,请重新输入")
        sys.exit()

    # 验证markdown_preview
    md_preview = config.get("markdown_preview", {})
    if isinstance(md_preview, dict):
        valid_preview_keys = [
            "link_preview", "original_video_preview", "retweet_video_preview",
            "original_image_preview", "retweet_image_preview",
            "original_live_photo_preview", "retweet_live_photo_preview",
            "inline_image_preview",
        ]
        for key in md_preview:
            if key not in valid_preview_keys:
                logger.warning("markdown_preview中存在无效键: %s, 有效键为: %s", key, ", ".join(valid_preview_keys))
                sys.exit()
            if md_preview[key] not in (0, 1):
                logger.warning("markdown_preview.%s 值应为0或1,请重新输入", key)
                sys.exit()

    # 验证markdown_file_dir
    md_file_dir = config.get("markdown_file_dir", {})
    if isinstance(md_file_dir, dict):
        valid_md_dir_keys = [
            "original_image_dir", "retweet_image_dir",
            "original_video_dir", "retweet_video_dir",
            "original_live_photo_dir", "retweet_live_photo_dir",
            "comment_image_dir",
        ]
        for key in md_file_dir:
            if key not in valid_md_dir_keys:
                logger.warning("markdown_file_dir中存在无效键: %s, 有效键为: %s", key, ", ".join(valid_md_dir_keys))
                sys.exit()

    # 验证download_folder_name
    dl_folder = config.get("download_folder_name", {})
    if isinstance(dl_folder, dict):
        valid_dl_keys = [
            "original_image_folder", "retweet_image_folder",
            "original_video_folder", "retweet_video_folder",
            "original_live_photo_folder", "retweet_live_photo_folder",
            "comment_image_folder",
        ]
        for key in dl_folder:
            if key not in valid_dl_keys:
                logger.warning("download_folder_name中存在无效键: %s, 有效键为: %s", key, ", ".join(valid_dl_keys))
                sys.exit()

    # 验证user_id_list
    user_id_list = config["user_id_list"]
    if (not isinstance(user_id_list, list)) and (not user_id_list.endswith(".txt")):
        logger.warning("user_id_list值应为list类型或txt文件路径")
        sys.exit()
    if not isinstance(user_id_list, list):
        user_id_list = config_paths.resolve_config_file(
            project.project_root(), user_id_list
        )
        if not os.path.isfile(user_id_list):
            logger.warning("不存在%s文件", user_id_list)
            sys.exit()

    # 验证since_date
    since_date = config["since_date"]
    if (not isinstance(since_date, int)) and (not wb.is_datetime(since_date)) and (not wb.is_date(since_date)):
        logger.warning("since_date值应为yyyy-mm-dd形式、yyyy-mm-ddTHH:MM:SS形式或整数，请重新输入")
        sys.exit()

    # 验证end_date
    end_date = config.get("end_date", "")
    if end_date:
        if (not isinstance(end_date, int)) and (not wb.is_datetime(end_date)) and (not wb.is_date(end_date)):
            logger.warning("end_date值应为yyyy-mm-dd形式、yyyy-mm-ddTHH:MM:SS形式或整数，请重新输入")
            sys.exit()

    comment_max_count = config["comment_max_download_count"]
    if not isinstance(comment_max_count, int):
        logger.warning("最大下载评论数 (comment_max_download_count) 应为整数类型")
        sys.exit()
    elif comment_max_count < 0:
        logger.warning("最大下载评论数 (comment_max_download_count) 应该为正整数")
        sys.exit()

    # 二级回复上限与评论请求间隔
    comment_reply_max_count = config.get("comment_reply_max_download_count", 0)
    if not isinstance(comment_reply_max_count, int):
        logger.warning("每条评论最大回复数 (comment_reply_max_download_count) 应为整数类型")
        sys.exit()
    elif comment_reply_max_count < 0:
        logger.warning("每条评论最大回复数 (comment_reply_max_download_count) 不能为负数")
        sys.exit()
    for delay_key in ("comment_request_delay_min", "comment_request_delay_max"):
        delay_value = config.get(delay_key, 0)
        if isinstance(delay_value, bool) or not isinstance(delay_value, (int, float)):
            logger.warning("%s 应为数字（秒）", delay_key)
            sys.exit()
        if delay_value < 0:
            logger.warning("%s 不能为负数", delay_key)
            sys.exit()
    if config.get("comment_request_delay_min", 0) > config.get(
        "comment_request_delay_max", 0
    ):
        logger.warning(
            "comment_request_delay_min 不能大于 comment_request_delay_max"
        )
        sys.exit()

    # 评论者白名单：{微博账号: [评论者ID或昵称, ...]}
    comment_user_filter_file = config.get(
        "comment_user_filter_file", "comment_user_filter.txt"
    )
    if not isinstance(comment_user_filter_file, str):
        logger.warning(
            "comment_user_filter_file 应为文件路径字符串（如 config/comment_user_filter.txt）"
        )
        sys.exit()
    comment_user_filter = config.get("comment_user_filter", {})
    if not isinstance(comment_user_filter, dict):
        logger.warning(
            "comment_user_filter 应为对象：{微博账号: [评论者ID或昵称, ...]}"
        )
        sys.exit()
    for account_key, allowed_users in comment_user_filter.items():
        if not isinstance(allowed_users, list):
            logger.warning("comment_user_filter.%s 应为数组", account_key)
            sys.exit()
        for user_item in allowed_users:
            if isinstance(user_item, bool) or not isinstance(user_item, (str, int)):
                logger.warning(
                    "comment_user_filter.%s 的元素应为用户ID或昵称（字符串/数字）",
                    account_key,
                )
                sys.exit()

    repost_max_count = config["repost_max_download_count"]
    if not isinstance(repost_max_count, int):
        logger.warning("最大下载转发数 (repost_max_download_count) 应为整数类型")
        sys.exit()
    elif repost_max_count < 0:
        logger.warning("最大下载转发数 (repost_max_download_count) 应该为正整数")
        sys.exit()
