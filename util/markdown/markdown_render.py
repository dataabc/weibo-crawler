"""微博块正文渲染。

逻辑：处理原创/转发、正文
占位替换、媒体链接/预览、Live Photo 等；依赖的 wb 属性/方法由 Weibo 实例提供。
"""

from __future__ import annotations

import os
import re

# 「位置」类网页链接：微博 POI 页面（weibo.com/p/100101…）
_LOCATION_LINK_RE = re.compile(
    r"^https?://(?:[a-z0-9-]+\.)?weibo\.(?:com|cn)/p/100101", re.IGNORECASE
)
_MEDIA_LINE_RE = re.compile(r"^!?\[[^\]]*\]\([^)]*\)$")
# 位置文字前缀：让读者一眼看出这是位置，而不是正文
LOCATION_PREFIX = "📍 "


def _filter_location_links(wb, links) -> list:
    """按配置过滤「位置」类网页链接（默认不写入 md）。"""
    links = list(links or [])
    if int(getattr(wb, "markdown_show_location_link", 0) or 0):
        return links
    return [link for link in links if not _LOCATION_LINK_RE.match(str(link or ""))]


def _split_location_text(text: str, location: str) -> tuple[str, str]:
    """把微博正文末尾的定位文字（如「测试城市·测试地点」）拆出来。

    平台把定位拼在正文末尾（可能是整行，也可能紧跟正文用空白分隔）；这里只
    去掉末尾那一处，返回 (剩余正文, 定位文字)。定位为空或正文里找不到时原样返回。
    """
    text = str(text or "").strip()
    location = str(location or "").strip()
    if not text or not location:
        return text, ""
    lines = text.split("\n")
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip() == location:
            del lines[i]
            return "\n".join(lines).strip(), location
    stripped = text.rstrip()
    if stripped.endswith(location):
        before = stripped[: -len(location)]
        if not before or before[-1].isspace():
            return before.rstrip(), location
    return text, ""


def _append_location(parts: list[str], location: str) -> None:
    """把定位文字追加到媒体之后（微博块最后），空行分隔与正文媒体一致。"""
    if not location:
        return
    joined = "".join(parts)
    if not joined:
        prefix = ""
    elif joined.endswith("\n\n"):
        prefix = ""
    elif joined.endswith("\n"):
        prefix = "\n"
    else:
        prefix = "\n\n"
    location = str(location or "").strip()
    if not location.startswith(LOCATION_PREFIX):
        location = LOCATION_PREFIX + location
    parts.append(f"{prefix}{location}\n\n")



