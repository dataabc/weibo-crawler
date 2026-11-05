"""微博评论抓取与清洗。

数据来源：
- 一级评论：``GET https://m.weibo.cn/comments/hotflow``，每页 14～19 条，
  ``data.max_id`` 为 0 时结束；实测热门流最多约 16 页 / 255 条。
- 二级回复：``GET https://m.weibo.cn/comments/hotFlowChild``，参数
  ``cid`` / ``max_id`` / ``max_id_type``，每页 2～10 条，``max_id`` 为 0 时结束。
- 最新流（可选）：``GET https://m.weibo.cn/api/comments/show``，每页 10 条，
  最多约 50 页；字段少、无图片/表情/楼层，只作为补充。

本模块不 import requests，session 由调用方注入，便于单元测试。
"""

from __future__ import annotations

import html
import logging
import os
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, unquote, urlsplit

from util.core.weibo_visibility import (
    WEIBO_STATUS_URL,
    check_weibo_visibility,
)

logger = logging.getLogger(__name__)

HOTFLOW_URL = "https://m.weibo.cn/comments/hotflow"
REPLY_URL = "https://m.weibo.cn/comments/hotFlowChild"
LATEST_URL = "https://m.weibo.cn/api/comments/show"

CST = timezone(timedelta(hours=8))

# ``_request_json`` 的四种结果：拿到数据 / 接口明确「没有内容」/ 抓取失败 /
# 请求预算用尽。它把「失败」与「确实没有评论」分开，供调用方可靠判断。
_REQUEST_OK = "ok"
_REQUEST_NO_CONTENT = "no_content"
_REQUEST_FAILED = "failed"
_REQUEST_BUDGET = "budget"

# 接口用 ``ok=0`` + 「这里还没有内容」这类业务提示表示「确实没有内容」；这属于
# 「无评论」，不是抓取失败，也不需要重试。只认已实测确认的提示，避免把风控/限流
# 类失败误判成「无评论」。
# 接口 ok=0 时表示「确实没有内容」的业务提示（不同版本/场景措辞不同）。
_NO_CONTENT_HINTS = (
    "还没有内容",
    "还没有人评论",
    "暂无评论",
    # 旧微博评论被删/不可见时，接口固定返回「暂无数据」：按无评论处理，避免
    # 把每轮都失败的老微博当成抓取失败、触发连续失败熔断。
    "暂无数据",
    "暂无内容",
    "快来抢沙发",
    "快来发表你的评论吧",
    # 平台过滤/防火墙导致「拿不到评论」：属于接口明确无内容，不是抓取失败
    "已过滤部分评论",
    "博主已开启防火墙",
)

_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

_ENGLISH_TIME_RE = re.compile(
    r"^[A-Za-z]{3}\s+([A-Za-z]{3})\s+(\d{1,2})\s+(\d{1,2}):(\d{2}):(\d{2})"
    r"\s+\+0800\s+(\d{4})$"
)
_YYYY_MM_DD_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?$")
_MM_DD_RE = re.compile(r"^(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?$")
_MINUTES_AGO_RE = re.compile(r"^(\d+)\s*分钟前$")
_HOURS_AGO_RE = re.compile(r"^(\d+)\s*小时前$")
_YESTERDAY_RE = re.compile(r"^昨天\s*(\d{1,2}):(\d{2})$")
_TODAY_RE = re.compile(r"^今天\s*(\d{1,2}):(\d{2})$")
_HHMM_RE = re.compile(r"^(\d{1,2}):(\d{2})$")

_REPLY_TARGET_HTML_RES = (
    # 「回复<a>@某人</a>：正文」
    re.compile(r"回复\s*<a[^>]*>\s*@?([^<]+?)\s*</a>"),
    # 正文直接以「<a>@某人</a> 正文」开头（楼中楼回复对象的常见形式）
    re.compile(r"^\s*<a[^>]*>\s*@?([^<]+?)\s*</a>"),
)
_REPLY_TARGET_TEXT_RE = re.compile(r"^回复\s*@?([^\s:：]+)\s*[:：]")
_REPLY_TARGET_TEXT_AT_RE = re.compile(r"^@([^\s:：]+)[:：]?\s")


