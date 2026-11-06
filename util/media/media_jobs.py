"""媒体下载编排。

``download_files`` 负责按类型（img/video/live_photo 等）分派媒体下载并写
JSON 记录；``download_markdown_images`` 负责回填 markdown 媒体文件名。两者
依赖的 Weibo 方法（download_one_file 等）由调用方传入 wb 实例提供。
"""

from __future__ import annotations

import logging
import os
import random
from datetime import datetime
from time import sleep

from tqdm import tqdm

from util.core.time_format import DTFORMAT

logger = logging.getLogger("weibo")


def download_files(wb, file_type, weibo_type, wrote_count) -> None:
    """按类型下载媒体文件（等价原 Weibo.download_files）。"""
    try:
        describe = ""
        if file_type == "img":
            describe = "图片"
            key = "pics"
            folder_key = "original_image_folder" if weibo_type == "original" else "retweet_image_folder"
        elif file_type == "video":
            describe = "视频"
            key = "video_url"
            folder_key = "original_video_folder" if weibo_type == "original" else "retweet_video_folder"
        elif file_type == "live_photo":
            describe = "Live Photo视频"
            key = "live_photo_url"
            folder_key = "original_live_photo_folder" if weibo_type == "original" else "retweet_live_photo_folder"
        else:
            return

        # 使用自定义的下载文件夹名称（非 markdown 模式）
        folder_name = wb.download_folder_name.get(folder_key, describe)
        # markdown 模式下媒体目录必须与 Markdown 链接使用的目录名一致：
        # 链接用 markdown_file_dir.<type>_dir，落盘也用同一个名字，
        # 否则「下载到 video/原创微博视频、链接写 原创微博视频」会永远对不上
        # 。
        md_dir_key = {
            ("img", "original"): "original_image_dir",
            ("img", "retweet"): "retweet_image_dir",
            ("video", "original"): "original_video_dir",
            ("video", "retweet"): "retweet_video_dir",
            ("live_photo", "original"): "original_live_photo_dir",
            ("live_photo", "retweet"): "retweet_live_photo_dir",
        }.get((file_type, weibo_type))
        md_folder_name = (
            wb.markdown_file_dir.get(md_dir_key, folder_name)
            if md_dir_key
            else folder_name
        )
        if weibo_type == "original":
            describe = "原创微博" + describe
        else:
            describe = "转发微博" + describe

        logger.info("即将进行%s下载", describe)

        # 检查是否有文件需要下载
        has_files = False
        for w in wb.weibo[wrote_count:]:
            if weibo_type == "retweet":
                if w.get("retweet"):
                    w = w["retweet"]
                else:
                    continue
            if w.get(key):
                has_files = True
                break

        if not has_files:
            logger.info("没有%s需要下载", describe)
            return

        # markdown 模式：媒体与 Markdown 同目录结构，目录名与 md 链接一致

        if "markdown" in wb.write_mode:
            base_dir = wb.get_filepath("markdown")
            if wb.markdown_split_by == "day_by_month":
                for w in tqdm(wb.weibo[wrote_count:], desc="Download progress"):
                    # 对于转发微博，使用父微博的日期确定月份文件夹
                    # 这样转发的内容会与父微博保存在同一个月份目录中
                    parent_created_at = w.get("created_at", "")
                    if not parent_created_at:
                        continue
                    try:
                        parent_time_obj = datetime.strptime(parent_created_at, DTFORMAT)
                        month_folder = parent_time_obj.strftime("%Y-%m")
                    except ValueError:
                        continue

                    weibo_data = w
                    if weibo_type == "retweet":
                        if w.get("retweet"):
                            weibo_data = w["retweet"]
                        else:
                            continue

                    if not weibo_data.get(key):
                        continue

                    # 创建月份子目录下的文件目录（使用父微博的月份）
                    month_dir = os.path.join(base_dir, month_folder)
                    file_dir = os.path.join(month_dir, md_folder_name)
                    os.makedirs(file_dir, exist_ok=True)

                    wb.handle_download(
                        file_type, file_dir, weibo_data.get(key), weibo_data
                    )

                logger.info("%s下载完毕", describe)
            else:
                # 非 day_by_month：媒体放在 Markdown 根目录下的链接目录
                file_dir = os.path.join(base_dir, md_folder_name)
                os.makedirs(file_dir, exist_ok=True)

                for w in tqdm(wb.weibo[wrote_count:], desc="Download progress"):
                    if weibo_type == "retweet":
                        if w.get("retweet"):
                            w = w["retweet"]
                        else:
                            continue
                    if w.get(key):
                        wb.handle_download(file_type, file_dir, w.get(key), w)

                logger.info("%s下载完毕,保存路径:", describe)
                logger.info(file_dir)
        else:
            # 非 markdown 模式：使用默认目录结构（<用户>/<type>/<下载目录名>）
            file_dir = os.path.join(wb.get_filepath(file_type), folder_name)
            os.makedirs(file_dir, exist_ok=True)

            for w in tqdm(wb.weibo[wrote_count:], desc="Download progress"):
                if weibo_type == "retweet":
                    if w.get("retweet"):
                        w = w["retweet"]
                    else:
                        continue
                if w.get(key):
                    wb.handle_download(file_type, file_dir, w.get(key), w)

            logger.info("%s下载完毕,保存路径:", describe)
            logger.info(file_dir)
    except Exception as e:  # noqa: BLE001 兜底：失败不阻断
        logger.exception(e)


