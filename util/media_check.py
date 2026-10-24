"""媒体文件轻量校验：文件头/尾部、常见容器签名与 Content-Length。

纯函数模块：不联网、不写文件，便于单元测试。
目标是拦截常见的「非媒体内容 / 明显不完整 / 内容长度不符」，
不做解码级校验（图片解码、视频可播放性留给更重的检查）。
"""

from __future__ import annotations

import os

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}

_HTML_PREFIXES = (
    b"<!doctype",
    b"<html",
    b"<head",
    b"<body",
    b"<?xml",
)


def looks_like_html(data: bytes) -> bool:
    """判断内容是否像 HTML/XML 错误页（CDN 拦截页常见）。"""
    head = (data or b"")[:512].lstrip().lower()
    return head.startswith(_HTML_PREFIXES)


def detect_kind(data: bytes) -> str:
    """根据文件头判断媒体类型；返回 jpeg/png/gif/webp/bmp/tiff/mp4/webm/html/unknown/empty。"""
    if not data:
        return "empty"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith(b"BM"):
        return "bmp"
    if data.startswith((b"II*\x00", b"MM\x00*")):
        return "tiff"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "mp4"
    if data.startswith(b"\x1aE\xdf\xa3"):
        return "webm"
    if looks_like_html(data):
        return "html"
    return "unknown"


def extension_of_kind(kind: str) -> str | None:
    return {
        "jpeg": ".jpg",
        "png": ".png",
        "gif": ".gif",
        "webp": ".webp",
        "bmp": ".bmp",
        "tiff": ".tiff",
        "mp4": ".mp4",
        "webm": ".webm",
    }.get(kind)


def _tail_endswith(data: bytes, suffix: bytes, window: int = 1024) -> bool:
    """允许文件末尾有少量附加字节，但仍要求目标尾部标记出现在末尾窗口内。"""
    if data.endswith(suffix):
        return True
    return suffix in data[-window:]


def validate_bytes(
    data: bytes,
    url: str = "",
    content_type: str = "",
    expected_ext: str | None = None,
) -> tuple[bool, str, str, str | None]:
    """校验下载到的字节内容。

    返回 (ok, reason, kind, detected_extension)。
    - 图片：校验文件头与关键尾部标记；
    - 视频：校验常见容器签名（ftyp / EBML）；
    - HTML/XML 错误页：直接判失败；
    - 无法识别的类型：如果 URL/期望扩展名表明是媒体，则判失败，否则放行。
    """
    if not data:
        return False, "内容为空", "empty", None

    kind = detect_kind(data)
    if kind == "html":
        return False, "内容是 HTML/XML 错误页", kind, None

    if kind == "jpeg":
        if not _tail_endswith(data, b"\xff\xd9"):
            return False, "JPEG 不完整（缺少 EOI 结尾）", kind, ".jpg"
        return True, "", kind, ".jpg"

    if kind == "png":
        if not _tail_endswith(data, b"IEND\xaeB`\x82"):
            return False, "PNG 不完整（缺少 IEND 结尾）", kind, ".png"
        return True, "", kind, ".png"

    if kind == "gif":
        if not _tail_endswith(data, b";"):
            return False, "GIF 不完整（缺少结尾分号）", kind, ".gif"
        return True, "", kind, ".gif"

    if kind == "webp":
        return True, "", kind, ".webp"
    if kind == "bmp":
        return True, "", kind, ".bmp"
    if kind == "tiff":
        return True, "", kind, ".tiff"
    if kind == "mp4":
        return True, "", kind, ".mp4"
    if kind == "webm":
        return True, "", kind, ".webm"

    # unknown：如果期望/URL/Content-Type 表明应该是媒体，则不能放行
    lower_url = (url or "").split("?")[0].lower()
    url_ext = os.path.splitext(lower_url)[1]
    expected = (expected_ext or "").lower()
    content = (content_type or "").lower()
    media_hint = (
        expected in IMAGE_EXTENSIONS
        or expected in VIDEO_EXTENSIONS
        or url_ext in IMAGE_EXTENSIONS
        or url_ext in VIDEO_EXTENSIONS
        or content.startswith("image/")
        or content.startswith("video/")
    )
    if media_hint:
        return False, "无法识别的媒体格式（可能是错误页或异常响应）", kind, None
    return True, "", kind, None


def check_content_length(header_value, actual_length: int) -> tuple[bool, str]:
    """比对响应头 Content-Length 与实际下载字节数；没有该头时放行。"""
    if header_value is None or header_value == "":
        return True, ""
    try:
        expected = int(str(header_value).strip())
    except (TypeError, ValueError):
        return True, ""
    if expected != actual_length:
        return False, f"Content-Length={expected}，实际下载={actual_length}"
    return True, ""


def validate_file(path: str) -> tuple[bool, str]:
    """轻量校验本地已存在的媒体文件（非空 + 头部/尾部检查）。

    只对能识别的格式做完整性检查；无法识别的格式只要非空就放行，
    避免把合法但少见的格式误判为损坏。
    """
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return False, f"无法读取文件大小: {e}"
    if size <= 0:
        return False, "文件为空（0 字节）"

    try:
        with open(path, "rb") as f:
            head = f.read(64)
            if size > 64:
                f.seek(max(0, size - 1024))
                tail = f.read(1024)
            else:
                tail = head
    except OSError as e:
        return False, f"无法读取文件: {e}"

    kind = detect_kind(head)
    if kind == "html":
        return False, "内容是 HTML/XML 错误页"
    if kind == "jpeg":
        return (True, "") if _tail_endswith(tail, b"\xff\xd9") else (False, "JPEG 不完整（缺少 EOI 结尾）")
    if kind == "png":
        return (True, "") if _tail_endswith(tail, b"IEND\xaeB`\x82") else (False, "PNG 不完整（缺少 IEND 结尾）")
    if kind == "gif":
        return (True, "") if _tail_endswith(tail, b";") else (False, "GIF 不完整（缺少结尾分号）")
    # 其他已知格式与未知格式：非空即放行
    return True, ""