def _render_original_part(wb, w, created_at) -> str:
    """渲染「原创部分」正文，供原创块与转发块的原创部分共用。"""
    original_img_dir = wb.markdown_file_dir.get("original_image_dir", "img")
    original_video_dir = wb.markdown_file_dir.get("original_video_dir", "原创微博视频")
    text = w.get("text", "").strip()
    # 定位文字（平台拼在正文末尾）不跟正文走，移到媒体之后、微博块最后显示
    location = str(w.get("location") or "").strip()
    text, location_line = _split_location_text(text, location)
    links = _filter_location_links(wb, w.get("links") or [])
    # 渲染与下载开关联动：开关关闭时不写本地媒体链接，避免死链
    # ；正文占位保留原文。
    image_filenames = (
        wb.get_markdown_image_filenames(w, created_at)
        if wb.original_pic_download
        else []
    )
    rendered_text, remaining_links, remaining_images = wb.render_markdown_text(
        text, links, image_filenames
    )

    parts: list[str] = []
    # 含信息的网页链接（音乐/视频/卡片）：正文最后一行通常是链接标题，
    # 把它挪到链接前面紧挨显示，与前面的正文空一行隔开。
    link_title = ""
    if remaining_links and rendered_text and "\n" in rendered_text:
        lines = rendered_text.split("\n")
        while lines and not lines[-1].strip():
            lines.pop()
        if len(lines) >= 2:
            candidate = lines[-1].strip()
            if candidate and not _MEDIA_LINE_RE.match(candidate):
                link_title = candidate
                rendered_text = "\n".join(lines[:-1]).rstrip()
    if rendered_text:
        parts.append(f"{rendered_text}\n\n")

    if remaining_links:
        preview = wb.markdown_preview.get("link_preview", 0)
        if link_title:
            parts.append(f"{link_title}\n")
            for link in remaining_links:
                marker = "![网页链接]" if preview else "[网页链接]"
                parts.append(f"{marker}({link})\n")
            parts.append("\n")
        else:
            for link in remaining_links:
                marker = "![网页链接]" if preview else "[网页链接]"
                parts.append(f"{marker}({link})\n\n")

    video_preview = wb.markdown_preview.get("original_video_preview", 0)
    video_files = (
        wb.get_download_file_names("video", w.get("video_url", ""), w)
        if wb.original_video_download
        else []
    )
    for idx, file_name in enumerate(video_files, start=1):
        label = "视频" if len(video_files) == 1 else f"视频 {idx}"
        parts.append(
            f"{'!' if video_preview else ''}[{label}]"
            f"({original_video_dir}/{file_name})\n\n"
        )

    original_lp_dir_ref = wb.markdown_file_dir.get(
        "original_live_photo_dir", "原创微博Live Photo视频"
    )
    live_photo_prefix = original_lp_dir_ref + "/"
    live_photo_preview = wb.markdown_preview.get("original_live_photo_preview", 0)
    live_photo_files = (
        wb.get_download_file_names("live_photo", w.get("live_photo_url", ""), w)
        if wb.original_live_photo_download
        else []
    )
    image_preview = wb.markdown_preview.get("original_image_preview", 1)
    parts.append(
        wb._render_media_items(
            remaining_images, live_photo_files,
            live_photo_prefix, "Live Photo",
            original_img_dir,
            blockquote=False,
            image_preview=image_preview,
            live_photo_preview=live_photo_preview,
        )
    )
    _append_location(parts, location_line)
    return "".join(parts)


def render_block_body(wb, w, created_at) -> str:
    """渲染单条微博块正文（不含标题行、微博 ID 行、分隔线）。

    新微博写入与「已存档微博编辑更新」共用这一套渲染逻辑。
    """
    parts: list[str] = []
    # 处理转发微博
    if not wb.only_crawl_original and w.get("retweet"):
        retweet = w["retweet"]
        retweet_img_dir = wb.markdown_file_dir.get("retweet_image_dir", "img")
        retweet_video_dir = wb.markdown_file_dir.get("retweet_video_dir", "转发微博视频")
        # 原创部分
        parts.append(_render_original_part(wb, w, created_at))

        # 转发部分
        retweet_text = retweet.get("text", "").strip()
        retweet_links = _filter_location_links(wb, retweet.get("links") or [])
        retweet_image_filenames = (
            wb.get_markdown_image_filenames(retweet, created_at)
            if wb.retweet_pic_download
            else []
        )
        rendered_retweet_text, remaining_retweet_links, remaining_retweet_images = (
            wb.render_markdown_text(
                retweet_text,
                retweet_links,
                retweet_image_filenames,
                image_dir=retweet_img_dir,
            )
        )

        quoted_retweet = wb.format_blockquote(rendered_retweet_text, "转发")
        if quoted_retweet:
            parts.append(f"{quoted_retweet}\n\n")

        for link in remaining_retweet_links:
            if wb.markdown_preview.get("link_preview", 0):
                parts.append(f"> ![网页链接]({link})\n\n")
            else:
                parts.append(f"> [网页链接]({link})\n\n")

        retweet_video_preview = wb.markdown_preview.get("retweet_video_preview", 0)
        retweet_video_files = (
            wb.get_download_file_names("video", retweet.get("video_url", ""), retweet)
            if wb.retweet_video_download
            else []
        )
        for idx, file_name in enumerate(retweet_video_files, start=1):
            label = "转发视频" if len(retweet_video_files) == 1 else f"转发视频 {idx}"
            parts.append(
                f"> {'!' if retweet_video_preview else ''}[{label}]"
                f"({retweet_video_dir}/{file_name})\n\n"
            )

        retweet_lp_dir_ref = wb.markdown_file_dir.get(
            "retweet_live_photo_dir", "转发微博Live Photo视频"
        )
        retweet_live_photo_prefix = retweet_lp_dir_ref + "/"
        retweet_live_photo_preview = wb.markdown_preview.get(
            "retweet_live_photo_preview", 0
        )
        retweet_live_photo_files = (
            wb.get_download_file_names(
                "live_photo", retweet.get("live_photo_url", ""), retweet
            )
            if wb.retweet_live_photo_download
            else []
        )
        retweet_image_preview = wb.markdown_preview.get("retweet_image_preview", 1)
        parts.append(
            wb._render_media_items(
                remaining_retweet_images, retweet_live_photo_files,
                retweet_live_photo_prefix, "转发Live Photo",
                retweet_img_dir,
                blockquote=True,
                image_preview=retweet_image_preview,
                live_photo_preview=retweet_live_photo_preview,
            )
        )
    else:
        # 原创微博
        parts.append(_render_original_part(wb, w, created_at))
    return "".join(parts)