@dataclass
class FetchOptions:
    """抓取参数。``0`` 表示不限制（翻到接口结束）。"""

    max_root: int = 0
    max_reply: int = 0
    max_requests: int = 300
    delay_min: float = 1.0
    delay_max: float = 2.0
    include_latest: bool = False
    timeout: float = 15.0
    max_retries: int = 2
    # 对应配置 comment_emoji_replace：0 = 保留表情外链图片；1 = 不显示图片，
    # 优先输出表情名（alt），没有 alt 时回退为可点击外链。
    emoji_replace: bool = True
    # 网页链接卡片：False（默认）= 输出 ``[网页链接](href)``；
    # True = 输出 ``![网页链接](href)``。
    link_preview: bool = False
    sleep: Callable[[float], None] = time.sleep
    rand: Callable[[], float] = random.random


@dataclass
class Comment:
    """一条评论（一级或二级）。"""

    id: str
    weibo_id: str
    is_root: bool = False
    root_id: str = ""
    user_id: str = ""
    user_name: str = ""
    created_at: str = ""
    timestamp: float = 0.0
    text: str = ""
    like_count: int = 0
    reply_target: str = ""
    pic_url: str = ""
    pic_file: str = ""
    floor: int = 0
    reply_total: int = 0
    replies: list["Comment"] = field(default_factory=list)


@dataclass
class FetchStats:
    requests: int = 0
    errors: int = 0
    total_number: int = 0
    roots_fetched: int = 0
    replies_fetched: int = 0
    latest_fetched: int = 0
    stopped_reason: str = ""
    # 接口 ok=0 明确「无内容」时记下最后一条提示，供区分「暂无数据（待复核）」
    # 与「还没有人评论（确定无评论）」。
    empty_hint: str = ""
    error_messages: list[str] = field(default_factory=list)

    def note_error(self, message: str) -> None:
        self.errors += 1
        if len(self.error_messages) < 20:
            self.error_messages.append(message)


@dataclass
class FetchResult:
    """一条微博的评论抓取结果。

    ``failed`` 表示抓取过程中至少有一个请求在重试后仍然失败（网络异常、
    HTTP 非 2xx、接口 ``ok != 1`` 等）。此时 ``roots`` 可能是空列表，也可能
    只是部分数据，**不能把空列表解读为「确实没有评论」**：
    只有 ``failed is False`` 且 ``roots`` 为空才是「无评论」。
    """

    weibo_id: str
    total_number: int
    roots: list[Comment]
    stats: FetchStats
    failed: bool = False


def _src_host(src: str) -> str:
    """取 ``src`` 的主机名；无法取出（相对路径、data: 等）时返回空字符串。"""
    candidate = (src or "").strip()
    if not candidate:
        return ""
    if candidate.startswith("//"):
        candidate = "https:" + candidate
    elif "://" not in candidate:
        if candidate.startswith("/"):
            return ""
        candidate = "https://" + candidate
    try:
        host = urlsplit(candidate).hostname or ""
    except ValueError:
        return ""
    return host.lower().rstrip(".")


def _visible_text(text: str) -> str:
    """去掉全部空白与零宽字符，用于判断链接卡片里是否只剩「网页链接」。"""
    return "".join((text or "").split()).replace("\u200b", "").replace("\ufeff", "")

_IMAGE_PATH_RE = re.compile(r"\.(?:jpg|jpeg|png|gif|webp)$", re.IGNORECASE)


def _image_url_from_href(href: str) -> str:
    """从评论里的链接卡片 href 提取图片 URL（支持直链与 weibo.cn/sinaurl 包装）。"""
    raw = (href or "").strip()
    if not raw:
        return ""
    candidates = [raw]
    try:
        query = parse_qs(urlsplit(raw).query)
        for value in query.get("u", []):
            decoded = unquote(value)
            if decoded:
                candidates.append(decoded)
    except ValueError:
        pass
    for candidate in candidates:
        try:
            path = urlsplit(candidate).path
        except ValueError:
            path = candidate
        if _IMAGE_PATH_RE.search(path):
            return candidate
    return ""