def download_markdown_images(wb, wrote_count) -> None:
    """为Markdown格式下载图片，使用指定的命名规则。

    - 原创/转发图片分别按各自开关下载；
    - 转发图片落到 ``markdown_file_dir.retweet_image_dir``（与渲染端一致）。
    """
    # 获取用户目录
    file_dir = wb.get_filepath("markdown")
    original_dir_name = wb.markdown_file_dir.get("original_image_dir", "img")
    retweet_dir_name = wb.markdown_file_dir.get("retweet_image_dir", "img")

    # 对于 day_by_month 模式，按月分组图片
    if wb.markdown_split_by == "day_by_month":
        # 按月分组微博，然后为每个月创建img目录
        for w in wb.weibo[wrote_count:]:
            created_at = w.get("created_at", "")
            if not created_at:
                continue
            try:
                time_obj = datetime.strptime(created_at, DTFORMAT)
                month_folder = time_obj.strftime("%Y-%m")
            except ValueError:
                continue

            month_dir = os.path.join(file_dir, month_folder)

            # 处理原创微博图片
            if wb.original_pic_download and w.get("pics"):
                img_dir = os.path.join(month_dir, original_dir_name)
                os.makedirs(img_dir, exist_ok=True)
                wb._download_weibo_images(w, img_dir, is_retweet=False)

            # 处理转发微博图片（使用父微博的月份文件夹）
            if (
                not wb.only_crawl_original
                and wb.retweet_pic_download
                and w.get("retweet")
            ):
                retweet = w["retweet"]
                if retweet.get("pics"):
                    retweet_img_dir = os.path.join(month_dir, retweet_dir_name)
                    os.makedirs(retweet_img_dir, exist_ok=True)
                    wb._download_weibo_images(
                        retweet, retweet_img_dir, is_retweet=True
                    )
    else:
        # 其他模式：所有图片放在同一个 img 目录
        # 下载图片
        for w in wb.weibo[wrote_count:]:
            # 处理原创微博图片
            if wb.original_pic_download and w.get("pics"):
                img_dir = os.path.join(file_dir, original_dir_name)
                os.makedirs(img_dir, exist_ok=True)
                wb._download_weibo_images(w, img_dir, is_retweet=False)

            # 处理转发微博图片
            if (
                not wb.only_crawl_original
                and wb.retweet_pic_download
                and w.get("retweet")
            ):
                retweet = w["retweet"]
                if retweet.get("pics"):
                    retweet_img_dir = os.path.join(file_dir, retweet_dir_name)
                    os.makedirs(retweet_img_dir, exist_ok=True)
                    wb._download_weibo_images(
                        retweet, retweet_img_dir, is_retweet=True
                    )


def _split_media_urls(urls: str, separator: str) -> list[str]:
    """按分隔符拆分媒体 URL 列表（跳过空项；无分隔符时视为单元素）。"""
    if not urls:
        return []
    if separator in urls:
        return [url for url in urls.split(separator) if url]
    return [urls]


def _image_suffix(url: str) -> str:
    """按 URL 末尾推断图片扩展名；不像扩展名（无点或长度 > 4）时回退 .jpg。"""
    index = url.rfind(".")
    if index < 0 or len(url) - index >= 5:
        return ".jpg"
    return url[index:]


def _media_file_names(wb, file_type, urls, w) -> list[tuple[str, str]]:
    """按命名规则推导 (url, 本地文件名) 列表，供下载侧与 Markdown 链接侧共用。

    落盘名与 Markdown 链接名由同一函数产生，避免多视频/混合扩展名时
    文件名不一致导致断链。
    """
    file_prefix = wb._get_timestamp_prefix(w)
    pairs: list[tuple[str, str]] = []
    if file_type == "img":
        url_list = _split_media_urls(urls, ",")
        for i, url in enumerate(url_list):
            numbered = "_" + str(i + 1) if len(url_list) > 1 else ""
            pairs.append((url, file_prefix + numbered + _image_suffix(url)))
    elif file_type == "video":
        url_list = _split_media_urls(urls, ";")
        for i, url in enumerate(url_list):
            suffix = ".mov" if url.endswith(".mov") else ".mp4"
            numbered = "_" + str(i + 1) if len(url_list) > 1 else ""
            pairs.append((url, file_prefix + numbered + suffix))
    elif file_type == "live_photo":
        # 使用 live_photo_indices 使 live_photo 文件名与对应图片文件名一致
        indices = w.get("live_photo_indices", [])
        url_list = _split_media_urls(urls, ";")
        pics_count = len([p for p in (w.get("pics", "") or "").split(",") if p])
        for j, url in enumerate(url_list):
            suffix = ".mov" if url.endswith(".mov") else ".mp4"
            idx = indices[j] if j < len(indices) else (j + 1)
            if pics_count > 1:
                pairs.append((url, file_prefix + "_" + str(idx) + suffix))
            else:
                pairs.append((url, file_prefix + suffix))
    return pairs


def handle_download(wb, file_type, file_dir, urls, w) -> None:
    """处理下载相关操作"""
    pairs = _media_file_names(wb, file_type, urls, w)
    for index, (url, file_name) in enumerate(pairs):
        file_path = file_dir + os.sep + file_name
        wb.download_one_file(url, file_path, file_type, w["id"], w["created_at"])
        # 视频下载间隔延迟，减少触发CDN限流（仅 video/live_photo 多文件时）
        if file_type in ("video", "live_photo") and index < len(pairs) - 1:
            sleep(random.uniform(1, 3))


def get_download_file_names(wb, file_type, urls, w) -> list[str]:
    """根据下载规则推导本地文件名，供 Markdown 链接使用"""
    return [file_name for _url, file_name in _media_file_names(wb, file_type, urls, w)]
