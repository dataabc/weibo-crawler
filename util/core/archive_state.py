"""存档状态索引。

每个用户一份 ``archive_state_<用户ID>.json``，记录每条微博的档案状态：
发布时间、md 路径、首次/最后见到、最后编辑时间、内容指纹、可见性、
状态（active / suspect_invisible / isolated）、连续未抓到轮数、编辑快照列表。

设计原则：
- md 是内容真源，状态索引只是辅助；索引损坏/丢失可以重建（rebuild）；
- 写入走原子写（调用方传入 writer，一般为 Weibo._atomic_write_text）；
- 只记录元数据，不复制微博正文。
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime

from util.core.time_format import DTFORMAT

STATE_VERSION = 1
STATUS_ACTIVE = "active"
STATUS_SUSPECT = "suspect_invisible"
STATUS_ISOLATED = "isolated"


def now_str() -> str:
    return datetime.now().strftime(DTFORMAT)


def empty_state(user_id: str) -> dict:
    return {
        "version": STATE_VERSION,
        "user_id": str(user_id),
        "updated_at": now_str(),
        "weibos": {},
    }


def default_state_filename(user_id: str) -> str:
    return f"archive_state_{user_id}.json"


def resolve_state_path(user_dir: str, user_id: str, configured: str = "") -> str:
    """状态索引路径。

    ``configured`` 为空：``<用户目录>/archive_state_<用户ID>.json``；
    相对路径：相对项目根（调用方传绝对路径更稳）；绝对路径：原样。
    """
    value = (configured or "").strip()
    if not value:
        return os.path.join(user_dir, default_state_filename(user_id))
    return value


def load_state(path: str) -> tuple[dict, str]:
    """读取状态索引。

    返回 ``(state, warning)``；文件不存在返回空状态且 warning 为空；
    损坏时返回空状态并在 warning 里说明（不抛异常，调用方可记录并重建）。
    """
    if not path or not os.path.isfile(path):
        return empty_state(""), ""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (ValueError, OSError) as exc:
        return empty_state(""), f"状态索引读取失败（已按空状态处理）：{exc}"
    if not isinstance(data, dict):
        return empty_state(""), "状态索引根节点不是对象（已按空状态处理）"
    data.setdefault("version", STATE_VERSION)
    data.setdefault("user_id", "")
    data.setdefault("updated_at", now_str())
    weibos = data.get("weibos")
    if not isinstance(weibos, dict):
        data["weibos"] = {}
    return data, ""


def save_state(state: dict, path: str, writer) -> None:
    """原子写状态索引；``writer(path, text)`` 由调用方提供。"""
    state["updated_at"] = now_str()
    payload = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
    writer(path, payload)


def get_weibo(state: dict, weibo_id: str | int) -> dict | None:
    return state.get("weibos", {}).get(str(weibo_id))


def upsert_weibo(
    state: dict,
    weibo_id: str | int,
    *,
    created_at: str | None = None,
    md_path: str | None = None,
    content_hash: str | None = None,
    last_edit_at: str | None = None,
    visibility: dict | None = None,
    status: str | None = None,
    comment_refreshed_at: str | None = None,
    touch: bool = True,
) -> dict:
    """插入/更新一条微博状态，返回该条状态。

    只覆盖传入的字段；``touch=True`` 时更新 ``last_seen``。
    """
    weibos = state.setdefault("weibos", {})
    key = str(weibo_id)
    item = weibos.get(key)
    if not isinstance(item, dict):
        item = {
            "created_at": "",
            "md_path": "",
            "first_seen": now_str(),
            "last_seen": "",
            "last_edit_at": "",
            "content_hash": "",
            "visibility": {},
            "status": STATUS_ACTIVE,
            "missing_rounds": 0,
            "comment_refreshed_at": "",
            "versions": [],
        }
        weibos[key] = item
    if created_at is not None:
        item["created_at"] = created_at
    if md_path is not None:
        item["md_path"] = md_path
    if content_hash is not None:
        item["content_hash"] = content_hash
    if last_edit_at is not None:
        item["last_edit_at"] = last_edit_at
    if visibility is not None:
        item["visibility"] = visibility
    if status is not None:
        item["status"] = status
    if comment_refreshed_at is not None:
        item["comment_refreshed_at"] = comment_refreshed_at
    if touch:
        item["last_seen"] = now_str()
    return item


def set_status(state: dict, weibo_id: str | int, status: str) -> bool:
    item = get_weibo(state, weibo_id)
    if not item:
        return False
    changed = item.get("status") != status
    item["status"] = status
    if status == STATUS_ACTIVE:
        item["missing_rounds"] = 0
    return changed


def bump_missing(state: dict, weibo_id: str | int) -> int:
    item = get_weibo(state, weibo_id)
    if not item:
        return 0
    item["missing_rounds"] = int(item.get("missing_rounds") or 0) + 1
    return item["missing_rounds"]


def record_version(
    state: dict,
    weibo_id: str | int,
    *,
    fingerprint: str,
    saved_at: str,
    path: str,
) -> bool:
    """记录一个编辑快照版本；同一指纹已存在则跳过（幂等）。"""
    item = get_weibo(state, weibo_id)
    if not item:
        return False
    versions = item.setdefault("versions", [])
    for version in versions:
        if version.get("hash") == fingerprint:
            return False
    versions.append({"hash": fingerprint, "saved_at": saved_at, "path": path})
    return True


def normalize_visibility(visible) -> dict:
    """把接口的 ``visible`` 字段归一化为 {'type': int, 'list_id': int}。"""
    if not isinstance(visible, dict):
        return {}
    try:
        return {
            "type": int(visible.get("type") or 0),
            "list_id": int(visible.get("list_id") or 0),
        }
    except (TypeError, ValueError):
        return {}


def visibility_label(visibility: dict | None) -> str:
    """把可见性归一化值转成人可读标签（用于报告）。"""
    if not visibility:
        return "未知"
    vtype = visibility.get("type")
    list_id = visibility.get("list_id") or 0
    labels = {
        0: "公开",
        1: "仅自己可见",
        2: "好友圈",
        3: "粉丝可见",
    }
    if vtype in labels:
        return labels[vtype]
    if vtype == 6 or list_id:
        return f"分组可见（list_id={list_id}）"
    return f"类型 {vtype}"


def normalize_content_url(url: str) -> str:
    """归一化内容里的媒体 URL：去掉查询串与锚点，只保留 scheme/host/path。

    现场实测：微博媒体 URL（视频、Live Photo）常带 ``Expires`` / ``ssig`` 等
    时效签名参数，每次抓取都可能不同；若直接进指纹，会把「URL 参数变化」误判
    成「微博被编辑」。分号分隔的多 URL（图片列表）逐个归一化后再拼回。

    注意：只用于指纹等「内容身份」比较，不用于实际下载/写链接（下载仍用原始 URL）。
    """
    text = str(url or "").strip()
    if not text:
        return ""
    normalized = []
    for piece in text.split(";"):
        item = piece.strip()
        if not item:
            continue
        item = item.split("?", 1)[0].split("#", 1)[0]
        normalized.append(item)
    return ";".join(normalized)


def content_fingerprint(
    *,
    text: str = "",
    pics: str = "",
    video_url: str = "",
    live_photo_url: str = "",
    article_url: str = "",
    topics: str = "",
    edit_at: str = "",
    visible: str = "",
) -> str:
    """内容指纹：只包含「编辑相关」的稳定内容字段（不含点赞/评论/转发数）。

    易变字段不参与指纹，避免接口字段波动被误判为编辑：
    - ``edit_at``：列表接口时有时无（详情接口变动），且平台编辑时间并不代表
      正文一定变化，保留参数仅为兼容旧调用，计算时忽略；
    - ``pics`` / ``video_url`` / ``live_photo_url``：去掉带时效签名的查询串，
      只比较媒体路径身份。

    指纹变化只作为「疑似编辑」的第一道信号，调用方还需用正文/媒体/链接的
    内容签名做二次确认（见 ``util/edit/edit_updates.py``）。
    """
    payload = "\n".join(
        [
            str(text or "").strip(),
            normalize_content_url(pics),
            normalize_content_url(video_url),
            normalize_content_url(live_photo_url),
            str(article_url or "").strip(),
            str(topics or "").strip(),
            str(visible or "").strip(),
        ]
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