def render_media_items(wb, image_filenames, live_photo_files, live_photo_prefix,
                       live_photo_label_prefix, image_dir_name, blockquote=False,
                       image_preview=1, live_photo_preview=1) -> object:
    """根据 markdown_image_order 配置渲染图片和 Live Photo 的 Markdown 内容

    live_photo_first: Live Photo 全部放前面，图片放后面（现有模式）
    image_first_interleaved: 图片优先，同名 Live Photo 插入到对应图片下方

    image_preview / live_photo_preview: 控制是否使用 ! 语法实现直接预览
    image_dir_name: 图片文件所在的目录名（如 "img"）
    """
    quote = "> " if blockquote else ""

    def _img_link(filename):
        if image_preview:
            return f"{quote}![img]({image_dir_name}/{filename})\n"
        else:
            return f"{quote}[img]({image_dir_name}/{filename})\n"

    def _lp_link(label, filename):
        if live_photo_preview:
            return f"{quote}![{label}]({live_photo_prefix}{filename})\n"
        else:
            return f"{quote}[{label}]({live_photo_prefix}{filename})\n"

    lines = []

    if wb.markdown_image_order == "image_first_interleaved":
        # 构建 Live Photo stem → (label, filename) 映射
        # stem 如 YYYY-MM-DD_HH-MM-SS_1.mov → YYYY-MM-DD_HH-MM-SS_1
        lp_map = {}
        for i, file_name in enumerate(live_photo_files):
            stem = os.path.splitext(file_name)[0]
            label = (live_photo_label_prefix if len(live_photo_files) == 1
                     else f"{live_photo_label_prefix} {i + 1}")
            lp_map[stem] = (label, file_name)

        # 先输出图片，每张图片后紧跟同名 Live Photo
        remaining_lp_stems = set(lp_map.keys())
        for img_filename in image_filenames:
            lines.append(_img_link(img_filename))
            img_stem = os.path.splitext(img_filename)[0]
            if img_stem in lp_map:
                label, lp_filename = lp_map[img_stem]
                # Live Photo 紧跟同名图片，不加空行
                lines.append(_lp_link(label, lp_filename))
                remaining_lp_stems.discard(img_stem)
            # 每条图片（及其可能跟随的 Live Photo）之后加一个空行
            lines.append("\n")

        # 输出未能匹配到任何图片的 Live Photo（异常情况兜底）
        for stem in remaining_lp_stems:
            label, lp_filename = lp_map[stem]
            lines.append(_lp_link(label, lp_filename) + "\n")
    else:
        # live_photo_first: Live Photo 全部在前，图片在后（现有默认模式）
        for i, file_name in enumerate(live_photo_files):
            label = (live_photo_label_prefix if len(live_photo_files) == 1
                     else f"{live_photo_label_prefix} {i + 1}")
            lines.append(_lp_link(label, file_name) + "\n")
        for img_filename in image_filenames:
            lines.append(_img_link(img_filename) + "\n")

    return "".join(lines)