class _TextExtractor(HTMLParser):
    """把评论 HTML 转成纯文本；换行标签转换行，表情 img 按配置语义转换。

    ``emoji_replace`` 与配置 ``comment_emoji_replace`` 对应：

    - ``0``（保留外链图片）：有 ``src`` 时输出 ``![表情名或表情](src)``，表情名取
      ``alt`` 去掉两端方括号；无 ``src`` 但有 ``alt`` 时输出 ``alt`` 原文；都没有
      时输出 ``[表情]``。
    - ``1``（默认，不显示图片）：有 ``alt`` 时输出 ``alt`` 原文；无 ``alt`` 但有
      ``src`` 时输出可点击的 ``[表情](src)``；都没有时输出 ``[表情]``。

    只有表情图片才按上面的语义输出，判定规则见 :meth:`_is_emoji_img`；其余
    ``<img>``（网页链接卡片图标、各类 UI 图标）直接丢弃。

    ``link_preview`` 控制网页链接卡片的输出：``<a href="http(s)://...">`` 的可见
    文本去空白后正好是「网页链接」时，``False``（默认）输出
    ``[网页链接](href)``，``True`` 输出 ``![网页链接](href)``。其他 ``<a>`` 只输出
    可见文本，不带 URL。
    """

    _BLOCK_TAGS = {"p", "div", "li", "blockquote", "section"}
    _BR_TAGS = {"br"}
    _EMOJI_HOST = "face.t.sinajs.cn"
    _EMOJI_CLASS_HINTS = ("emoji", "face", "expression")
    _LINK_CARD_TEXT = "网页链接"

    def __init__(
        self,
        emoji_replace: bool = True,
        link_preview: bool = False,
        pictures: list[str] | None = None,
    ) -> None:
        super().__init__(convert_charrefs=True)
        self.emoji_replace = emoji_replace
        self.link_preview = link_preview
        self.pictures: list[str] = pictures if pictures is not None else []
        self.parts: list[str] = []
        # 每个 <a> 一项：http(s) 链接存 (href, 内容缓冲)，其他链接存 None。
        # 卡片要在 </a> 时才能判断整段可见文本，所以先按标签层级缓冲拼接。
        self._anchors: list[Optional[tuple[str, list[str]]]] = []

    @staticmethod
    def _attr(attrs: list[tuple[str, Optional[str]]], name: str) -> str:
        """取标签的属性值；HTMLParser 已解好引号与实体，空值按缺失处理。"""
        for key, value in attrs:
            if key == name:
                return (value or "").strip()
        return ""

    @classmethod
    def _is_emoji_img(cls, attrs: list[tuple[str, Optional[str]]]) -> bool:
        """只有表情 CDN 的图片或带表情语义 class 的图片才算表情。"""
        classes = cls._attr(attrs, "class").lower()
        if any(hint in classes for hint in cls._EMOJI_CLASS_HINTS):
            return True
        host = _src_host(cls._attr(attrs, "src"))
        return host == cls._EMOJI_HOST or host.endswith("." + cls._EMOJI_HOST)

    @staticmethod
    def _is_http_url(url: str) -> bool:
        return url.lower().startswith(("http://", "https://"))

    def _echo(self, text: str) -> None:
        """输出文本；在 http(s) 链接卡片内时先攒进卡片缓冲。"""
        if not text:
            return
        for anchor in reversed(self._anchors):
            if anchor is not None:
                anchor[1].append(text)
                return
        self.parts.append(text)

    def _emoji_text(self, attrs: list[tuple[str, Optional[str]]]) -> str:
        """按 ``emoji_replace`` 的 0/1 语义输出一个表情 img。"""
        src = self._attr(attrs, "src")
        alt = self._attr(attrs, "alt")
        if self.emoji_replace:
            # 1 = 不显示表情图片：优先表情名，没有表情名才回退可点击外链。
            if alt:
                return alt
            if src:
                return f"[表情]({src})"
            return "[表情]"
        # 0 = 保留表情外链图片；alt="[微笑]" 时取方括号内的名字。
        if src:
            name = alt
            if name.startswith("[") and name.endswith("]"):
                name = name[1:-1].strip()
            return f"![{name or '表情'}]({src})"
        if alt:
            return alt
        return "[表情]"

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag in self._BR_TAGS:
            self._echo("\n")
        elif tag == "img":
            if self._is_emoji_img(attrs):
                self._echo(self._emoji_text(attrs))
        elif tag == "a":
            href = self._attr(attrs, "href")
            self._anchors.append((href, []) if self._is_http_url(href) else None)
        elif tag in self._BLOCK_TAGS:
            self._echo("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag in self._BR_TAGS:
            self._echo("\n")
        elif tag == "img" and self._is_emoji_img(attrs):
            self._echo(self._emoji_text(attrs))

    def handle_data(self, data: str) -> None:
        self._echo(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            if not self._anchors:
                return
            anchor = self._anchors.pop()
            if anchor is None:
                return
            href, buffer = anchor
            content = "".join(buffer)
            visible = _visible_text(content)
            if visible == "评论配图":
                # 评论配图：链接卡片指向图片，记录图片 URL 交给评论图片下载，
                # 不输出「评论配图」占位文字。
                image_url = _image_url_from_href(href)
                if image_url:
                    self.pictures.append(image_url)
                    return
            if visible == self._LINK_CARD_TEXT:
                marker = "![网页链接]" if self.link_preview else "[网页链接]"
                self._echo(f"{marker}({href})")
            else:
                self._echo(content)
        elif tag in self._BLOCK_TAGS:
            self._echo("\n")

    def close(self) -> None:
        super().close()
        # 畸形 HTML 里 <a> 可能没有闭合：把缓冲内容原样输出，避免吞掉后文。
        while self._anchors:
            anchor = self._anchors.pop()
            if anchor is not None:
                self._echo("".join(anchor[1]))


def clean_comment_html(
    text: str,
    emoji_replace: bool = True,
    link_preview: bool = False,
    pictures: list[str] | None = None,
) -> str:
    """评论 HTML 转纯文本：去标签、保留 @ 昵称与换行。

    ``emoji_replace`` 的 0/1 语义见 :class:`_TextExtractor`：``0`` 保留表情外链
    图片；``1``（默认）不显示图片，优先输出表情名，没有表情名时回退可点击外链。
    非表情图片（链接卡片图标、UI 图标）不输出任何文本。

    ``link_preview`` 的两种取值见 :class:`_TextExtractor`：``False``（默认）时
    网页链接卡片输出 ``[网页链接](href)``；``True`` 输出 ``![网页链接](href)``。
    """
    if not text:
        return ""
    parser = _TextExtractor(
        emoji_replace=emoji_replace, link_preview=link_preview, pictures=pictures
    )
    try:
        parser.feed(text)
        parser.close()
        raw = "".join(parser.parts)
    except Exception:  # 极端畸形 HTML 兜底
        raw = re.sub(r"<[^>]+>", "", text)
    raw = html.unescape(raw)
    raw = raw.replace("\u200b", "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in raw.split("\n")]
    cleaned: list[str] = []
    blank = False
    for line in lines:
        if not line.strip():
            if not blank and cleaned:
                cleaned.append("")
                blank = True
            continue
        cleaned.append(line)
        blank = False
    return "\n".join(cleaned).strip()


def parse_comment_time(value: str, now: Optional[datetime] = None) -> tuple[str, float]:
    """把评论时间转成 ``(YYYY-MM-DD HH:MM:SS, timestamp)``；无法解析返回 ``("", 0)``。"""
    text = (value or "").strip()
    if not text:
        return "", 0.0
    now = now or datetime.now(CST)

    m = _ENGLISH_TIME_RE.match(text)
    if m and m.group(1) in _MONTHS:
        dt = datetime(
            int(m.group(6)), _MONTHS[m.group(1)], int(m.group(2)),
            int(m.group(3)), int(m.group(4)), int(m.group(5)), tzinfo=CST,
        )
        return dt.strftime("%Y-%m-%d %H:%M:%S"), dt.timestamp()

    if text == "刚刚":
        return _time_result(now)

    m = _MINUTES_AGO_RE.match(text)
    if m:
        return _time_result(now - timedelta(minutes=int(m.group(1))))

    m = _HOURS_AGO_RE.match(text)
    if m:
        return _time_result(now - timedelta(hours=int(m.group(1))))

    m = _YESTERDAY_RE.match(text)
    if m:
        dt = (now - timedelta(days=1)).replace(
            hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0
        )
        return _time_result(dt)

    m = _TODAY_RE.match(text)
    if m:
        dt = now.replace(
            hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0
        )
        return _time_result(dt)

    m = _HHMM_RE.match(text)
    if m:
        dt = now.replace(
            hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0
        )
        return _time_result(dt)

    m = _YYYY_MM_DD_RE.match(text)
    if m:
        dt = datetime(
            int(m.group(1)), int(m.group(2)), int(m.group(3)),
            int(m.group(4)), int(m.group(5)), int(m.group(6) or 0), tzinfo=CST,
        )
        return _time_result(dt)

    m = _MM_DD_RE.match(text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        hour = int(m.group(3) or 0)
        minute = int(m.group(4) or 0)
        year = now.year
        try:
            dt = datetime(year, month, day, hour, minute, tzinfo=CST)
        except ValueError:
            return "", 0.0
        if dt > now + timedelta(days=1):
            dt = dt.replace(year=year - 1)
        return _time_result(dt)

    return "", 0.0


def _time_result(dt: datetime) -> tuple[str, float]:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CST)
    return dt.strftime("%Y-%m-%d %H:%M:%S"), dt.timestamp()


def extract_reply_target(raw_html: str, cleaned_text: str = "") -> str:
    """从二级回复的 HTML/文本中提取被回复的用户名。

    实测回复对象有两种形式：
    1. ``回复<a href="/n/某人">@某人</a>：正文``；
    2. 正文直接以 ``<a href="/n/某人">@某人</a> 正文`` 开头。
    没有回复对象时返回空字符串。
    """
    for pattern in _REPLY_TARGET_HTML_RES:
        match = pattern.search(raw_html or "")
        if match:
            return match.group(1).strip()
    match = _REPLY_TARGET_TEXT_RE.match((cleaned_text or "").strip())
    if match:
        return match.group(1).strip()
    match = _REPLY_TARGET_TEXT_AT_RE.match((cleaned_text or "").strip())
    if match:
        return match.group(1).strip()
    return ""


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _pick_pic_url(raw: dict) -> str:
    pic = raw.get("pic") or {}
    if not isinstance(pic, dict):
        return ""
    large = pic.get("large") or {}
    if isinstance(large, dict) and large.get("url"):
        return str(large["url"])
    return str(pic.get("url") or "")


def parse_comment(
    raw: dict,
    weibo_id: str,
    is_root: bool = False,
    emoji_replace: bool = True,
    link_preview: bool = False,
) -> Optional[Comment]:
    """把接口单条评论转成 :class:`Comment`。

    ``emoji_replace`` / ``link_preview`` 的语义见 :func:`clean_comment_html`。
    """
    if not isinstance(raw, dict):
        return None
    cid = str(raw.get("id") or "").strip()
    if not cid:
        return None
    user = raw.get("user") or {}
    raw_text = raw.get("text") or ""
    pictures: list[str] = []
    cleaned = clean_comment_html(
        raw_text,
        emoji_replace=emoji_replace,
        link_preview=link_preview,
        pictures=pictures,
    )
    created_at, timestamp = parse_comment_time(str(raw.get("created_at") or ""))
    reply_target = "" if is_root else extract_reply_target(raw_text, cleaned)
    return Comment(
        id=cid,
        weibo_id=str(weibo_id),
        is_root=is_root,
        root_id=str(raw.get("rootid") or cid),
        user_id=str(user.get("id") or ""),
        user_name=str(user.get("screen_name") or ""),
        created_at=created_at,
        timestamp=timestamp,
        text=cleaned,
        like_count=_to_int(raw.get("like_count")),
        reply_target=reply_target,
        pic_url=_pick_pic_url(raw) or (pictures[0] if pictures else ""),
        floor=_to_int(raw.get("floor_number")),
        reply_total=_to_int(raw.get("total_number")),
    )


def _sleep(options: FetchOptions) -> None:
    low = max(0.0, float(options.delay_min or 0))
    high = max(low, float(options.delay_max or low))
    delay = low + (high - low) * max(0.0, min(1.0, options.rand()))
    if delay > 0:
        options.sleep(delay)


def _is_no_content_message(message: str) -> bool:
    """接口的 ``ok=0`` 业务提示是否表示「确实没有内容」。"""
    text = (message or "").strip()
    return bool(text) and any(hint in text for hint in _NO_CONTENT_HINTS)


def is_ambiguous_empty_hint(message: str) -> bool:
    """「暂无数据 / 暂无内容」可能是评论被删/不可见，也可能是临时空响应。

    这类结果按无评论处理以保证抓取继续，但需要写入待复核清单，稍后定向复查。
    """
    text = (message or "").strip()
    return any(hint in text for hint in ("暂无数据", "暂无内容"))


def _request_json(
    session,
    url: str,
    params: dict,
    headers: dict,
    options: FetchOptions,
    stats: FetchStats,
) -> tuple[Optional[dict], str]:
    """请求并解析 JSON；失败重试。

    返回 ``(payload, outcome)``。``payload`` 为 None 时，``outcome`` 说明原因：

    - ``_REQUEST_NO_CONTENT``：接口明确表示「没有内容」→ 属于「无评论」；
    - ``_REQUEST_BUDGET``：请求预算用尽，未发起请求；
    - ``_REQUEST_FAILED``：重试后仍失败（网络异常、HTTP 非 2xx、``ok != 1``）。

    只有拿到数据时 ``outcome`` 才是 ``_REQUEST_OK``。
    """
    for attempt in range(options.max_retries + 1):
        if stats.requests >= options.max_requests:
            return None, _REQUEST_BUDGET
        try:
            response = session.get(
                url, params=params, headers=headers, timeout=options.timeout
            )
            stats.requests += 1
            status = getattr(response, "status_code", 200)
            if status != 200:
                stats.note_error(f"HTTP {status} {url}")
            else:
                payload = response.json()
                if isinstance(payload, dict) and payload.get("ok") == 1:
                    _sleep(options)
                    return payload, _REQUEST_OK
                message = ""
                if isinstance(payload, dict):
                    message = str(payload.get("msg") or "")
                if _is_no_content_message(message):
                    # 业务层明确说明「还没有内容」：是「无评论」，不是失败，不再重试。
                    stats.empty_hint = message
                    return None, _REQUEST_NO_CONTENT
                if not message.strip():
                    # 实测旧微博评论被删/不可见时接口返回 ``{"ok": 0}``（无 msg）。
                    # 无法区分「确实无评论」与临时空响应：按无评论处理让抓取继续，
                    # 并记入「暂无数据待复核」清单，供 --recheck-empty 定向复查。
                    stats.empty_hint = "暂无数据（接口 ok=0 无提示）"
                    return None, _REQUEST_NO_CONTENT
                stats.note_error(f"接口未返回数据 {url} {message}".strip())
        except Exception as exc:  # 网络异常、JSON 解析失败等
            stats.note_error(f"{type(exc).__name__}: {exc}")
        if attempt < options.max_retries:
            _sleep(options)
    return None, _REQUEST_FAILED


def _sort_comments(items: list[Comment]) -> None:
    items.sort(key=lambda c: (c.timestamp if c.timestamp else float("inf"), c.id))


def fetch_comments(
    session,
    headers: dict,
    weibo_id: str,
    options: Optional[FetchOptions] = None,
) -> FetchResult:
    """抓取一条微博的评论（一级 + 二级回复），返回按时间升序排列的结果。"""
    options = options or FetchOptions()
    stats = FetchStats()
    roots: list[Comment] = []
    seen_ids: set[str] = set()
    total_number = 0
    failed = False

    # 1) 一级评论（热门流）
    max_id: Any = 0
    max_id_type: Any = 0
    while True:
        if options.max_root and len(roots) >= options.max_root:
            stats.stopped_reason = "root_limit"
            break
        if stats.requests >= options.max_requests:
            stats.stopped_reason = "request_budget"
            break
        params = {
            "id": str(weibo_id),
            "mid": str(weibo_id),
            "max_id_type": str(max_id_type),
        }
        if max_id:
            params["max_id"] = str(max_id)
        payload, outcome = _request_json(
            session, HOTFLOW_URL, params, headers, options, stats
        )
        if payload is None:
            if outcome == _REQUEST_NO_CONTENT:
                stats.stopped_reason = "empty"
            elif outcome == _REQUEST_BUDGET:
                stats.stopped_reason = "request_budget"
            else:
                stats.stopped_reason = "error"
                failed = True
            break
        page = payload.get("data") or {}
        if not isinstance(page, dict):
            stats.stopped_reason = "empty"
            break
        items = page.get("data") or []
        if not items:
            stats.stopped_reason = "empty"
            break
        total_number = max(total_number, _to_int(page.get("total_number")))
        for raw in items:
            comment = parse_comment(
                raw,
                weibo_id,
                is_root=True,
                emoji_replace=options.emoji_replace,
                link_preview=options.link_preview,
            )
            if comment is None or comment.id in seen_ids:
                continue
            if options.max_root and len(roots) >= options.max_root:
                stats.stopped_reason = "root_limit"
                break
            seen_ids.add(comment.id)
            roots.append(comment)
        if stats.stopped_reason == "root_limit":
            break
        max_id = page.get("max_id") or 0
        max_id_type = page.get("max_id_type") or 0
        if not max_id:
            stats.stopped_reason = "end"
            break

    # 2) 可选：最新流补充（按时间倒序，字段较少）
    if options.include_latest and stats.requests < options.max_requests:
        page_no = 1
        while True:
            if stats.requests >= options.max_requests:
                break
            payload, outcome = _request_json(
                session,
                LATEST_URL,
                {"id": str(weibo_id), "page": str(page_no)},
                headers,
                options,
                stats,
            )
            if payload is None:
                if outcome == _REQUEST_FAILED:
                    failed = True
                break
            data = payload.get("data") or {}
            items = data.get("data") if isinstance(data, dict) else None
            if not items:
                break
            for raw in items:
                comment = parse_comment(
                    raw,
                    weibo_id,
                    is_root=True,
                    emoji_replace=options.emoji_replace,
                    link_preview=options.link_preview,
                )
                if comment is None or comment.id in seen_ids:
                    continue
                if options.max_root and len(roots) >= options.max_root:
                    break
                seen_ids.add(comment.id)
                roots.append(comment)
                stats.latest_fetched += 1
            if options.max_root and len(roots) >= options.max_root:
                break
            page_no += 1

    # 3) 二级回复（楼中楼）；只对可能有回复的一级评论抓取
    reply_failed = False
    for root in roots:
        if options.max_reply and len(root.replies) >= options.max_reply:
            continue
        if root.reply_total <= 0 and not root.replies:
            # total_number 为 0 时仍可能有个别回复，但成本高；按 0 跳过。
            continue
        if stats.requests >= options.max_requests:
            stats.stopped_reason = "request_budget"
            break
        reply_max_id: Any = 0
        reply_max_type: Any = 0
        while True:
            if options.max_reply and len(root.replies) >= options.max_reply:
                break
            if stats.requests >= options.max_requests:
                stats.stopped_reason = "request_budget"
                break
            params = {
                "cid": root.id,
                "max_id": str(reply_max_id or 0),
                "max_id_type": str(reply_max_type or 0),
            }
            payload, outcome = _request_json(
                session, REPLY_URL, params, headers, options, stats
            )
            if payload is None:
                if outcome == _REQUEST_FAILED:
                    # 回复抓取失败：整条结果按「失败」处理，并尽快停止后续请求。
                    failed = True
                    reply_failed = True
                break
            items = payload.get("data") or []
            if not items:
                break
            for raw in items:
                reply = parse_comment(
                    raw,
                    weibo_id,
                    is_root=False,
                    emoji_replace=options.emoji_replace,
                    link_preview=options.link_preview,
                )
                if reply is None or reply.id == root.id:
                    continue
                if options.max_reply and len(root.replies) >= options.max_reply:
                    break
                # 同一楼内去重
                if any(existing.id == reply.id for existing in root.replies):
                    continue
                root.replies.append(reply)
            reply_max_id = payload.get("max_id") or 0
            reply_max_type = payload.get("max_id_type") or 0
            if not reply_max_id:
                break
        if reply_failed:
            break

    # 4) 排序与统计
    if failed:
        stats.stopped_reason = "error"
    roots.sort(key=lambda c: (c.timestamp if c.timestamp else float("inf"), c.id))
    for root in roots:
        _sort_comments(root.replies)
    stats.total_number = total_number
    stats.roots_fetched = len(roots)
    stats.replies_fetched = sum(len(root.replies) for root in roots)
    return FetchResult(
        weibo_id=str(weibo_id),
        total_number=total_number,
        roots=roots,
        stats=stats,
        failed=failed,
    )


def match_comment_user(comment: Comment, allowed_users) -> bool:
    """判断评论作者是否在白名单内（按用户 ID 或昵称精确匹配）。"""
    allowed = allowed_users
    if not allowed:
        return True
    if str(comment.user_id or "") in allowed:
        return True
    name = str(comment.user_name or "").strip()
    return bool(name and name in allowed)


def _clone_comment(comment: Comment, replies: list[Comment]) -> Comment:
    return Comment(
        id=comment.id,
        weibo_id=comment.weibo_id,
        is_root=comment.is_root,
        root_id=comment.root_id,
        user_id=comment.user_id,
        user_name=comment.user_name,
        created_at=comment.created_at,
        timestamp=comment.timestamp,
        text=comment.text,
        like_count=comment.like_count,
        reply_target=comment.reply_target,
        pic_url=comment.pic_url,
        pic_file=comment.pic_file,
        floor=comment.floor,
        reply_total=comment.reply_total,
        replies=list(replies),
    )


def filter_comments(result: FetchResult, allowed_users) -> FetchResult:
    """按评论者白名单过滤抓取结果。

    规则：
    - ``allowed_users`` 为空（未配置/该账号未配置）时原样返回；
    - 保留作者在白名单内的一级评论，以及其下作者在白名单内的二级回复；
    - 一级评论作者不在白名单、但其下有白名单作者的回复时，保留该一级评论
      作为上下文（否则回复无处挂载）；
    - 过滤后重新统计 roots_fetched / replies_fetched。

    ``allowed_users`` 中的元素可以是评论者用户 ID（字符串/数字）或昵称。
    """
    allowed = {str(item).strip() for item in (allowed_users or []) if str(item).strip()}
    if not allowed:
        return result
    roots: list[Comment] = []
    for root in result.roots:
        replies = [reply for reply in root.replies if match_comment_user(reply, allowed)]
        if not match_comment_user(root, allowed) and not replies:
            continue
        roots.append(_clone_comment(root, replies))
    stats = FetchStats(
        requests=result.stats.requests,
        errors=result.stats.errors,
        total_number=result.stats.total_number,
        roots_fetched=len(roots),
        replies_fetched=sum(len(root.replies) for root in roots),
        latest_fetched=result.stats.latest_fetched,
        stopped_reason=result.stats.stopped_reason,
        error_messages=list(result.stats.error_messages),
    )
    return FetchResult(
        weibo_id=result.weibo_id,
        total_number=result.total_number,
        roots=roots,
        stats=stats,
        failed=result.failed,
    )


def parse_user_filter_file(path: str) -> dict[str, set[str]]:
    """解析评论者白名单文本文件。

    每行格式：``<微博账号ID或昵称> <评论者1> [评论者2] ...``；
    空格或制表符分隔，``#`` 开头为注释，空行忽略。
    返回 ``{微博账号: {评论者, ...}}``；文件不存在或读取失败返回空字典。
    """
    mapping: dict[str, set[str]] = {}
    if not path or not os.path.isfile(path):
        return mapping
    try:
        with open(path, encoding="utf-8-sig") as handle:
            for line_no, raw_line in enumerate(handle, 1):
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.replace("\t", " ").split()
                if len(parts) < 2:
                    logger.warning(
                        "评论者白名单文件第 %d 行缺少评论者，已忽略: %s", line_no, line
                    )
                    continue
                account = parts[0]
                mapping.setdefault(account, set()).update(parts[1:])
    except OSError as exc:
        logger.warning("读取评论者白名单文件失败: %s", exc)
    return mapping
