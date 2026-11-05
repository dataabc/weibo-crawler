#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""微博评论全量回填脚本。

对已存档 Markdown 里的微博逐条抓取评论（一级评论 + 二级回复），把评论块
写回对应微博块。支持全量/按用户/按时间范围，分批执行、断点续跑、dry-run。

用法示例::

    python scripts/weibo_comment_backfill.py --dry-run                 # 只看规模，不写文件
    python scripts/weibo_comment_backfill.py --max-weibo 20            # 先小批试跑
    python scripts/weibo_comment_backfill.py                    # 全量回填（默认全量）
    python scripts/weibo_comment_backfill.py --users 用户A --since <起始日期>
    python scripts/weibo_comment_backfill.py --resume           # 断点续跑（默认）

安全开关：config.json 中 ``weibo_comment_backfill`` 必须为 1，否则直接退出。

并发与安全：
- 与主爬（weibo.py）共用 ``<output_directory>/.weibo_crawler.lock`` 互斥，
  避免两者同时改写同一批 markdown（整文件读改写会互相覆盖）；
- 备份默认关闭（``comment_markdown_backup=0``）；需要回滚保护时设为 1，
  同一次运行内同一个 md 文件只生成一份 ``.bak.<时间戳>``；
- Ctrl-C 会保存进度并生成「已中断」报告（退出码 2），下次直接重跑即可续跑；
- 失败项（HTTP/网络/风控）不写断点，下次运行自动重试。"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime

import requests

# 脚本位于 scripts/，项目根目录是其上一级；把项目根加入 sys.path 以导入 util 与 weibo
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from util.core import archive_index
from util.core import file_lock
from util.comment import comment_fetch
from util.comment import comment_markdown
from util.comment import comment_emoji
from util.comment import comment_backfill as comment_merge
from util.core import markdown_blocks
from util.config import config_migrate
from util.config import config_paths
from util.media import media_check
from util.core import atomic_io  # noqa: E402
from util.core.time_format import DTFORMAT  # 单一来源（L-04）
PROGRESS_EVERY = 10
# 覆盖两类备份命名：
# - 主爬/编辑更新：<md>.bak.YYYYMMDD[_N]（同一天多份编号递增）
# - 评论写入：<md>.bak.YYYYMMDD_HHMMSS[_N]
BACKUP_RE = re.compile(r"\.md\.bak\.\d{8}(?:_\d{6})?(?:_\d+)?$")

HEADERS = {
    "Referer": "https://m.weibo.cn/",
    "accept": "application/json, text/plain, */*",
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0"
    ),
    "x-requested-with": "XMLHttpRequest",
    "mweibo-pwa": "1",
}


def _logger():
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    return logging.getLogger("comment_backfill")


logger = _logger()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="微博评论全量回填")
    parser.add_argument(
        "--config",
        default=config_paths.config_files(ROOT_DIR)[0],
        help="配置文件路径",
    )
    parser.add_argument("--users", nargs="*", default=None, help="只处理这些用户目录（目录名包含匹配）")
    parser.add_argument("--since", default=None, help="微博发布时间下限（YYYY-MM-DD）")
    parser.add_argument("--until", default=None, help="微博发布时间上限（YYYY-MM-DD）")
    parser.add_argument("--max-weibo", type=int, default=0, help="本次最多处理多少条微博；0 = 不限制")
    parser.add_argument("--limit-comments", type=int, default=None, help="覆盖 comment_max_download_count")
    parser.add_argument("--limit-replies", type=int, default=None, help="覆盖 comment_reply_max_download_count")
    parser.add_argument("--batch-size", type=int, default=50, help="每批处理多少条微博后休息")
    parser.add_argument("--batch-rest", type=float, default=60.0, help="批间休息秒数")
    parser.add_argument("--max-consecutive-errors", type=int, default=5, help="连续失败多少次后停止")
    parser.add_argument("--dry-run", action="store_true", help="只统计与预览，不请求接口、不写文件")
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="配置 comment_markdown_backup=1 开启备份时，本次运行不生成 .bak",
    )
    parser.add_argument(
        "--clean-backups",
        action="store_true",
        help="删除由本功能生成的 .bak 备份（*.md.bak.<时间戳>），不执行回填",
    )
    parser.add_argument("--no-resume", action="store_true", help="不复用断点进度，从头开始")
    parser.add_argument("--force", action="store_true", help="忽略进度记录，重抓已处理微博")
    parser.add_argument(
        "--recheck-empty",
        action="store_true",
        help="定向复查「暂无数据待复核」清单里的微博（忽略断点，只处理清单内条目）",
    )
    parser.add_argument("--report-dir", default=None, help="覆盖回填报告与进度目录")
    return parser.parse_args(argv)


def _parse_day(value: str | None, end_of_day: bool = False):
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        sys.exit("日期格式应为 YYYY-MM-DD: %s" % value)
    if end_of_day:
        parsed = parsed.replace(hour=23, minute=59, second=59)
    return parsed


def load_config(path: str) -> dict:
    if not os.path.isfile(path):
        sys.exit("找不到配置文件: %s" % path)
    return config_migrate.load_config_file(path)


def build_session(config: dict):
    cookie = str(config.get("cookie") or "")
    session = requests.Session()
    for pair in cookie.split(";"):
        if "=" in pair:
            key, value = pair.split("=", 1)
            session.cookies.set(key.strip(), value.strip(), domain=".weibo.cn")
    try:
        session.get("https://m.weibo.cn", headers=HEADERS, timeout=10)
    except Exception as exc:  # noqa: BLE001 兜底：失败不阻断
        logger.warning("session 预热失败（继续）: %s", exc)
    return session

def _report_dir(
    config: dict, override: str | None, key: str, create: bool = True
) -> str:
    output_dir = file_lock.resolve_output_dir(ROOT_DIR, config.get("output_directory"))
    value = override or config.get(key) or "report"
    if not os.path.isabs(value):
        value = os.path.join(output_dir, value)
    if create:
        os.makedirs(value, exist_ok=True)
    return value


def _has_md(path: str) -> bool:
    for root, _dirs, files in os.walk(path):
        depth = root[len(path):].count(os.sep)
        if depth > 2:
            continue
        for name in files:
            if name.endswith(".md") and not name.startswith("._"):
                return True
    return False


def iter_user_dirs(output_dir: str, exclude_names, only_users=None):
    for name in sorted(os.listdir(output_dir)):
        path = os.path.join(output_dir, name)
        if not os.path.isdir(path) or name.startswith(".") or name in exclude_names:
            continue
        if only_users and not any(user in name for user in only_users):
            continue
        if _has_md(path):
            yield name, path


def load_progress(path: str) -> dict:
    if not os.path.isfile(path):
        return {"done": []}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            return {"done": []}
        data.setdefault("done", [])
        return data
    except (OSError, ValueError):
        return {"done": []}


def _config_fingerprint(config: dict) -> dict:
    """影响回填口径的配置摘要，用于检测「断点来自另一套配置」。"""
    md_dir = config.get("markdown_file_dir") or {}
    return {
        "max_root": int(config.get("comment_max_download_count", 0) or 0),
        "max_reply": int(config.get("comment_reply_max_download_count", 0) or 0),
        "filter_file": str(config.get("comment_user_filter_file") or ""),
        "filter_inline": bool(config.get("comment_user_filter")),
        "emoji": bool(config.get("comment_emoji_replace", 1)),
        "link_preview": bool(
            (config.get("markdown_preview") or {}).get("link_preview", 0)
        ),
        "show_like": bool(config.get("comment_show_like_count", 0)),
        "show_reply_target": bool(config.get("comment_show_reply_target", 1)),
        "comment_image_dir": (
            md_dir.get("comment_image_dir") if isinstance(md_dir, dict) else None
        ),
    }


def save_progress(
    path: str, done: set[str], stats: dict, config_fingerprint: dict | None = None
) -> None:
    payload = {
        "updated_at": datetime.now().strftime(DTFORMAT),
        "done": sorted(done),
        "stats": stats,
    }
    if config_fingerprint is not None:
        payload["config"] = config_fingerprint
    atomic_io.atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


EMPTY_PENDING_NAME = "comment_empty_data_pending.json"


def empty_pending_path(report_dir: str) -> str:
    return os.path.join(report_dir, EMPTY_PENDING_NAME)


def load_empty_pending(path: str) -> dict[str, dict]:
    """读取「暂无数据待复核」清单；返回 {weibo_id: entry}。"""
    if not os.path.isfile(path):
        return {}
    try:
        data = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, dict):
        return {}
    return {
        str(key): dict(value)
        for key, value in items.items()
        if isinstance(value, dict)
    }


def save_empty_pending(path: str, items: dict[str, dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "updated_at": datetime.now().strftime(DTFORMAT),
        "items": items,
    }
    atomic_io.atomic_write_text(
        path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    )


def upsert_empty_pending(items: dict[str, dict], record: dict) -> dict:
    """把一条「暂无数据」结果写入待复核清单（attempts+1）。"""
    weibo_id = str(record.get("weibo_id") or "")
    if not weibo_id:
        return {}
    now = datetime.now().strftime(DTFORMAT)
    previous = items.get(weibo_id) or {}
    entry = {
        "user": str(record.get("user") or ""),
        "weibo_id": weibo_id,
        "created_at": str(record.get("created_at") or ""),
        "md_path": str(record.get("md_path") or ""),
        "hint": str(record.get("empty_hint") or ""),
        "attempts": int(previous.get("attempts", 0) or 0) + 1,
        "first_seen": str(previous.get("first_seen") or now),
        "last_checked": now,
    }
    items[weibo_id] = entry
    return entry


def clear_empty_pending(items: dict[str, dict], weibo_id) -> bool:
    return items.pop(str(weibo_id), None) is not None


INVISIBLE_LIST_NAME = "comment_invisible_weibos.json"


def invisible_list_path(report_dir: str) -> str:
    return os.path.join(report_dir, INVISIBLE_LIST_NAME)


def load_invisible_list(path: str) -> dict[str, dict]:
    return load_empty_pending(path)


def save_invisible_list(path: str, items: dict[str, dict]) -> None:
    save_empty_pending(path, items)


def upsert_invisible(items: dict[str, dict], record: dict) -> dict:
    weibo_id = str(record.get("weibo_id") or "")
    if not weibo_id:
        return {}
    now = datetime.now().strftime(DTFORMAT)
    previous = items.get(weibo_id) or {}
    entry = {
        "user": str(record.get("user") or ""),
        "weibo_id": weibo_id,
        "created_at": str(record.get("created_at") or ""),
        "md_path": str(record.get("md_path") or ""),
        "reason": str(record.get("invisible_reason") or record.get("empty_hint") or ""),
        "first_seen": str(previous.get("first_seen") or now),
        "last_checked": now,
    }
    items[weibo_id] = entry
    return entry


def collect_items(output_dir, exclude_names, only_users, since_dt, until_dt):
    items = []
    for user_name, user_dir in iter_user_dirs(output_dir, exclude_names, only_users):
        entries, scan_stats = archive_index.collect_archive_entries(
            user_dir, since_dt, until_dt, exclude_names
        )
        for weibo_id, (created_at, md_path) in entries.items():
            items.append(
                {
                    "user": user_name,
                    "weibo_id": weibo_id,
                    "created_at": created_at,
                    "md_path": md_path,
                }
            )
        logger.info(
            "扫描用户目录 %s：%d 个微博块（扫描 %d 个文件）",
            user_name,
            len(entries),
            scan_stats.files_scanned,
        )
    items.sort(key=lambda item: (item["created_at"] or datetime.min, item["weibo_id"]))
    return items


def load_user_id_map(output_dir: str) -> dict[str, str]:
    """读取 users.csv，返回 {昵称或用户ID: 用户ID} 映射（用于账号匹配）。"""
    mapping: dict[str, str] = {}
    path = os.path.join(output_dir, "users.csv")
    if not os.path.isfile(path):
        return mapping
    try:
        with open(path, encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                user_id = str(row.get("用户id") or "").strip()
                nickname = str(row.get("昵称") or "").strip()
                if user_id:
                    mapping[user_id] = user_id
                if nickname and user_id:
                    mapping[nickname] = user_id
    except OSError:
        pass
    return mapping


def load_comment_user_filter(config: dict) -> dict[str, set[str]]:
    """加载评论者白名单：外部 txt 优先，文件不存在或为空时回退 config 内联配置。"""
    file_name = str(config.get("comment_user_filter_file") or "").strip()
    if file_name:
        path = config_paths.resolve_config_file(ROOT_DIR, file_name)
        mapping = comment_fetch.parse_user_filter_file(path)
        if mapping:
            logger.info("评论者白名单：从文件 %s 加载 %d 个账号", path, len(mapping))
            return mapping
    raw = config.get("comment_user_filter")
    mapping: dict[str, set[str]] = {}
    if isinstance(raw, dict):
        for account_key, allowed_users in raw.items():
            if not isinstance(allowed_users, (list, tuple)):
                continue
            allowed = {str(item).strip() for item in allowed_users if str(item).strip()}
            key_text = str(account_key).strip()
            if key_text and allowed:
                mapping[key_text] = allowed
        if mapping:
            logger.info("评论者白名单：使用 config 内联配置，%d 个账号", len(mapping))
    return mapping


def resolve_allowed_users(filter_conf, user_name: str, user_id_map: dict[str, str]):
    """按微博账号解析评论者白名单；账号未配置或数组为空时返回 None（不过滤）。"""
    if not isinstance(filter_conf, dict) or not filter_conf:
        return None
    candidates = {str(user_name or "").strip()}
    mapped_id = user_id_map.get(str(user_name or "").strip())
    if mapped_id:
        candidates.add(mapped_id)
    for account_key, allowed_users in filter_conf.items():
        if str(account_key).strip() not in candidates:
            continue
        # 白名单文本文件解析出来是 set；config 内联配置是 list/tuple，两种都要认
        if isinstance(allowed_users, (set, list, tuple)):
            allowed = {
                str(item).strip() for item in allowed_users if str(item).strip()
            }
            if allowed:
                return allowed
        return None
    return None


def download_comment_images(session, result, md_path: str, image_dir_name: str):
    image_dir = os.path.join(os.path.dirname(md_path), image_dir_name)
    comments = []
    for root in result.roots:
        comments.append(root)
        comments.extend(root.replies)
    comments = [comment for comment in comments if comment.pic_url]
    if not comments:
        return  # 没有评论图片时不创建空目录
    os.makedirs(image_dir, exist_ok=True)
    for comment in comments:
        stamp = (comment.created_at or "unknown").replace(" ", "_").replace(":", "-")
        target = os.path.join(image_dir, "%s.jpg" % stamp)
        try:
            response = session.get(comment.pic_url, headers=HEADERS, timeout=20)
            if response.status_code != 200:
                continue
            data = response.content
            ok, _reason, _kind, ext = media_check.validate_bytes(
                data, comment.pic_url, response.headers.get("Content-Type")
            )
            if not ok:
                continue
            if ext and not target.lower().endswith(ext):
                target = target[: -len(".jpg")] + ext
            atomic_io.atomic_write_bytes(target, data)
            comment.pic_file = os.path.basename(target)
        except Exception as exc:  # noqa: BLE001 兜底：失败不阻断
            logger.debug("评论图片下载失败 %s: %s", comment.pic_url, exc)


def fetch_failure_note(result) -> str:
    """把抓取失败的原因压成一行，供报告与日志使用。"""
    messages = getattr(result.stats, "error_messages", None) or []
    if messages:
        return messages[-1]
    return "停止原因: %s" % (result.stats.stopped_reason or "未知")


CID_RE = re.compile(r"<!-- cid: (\d+) -->")
COMMENT_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def _comment_entries(groups) -> list[dict]:
    """把评论组解析成 {cid, date, raw}，用于新旧差集与报告。"""
    entries = []
    for group in groups or []:
        raw = str(getattr(group, "raw", "") or "")
        for line in raw.splitlines():
            match = CID_RE.search(line)
            if not match:
                continue
            date_match = COMMENT_DATE_RE.search(line)
            entries.append(
                {
                    "cid": match.group(1),
                    "date": date_match.group(1) if date_match else "",
                    "raw": raw,
                }
            )
    return entries


def _old_comment_groups(original_text: str, md_path: str, weibo_id: str):
    fallback = os.path.splitext(os.path.basename(md_path))[0]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", fallback):
        fallback = None
    try:
        doc = markdown_blocks.parse_markdown(original_text, fallback_date=fallback)
        block = doc.find(weibo_id)
    except Exception:  # noqa: BLE001 兜底：解析失败按无旧块处理
        return []
    return list(getattr(block, "comments", []) or []) if block is not None else []


def _diff_comments(old_groups, new_groups):
    old_entries = _comment_entries(old_groups)
    new_entries = _comment_entries(new_groups)
    old_by_cid = {item["cid"]: item for item in old_entries}
    new_by_cid = {item["cid"]: item for item in new_entries}
    added = [new_by_cid[cid] for cid in new_by_cid if cid not in old_by_cid]
    updated = [
        new_by_cid[cid]
        for cid in new_by_cid
        if cid in old_by_cid and new_by_cid[cid]["raw"] != old_by_cid[cid]["raw"]
    ]
    return old_entries, new_entries, added, updated


def build_record(item, result, allowed_users) -> dict:
    """汇总一条微博的处理记录（status 由调用方填写）。"""
    return {
        **item,
        "status": "",
        "total_number": result.total_number,
        "roots": len(result.roots),
        "replies": result.stats.replies_fetched,
        "requests": result.stats.requests,
        "errors": result.stats.errors,
        "note": result.stats.stopped_reason,
        "filtered_users": len(allowed_users) if allowed_users else 0,
    }


def process_one(
    session, headers, item, options, render_options, backup, dry_run, pic_download,
    allowed_users=None, backup_state=None, emoji_store=None,
):
    result = comment_fetch.fetch_comments(
        session, headers, item["weibo_id"], options
    )
    if result.failed:
        # 抓取失败（网络/HTTP/接口异常）不是「无评论」：标记失败，由主循环
        # 计数并在下次运行时重试，绝不能写进断点。
        record = build_record(item, result, allowed_users)
        record["status"] = "失败"
        record["note"] = "抓取失败: %s" % fetch_failure_note(result)
        return record
    if allowed_users:
        result = comment_fetch.filter_comments(result, allowed_users)
    record = build_record(item, result, allowed_users)
    if not result.roots:
        hint = str(getattr(result.stats, "empty_hint", "") or "")
        if hint and comment_fetch.is_ambiguous_empty_hint(hint):
            # 可能是临时空响应/限频抖动，也可能是微博本身被删/隐藏：先核验微博状态。
            kind, reason = comment_fetch.check_weibo_visibility(
                session, headers, item["weibo_id"]
            )
            if kind in ("deleted", "restricted"):
                record["status"] = "已不可见"
                record["invisible_reason"] = reason or kind
                record["note"] = "微博已删除/不可见（%s），不再重试评论" % record["invisible_reason"]
                return record
            # 微博可访问或无法判断：不能标「已完成」，状态记为待复核，下次再试
            record["status"] = "待复核"
            record["empty_hint"] = hint
            record["note"] = "接口空响应（微博可访问），待再次抓取"
            return record

        try:
            original_text = open(item["md_path"], encoding="utf-8").read()
        except OSError:
            original_text = ""
        old_groups = _old_comment_groups(
            original_text, item["md_path"], item["weibo_id"]
        )
        if old_groups:
            record["status"] = "未抓到"
            record["old_comment_count"] = len(_comment_entries(old_groups))
            record["note"] = "本次未抓到新评论，旧评论块保留"
            return record
        record["status"] = "无评论"
        if allowed_users:
            record["note"] = "按评论者白名单过滤后无内容"
        return record
    if pic_download and not dry_run:
        download_comment_images(session, result, item["md_path"], render_options.image_dir)
    block = comment_markdown.render_comment_block(
        item["weibo_id"],
        result.roots,
        total_number=result.total_number,
        options=render_options,
    )
    try:
        original_text = open(item["md_path"], encoding="utf-8").read()
    except OSError:
        original_text = ""
    old_groups = _old_comment_groups(original_text, item["md_path"], item["weibo_id"])
    new_groups = markdown_blocks.parse_comments_block(block)[1]
    old_entries, new_entries, added, updated = _diff_comments(old_groups, new_groups)
    # 与已有评论块按 cid 合并（旧评论不丢，新评论追加/更新）；新抓取为空时保留旧块。
    merged_block = comment_merge.merge_comment_block(
        item["md_path"], item["weibo_id"], block, original_text=original_text
    )
    merged_block = comment_markdown.sort_comment_block(merged_block)
    changed, update_status = comment_markdown.update_markdown_file_status(
        item["md_path"], item["weibo_id"], merged_block, backup=backup, dry_run=dry_run,
        backup_state=backup_state, original_text=original_text, emoji_store=emoji_store,
    )
    if update_status == comment_markdown.UPDATE_MISSING:
        # 微博块被人工改动/删除：不能静默当「未变化」
        record["status"] = "跳过"
        record["note"] = "未找到微博块（文件可能被人工修改）"
        return record
    record["weibo_date"] = str(item.get("created_at") or "")
    record["old_comment_count"] = len(old_entries)
    record["new_comment_count"] = len(new_entries)
    record["added_comments"] = added
    record["updated_comments"] = updated
    if changed:
        record["status"] = "已写入"
        record["change_kind"] = "新抓到" if not old_entries else "已更新"
    else:
        record["status"] = "未抓到" if old_entries else "无评论"
        record["note"] = "本次未产生新增/更新，旧评论块保留" if old_entries else ""
    return record


def clean_backups(output_dir: str, dry_run: bool = False) -> tuple[int, int]:
    """删除由本功能生成的 ``*.md.bak.<时间戳>`` 备份；返回 (数量, 字节数)。"""
    count = 0
    total_bytes = 0
    for root, _dirs, files in os.walk(output_dir):
        for name in files:
            if not BACKUP_RE.search(name):
                continue
            path = os.path.join(root, name)
            count += 1
            try:
                total_bytes += os.path.getsize(path)
            except OSError:
                pass
            if not dry_run:
                try:
                    os.remove(path)
                except OSError:
                    pass
    return count, total_bytes


def write_report(
    report_dir: str,
    stats: dict,
    results: list[dict],
    elapsed: float,
    empty_pending: dict | None = None,
) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(report_dir, "weibo_comment_backfill_%s.txt" % timestamp)
    lines = [
        "评论回填报告（全量回填脚本）",
        "生成时间: %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "耗时: %.1f 秒" % elapsed,
        "计划处理: %d 条微博" % stats.get("planned", 0),
        "本次处理: %d 条" % stats.get("processed", 0),
        "已写入: %d 条" % stats.get("written", 0),
        "无评论: %d 条" % stats.get("empty", 0),
        "待复核（接口空响应，未写断点，下次会重试）: %d 条"
        % stats.get("empty_pending", 0),
        "已删除/不可见（不再重试评论）: %d 条" % stats.get("invisible", 0),
        "不可见清单: %s" % invisible_list_path(report_dir),
        "新抓到评论块: %d 条" % stats.get("new_caught", 0),
        "更新评论块: %d 条" % stats.get("updated_blocks", 0),
        "未抓到（旧评论块保留）: %d 条" % stats.get("not_fetched", 0),
        "未变化: %d 条" % stats.get("unchanged", 0),
        "失败: %d 条" % stats.get("failed", 0),
        "暂无数据待复核: 剩余 %d 条（本次新增 %d，已复核清除 %d）"
        % (
            stats.get("empty_pending_total", len(empty_pending or {})),
            stats.get("empty_pending_new", 0),
            stats.get("empty_recheck_cleared", 0),
        ),
        "暂无数据待复核清单: %s" % empty_pending_path(report_dir),
        "中断: %s"
        % (
            "是（进度已保存，可用 --resume 继续）"
            if stats.get("interrupted")
            else "否"
        ),
        "跳过（断点已完成）: %d 条" % stats.get("skipped", 0),
        "跳过（微博块缺失，文件可能被人工修改）: %d 条"
        % stats.get("missing_block", 0),
        "已存档评论: %d 条一级 + %d 条二级回复" % (stats.get("roots", 0), stats.get("replies", 0)),
        "评论请求: %d 次，请求错误: %d 次" % (stats.get("requests", 0), stats.get("errors", 0)),
        "评论者白名单过滤: %s"
        % (
            "已启用（%d 条微博应用）" % stats.get("filtered_weibo", 0)
            if stats.get("filtered_weibo")
            else "未启用"
        ),
        "说明: 微博评论接口存在上限，已存档条数可能明显少于微博显示的评论总数；"
        "评论块里已标注「已存档 / 总数」。",
        "-" * 70,
    ]
    if empty_pending:
        lines.append("-" * 70)
        lines.append("暂无数据待复核清单（接口返回「暂无数据/暂无内容」，可用 --recheck-empty 定向复查）:")
        for weibo_id, entry in sorted(empty_pending.items())[:100]:
            lines.append(
                "待复核 | 用户: %s | 微博id: %s | 提示: %s | 复查次数: %s"
                % (
                    entry.get("user", ""),
                    weibo_id,
                    entry.get("hint", ""),
                    entry.get("attempts", 0),
                )
            )
        if len(empty_pending) > 100:
            lines.append("...（其余 %d 条见清单文件）" % (len(empty_pending) - 100))
    new_caught = [
        item for item in results
        if item["status"] == "已写入" and item.get("change_kind") == "新抓到"
    ]
    updated_blocks = [
        item for item in results
        if item["status"] == "已写入" and item.get("change_kind") == "已更新"
    ]
    not_fetched = [item for item in results if item["status"] == "未抓到"]

    def _fmt_comments(entries):
        parts = []
        for entry in entries or []:
            cid = entry.get("cid") or "无cid"
            date = entry.get("date") or "日期未知"
            parts.append("%s(%s)" % (cid, date))
        return "、".join(parts) if parts else "无"

    if new_caught:
        lines.append("-" * 70)
        lines.append("本次新抓到的评论块:")
        for item in new_caught:
            lines.append(
                "新抓到 | 用户: %s | 微博id: %s | 日期: %s | 新评论: %s"
                % (
                    item.get("user", ""),
                    item.get("weibo_id", ""),
                    item.get("weibo_date") or item.get("created_at", ""),
                    _fmt_comments(item.get("added_comments")),
                )
            )
    if updated_blocks:
        lines.append("-" * 70)
        lines.append("本次更新的评论块:")
        for item in updated_blocks:
            lines.append(
                "已更新 | 用户: %s | 微博id: %s | 日期: %s | 新增: %s | 更新: %s | 旧评论数: %d"
                % (
                    item.get("user", ""),
                    item.get("weibo_id", ""),
                    item.get("weibo_date") or item.get("created_at", ""),
                    _fmt_comments(item.get("added_comments")),
                    _fmt_comments(item.get("updated_comments")),
                    item.get("old_comment_count", 0),
                )
            )
    if not_fetched:
        lines.append("-" * 70)
        lines.append("本次未抓到的评论块（旧块保留）:")
        for item in not_fetched:
            lines.append(
                "未抓到 | 用户: %s | 微博id: %s | 日期: %s | 旧评论数: %d | %s"
                % (
                    item.get("user", ""),
                    item.get("weibo_id", ""),
                    item.get("weibo_date") or item.get("created_at", ""),
                    item.get("old_comment_count", 0),
                    item.get("note", ""),
                )
            )

    failed = [item for item in results if item["status"] == "失败"]
    for item in failed:
        lines.append(
            "失败 | 用户: %s | 微博id: %s | %s"
            % (item["user"], item["weibo_id"], item.get("note", ""))
        )
    atomic_io.atomic_write_text(path, "\n".join(lines) + "\n")
    return path


def _run_backfill(argv=None) -> int:
    """回填主体（已完成开关检查与并发锁检查）。"""
    args = parse_args(argv)
    config = load_config(args.config)

    if int(config.get("weibo_comment_backfill", 0) or 0) != 1:
        logger.error(
            "配置中 weibo_comment_backfill=0（默认关闭）；如需回填，请把该配置改为 1 后重试"
        )
        return 1

    output_dir = str(config.get("output_directory") or "weibo_data")
    if not os.path.isabs(output_dir):
        output_dir = os.path.join(ROOT_DIR, output_dir)
    if not os.path.isdir(output_dir):
        logger.error("输出目录不存在: %s", output_dir)
        return 1

    if args.clean_backups:
        count, total_bytes = clean_backups(output_dir, dry_run=args.dry_run)
        action = "将删除" if args.dry_run else "已删除"
        print(
            "%s %d 个 .bak 备份，共 %.2f MB%s"
            % (
                action,
                count,
                total_bytes / 1024 / 1024,
                "（dry-run 预览，未实际删除）" if args.dry_run else "",
            )
        )
        return 0

    report_dir = _report_dir(
        config,
        args.report_dir,
        "weibo_comment_backfill_dir",
        create=not args.dry_run,
    )
    exclude_dirs = tuple(
        set(config.get("invisible_weibo_dirs") or ())
        | set(archive_index.DEFAULT_EXCLUDE_DIRS)
    )
    since_dt = _parse_day(args.since)
    until_dt = _parse_day(args.until, end_of_day=True)

    pending_file = empty_pending_path(report_dir)

    if args.recheck_empty:
        saved_pending = load_empty_pending(pending_file)
        recheck_items = []
        for weibo_id, entry in saved_pending.items():
            user = str(entry.get("user") or "")
            if args.users and not any(
                u == user or u in user or user in u for u in args.users
            ):
                continue
            recheck_items.append(
                {
                    "user": user,
                    "weibo_id": str(weibo_id),
                    "created_at": str(entry.get("created_at") or ""),
                    "md_path": str(entry.get("md_path") or ""),
                }
            )
        if not recheck_items:
            logger.info("暂无数据待复核清单为空或没有匹配用户：%s", pending_file)
            return 0
        recheck_items.sort(key=lambda item: (item["created_at"] or "", item["weibo_id"]))
        items = recheck_items
        fingerprint = _config_fingerprint(config)
        user_done = {}
        user_progress_path = {}
        for item in items:
            user = item["user"]
            if user in user_progress_path:
                continue
            safe_user = re.sub(r'[\\/:*?"<>|]', "_", user)
            progress_path = os.path.join(
                report_dir, safe_user, "comment_backfill_progress.json"
            )
            user_progress_path[user] = progress_path
            user_done[user] = set(load_progress(progress_path).get("done", []))
        pending = list(items)
        logger.info(
            "定向复查（暂无数据待复核）: %d 条；清单文件: %s",
            len(pending),
            pending_file,
        )
    else:
        items = collect_items(
            output_dir,
            exclude_dirs,
            args.users,
            since_dt,
            until_dt,
        )
        if not items:
            logger.info("没有符合条件的微博，退出")
            return 0

        # 按用户目录维护进度文件（dry-run 也读取，便于显示真实待处理数）
        user_done: dict[str, set[str]] = {}
        user_progress_path: dict[str, str] = {}
        fingerprint = _config_fingerprint(config)
        for item in items:
            user = item["user"]
            if user in user_progress_path:
                continue
            safe_user = re.sub(r'[\\/:*?"<>|]', "_", user)
            user_report_dir = os.path.join(report_dir, safe_user)
            path = os.path.join(user_report_dir, "comment_backfill_progress.json")
            user_progress_path[user] = path
            # 兼容旧版平铺路径：<report>/comment_backfill_progress_<用户>.json
            legacy_path = os.path.join(
                report_dir, "comment_backfill_progress_%s.json" % safe_user
            )
            load_path = path if os.path.isfile(path) else legacy_path
            progress = load_progress(load_path)
            saved_fingerprint = progress.get("config")
            if isinstance(saved_fingerprint, dict) and saved_fingerprint != fingerprint:
                changed_keys = [
                    key
                    for key in fingerprint
                    if saved_fingerprint.get(key) != fingerprint.get(key)
                ]
                logger.warning(
                    "断点文件 %s 来自不同配置（%s）；旧断点可能基于旧口径，"
                    "如需按新配置重扫请加 --force 或删除该进度文件",
                    path,
                    ", ".join(changed_keys) or "未知项",
                )
            user_done[user] = set() if (args.no_resume or args.force) else set(progress.get("done", []))

        pending = [
            item
            for item in items
            if args.force or item["weibo_id"] not in user_done[item["user"]]
        ]
        if args.max_weibo and args.max_weibo > 0:
            pending = pending[: args.max_weibo]
        logger.info(
            "待处理微博: %d 条（全部 %d 条，断点已完成 %d 条，本次上限 %s；用户: %s）",
            len(pending),
            len(items),
            len(items) - len(pending),
            args.max_weibo or "不限",
            "全部" if not args.users else ", ".join(args.users),
        )

    # 按微博账号解析评论者白名单（账号未配置 = 不过滤）
    user_filter_conf = load_comment_user_filter(config)
    user_id_map = load_user_id_map(output_dir)
    user_allowed: dict[str, set[str] | None] = {}
    for item in items:
        user = item["user"]
        if user not in user_allowed:
            user_allowed[user] = resolve_allowed_users(
                user_filter_conf, user, user_id_map
            )
    filtered_accounts = [
        user for user, allowed in user_allowed.items() if allowed
    ]
    if filtered_accounts:
        logger.info(
            "评论者白名单已启用：%s",
            "、".join(
                "%s（%d 人）" % (user, len(user_allowed[user]))
                for user in filtered_accounts
            ),
        )

    if args.dry_run:
        print(
            "dry-run：本次将处理 %d 条微博，涉及 %d 个用户目录；不请求接口、不写文件。"
            % (len(pending), len({item["user"] for item in pending}))
        )
        if filtered_accounts:
            print(
                "评论者白名单：%s"
                % "、".join(
                    "%s=%d 人" % (user, len(user_allowed[user]))
                    for user in filtered_accounts
                )
            )
        print("如需小批试跑：--max-weibo 20；全量回填：去掉 --dry-run 并把 --max-weibo 设为 0。")
        return 0
    if not pending:
        logger.info("没有待处理微博（断点均已完成；如需重抓请加 --force），退出")
        return 0

    max_root = args.limit_comments if args.limit_comments is not None else int(config.get("comment_max_download_count", 0) or 0)
    max_reply = args.limit_replies if args.limit_replies is not None else int(config.get("comment_reply_max_download_count", 0) or 0)
    options = comment_fetch.FetchOptions(
        max_root=max_root,
        max_reply=max_reply,
        delay_min=float(config.get("comment_request_delay_min", 1.0) or 0),
        delay_max=float(config.get("comment_request_delay_max", 2.0) or 0),
        include_latest=bool(config.get("comment_include_latest_stream", 0)),
        emoji_replace=bool(config.get("comment_emoji_replace", 1)),
        link_preview=bool((config.get("markdown_preview") or {}).get("link_preview", 0)),
        max_requests=int(config.get("comment_max_requests", 300) or 300),
    )
    render_options = comment_markdown.RenderOptions(
        show_like_count=bool(config.get("comment_show_like_count", 0)),
        show_reply_target=bool(config.get("comment_show_reply_target", 1)),
        image_dir=(
            (config.get("markdown_file_dir") or {}).get("comment_image_dir", "评论图片")
            if isinstance(config.get("markdown_file_dir"), dict)
            else "评论图片"
        ),
    )
    pic_download = bool(config.get("comment_pic_download", 0))
    backup = (not args.no_backup) and bool(config.get("comment_markdown_backup", 0))
    # 同一次运行内共享：同一个 md 文件只备份一次
    backup_state: set[str] = set()

    session = build_session(config)
    emoji_store = comment_emoji.EmojiStore(
        comment_emoji.resolve_emoji_dir(
            config.get("weibo_emoji_dir", "weibo_data/emoji"), ROOT_DIR
        ),
        comment_emoji.make_session_downloader(
            session, HEADERS, validate_bytes=media_check.validate_bytes
        ),
        logger_=logger,
        inline_size=int(config.get("comment_emoji_inline_size", 18) or 0),
    )

    stats = {
        "planned": len(pending),
        "processed": 0,
        "written": 0,
        "empty": 0,
        "unchanged": 0,
        "failed": 0,
        "skipped": len(items) - len(pending),
        "missing_block": 0,
        "roots": 0,
        "replies": 0,
        "requests": 0,
        "errors": 0,
        "filtered_weibo": 0,
        "empty_pending_new": 0,
        "empty_recheck_cleared": 0,
        "empty_pending": 0,
        "invisible": 0,
        "new_caught": 0,
        "updated_blocks": 0,
        "not_fetched": 0,
    }
    empty_pending = load_empty_pending(pending_file)
    empty_pending_before = len(empty_pending)
    invisible_file = invisible_list_path(report_dir)
    invisible_items = load_invisible_list(invisible_file)
    results: list[dict] = []
    start_time = time.time()
    consecutive_errors = 0

    interrupted = False
    try:
        for index, item in enumerate(pending, start=1):
            user = item["user"]
            try:
                record = process_one(
                    session, HEADERS, item, options, render_options, backup,
                    dry_run=False, pic_download=pic_download,
                    allowed_users=user_allowed.get(user),
                    backup_state=backup_state,
                    emoji_store=emoji_store,
                )
            except Exception as exc:  # noqa: BLE001 兜底：失败不阻断
                logger.exception("处理失败 微博id:%s", item["weibo_id"])
                record = {
                    **item,
                    "status": "失败",
                    "total_number": 0,
                    "roots": 0,
                    "replies": 0,
                    "requests": 0,
                    "errors": 1,
                    "note": "%s: %s" % (type(exc).__name__, exc),
                }
            if record.get("filtered_users"):
                stats["filtered_weibo"] += 1
            status = record["status"]
            if status == "已写入":
                stats["written"] += 1
                if record.get("change_kind") == "新抓到":
                    stats["new_caught"] += 1
                elif record.get("change_kind") == "已更新":
                    stats["updated_blocks"] += 1
            elif status == "未变化":
                stats["unchanged"] += 1
            elif status == "未抓到":
                stats["not_fetched"] += 1
            elif status == "无评论":
                stats["empty"] += 1
            elif status == "已不可见":
                stats["invisible"] += 1
                upsert_invisible(invisible_items, record)
                user_done[user].add(item["weibo_id"])
                clear_empty_pending(empty_pending, item["weibo_id"])
                consecutive_errors = 0
                logger.info(
                    "已不可见微博 %s：%s（不再重试评论）",
                    item["weibo_id"],
                    record.get("invisible_reason", ""),
                )
            elif status == "待复核":
                stats["empty_pending"] += 1
            elif status == "跳过":
                stats["missing_block"] = stats.get("missing_block", 0) + 1
            else:  # 失败
                stats["failed"] += 1
            stats["roots"] += record["roots"]
            stats["replies"] += record["replies"]
            stats["requests"] += record["requests"]
            stats["errors"] += record["errors"]
            if status == "跳过":
                # 微博块缺失：写断点避免每次重扫同一文件，但报告单独计数
                user_done[user].add(item["weibo_id"])
                consecutive_errors = 0
                logger.warning(
                    "跳过微博 %s：%s", item["weibo_id"], record.get("note", "")
                )
            elif status == "待复核":
                # 空响应：不写断点（下次运行会重试），不计入连续失败，只记待复核清单。
                upsert_empty_pending(empty_pending, record)
                stats["empty_pending_new"] += 1
                consecutive_errors = 0
                logger.warning(
                    "待复核微博 %s：%s（清单 %d 条）",
                    item["weibo_id"],
                    record.get("empty_hint", ""),
                    len(empty_pending),
                )
            elif status == "失败":
                # 失败项不写断点：下次运行（--resume 默认）会重试这些微博；
                # 同时计入连续失败熔断计数，避免 20~60 小时全量在白跑。
                consecutive_errors += 1
                logger.warning(
                    "处理失败 微博id:%s（连续失败 %d 次）: %s",
                    item["weibo_id"],
                    consecutive_errors,
                    record.get("note", ""),
                )
            else:
                user_done[user].add(item["weibo_id"])
                if clear_empty_pending(empty_pending, item["weibo_id"]):
                    stats["empty_recheck_cleared"] += 1
                consecutive_errors = 0
            results.append(record)
            stats["processed"] += 1

            if index % PROGRESS_EVERY == 0:
                for user_name, done in user_done.items():
                    save_progress(
                        user_progress_path[user_name], done, stats,
                        config_fingerprint=fingerprint,
                    )
                save_empty_pending(pending_file, empty_pending)
                save_invisible_list(invisible_file, invisible_items)
                logger.info(
                    "进度 %d/%d：已写入 %d，无评论 %d，失败 %d，跳过 %d，待复核 %d",
                    index, len(pending), stats["written"], stats["empty"],
                    stats["failed"], stats["skipped"], len(empty_pending),
                )
            if consecutive_errors >= args.max_consecutive_errors:
                logger.error(
                    "连续失败 %d 次，提前停止；正在保存进度并生成报告", consecutive_errors
                )
                break
            if index % max(1, args.batch_size) == 0:
                logger.info("批次完成，休息 %.0f 秒", args.batch_rest)
                time.sleep(max(0.0, args.batch_rest))
    except KeyboardInterrupt:
        interrupted = True
        logger.warning("收到中断信号：正在保存进度并生成部分报告……")
    finally:
        for user_name, done in user_done.items():
            save_progress(
                user_progress_path[user_name], done, stats,
                config_fingerprint=fingerprint,
            )
        save_empty_pending(pending_file, empty_pending)
        save_invisible_list(invisible_file, invisible_items)
        stats["empty_pending_total"] = len(empty_pending)
        stats["invisible_total"] = len(invisible_items)
        stats["empty_pending_before"] = empty_pending_before
        elapsed = time.time() - start_time
        stats["interrupted"] = interrupted
        report_path = write_report(
            report_dir, stats, results, elapsed, empty_pending=empty_pending
        )
        logger.info(
            "回填结束（%s）：处理 %d 条，已写入 %d 条，无评论 %d 条，失败 %d 条，耗时 %.0f 秒",
            "中断" if interrupted else "完成",
            stats["processed"], stats["written"], stats["empty"], stats["failed"], elapsed,
        )
        logger.info("回填报告: %s", report_path)

    return 0 if stats["failed"] == 0 and not interrupted else 2


def _print_clean_backups(output_dir: str, dry_run: bool) -> int:
    count, total_bytes = clean_backups(output_dir, dry_run=dry_run)
    action = "将删除" if dry_run else "已删除"
    print(
        "%s %d 个 .bak 备份，共 %.2f MB%s"
        % (
            action,
            count,
            total_bytes / 1024 / 1024,
            "（dry-run 预览，未实际删除）" if dry_run else "",
        )
    )
    return 0


def _with_lock(output_dir: str, action) -> int:
    """在输出目录锁内执行 action；锁被占用时不执行并返回 2。"""
    lock = file_lock.FileLock(file_lock.lock_path_for_output(output_dir))
    try:
        lock.acquire()
    except file_lock.LockBusy as exc:
        logger.error(
            "已有微博抓取或评论回填进程在运行（%s）；为避免同时改写 markdown，"
            "本次不执行。确认没有进程在跑后可重试（POSIX 下进程退出会自动释放锁）",
            exc,
        )
        return 2
    try:
        return action()
    finally:
        lock.release()


def main(argv=None) -> int:
    """回填入口：开关/目录检查、clean-backups 分发、并发锁，然后执行回填主体。"""
    args = parse_args(argv)
    config = load_config(args.config)

    if int(config.get("weibo_comment_backfill", 0) or 0) != 1:
        logger.error(
            "配置中 weibo_comment_backfill=0（默认关闭）；如需回填，请把该配置改为 1 后重试"
        )
        return 1

    output_dir = file_lock.resolve_output_dir(ROOT_DIR, config.get("output_directory"))
    if not os.path.isdir(output_dir):
        logger.error("输出目录不存在: %s", output_dir)
        return 1

    if args.clean_backups:
        if args.dry_run:
            return _print_clean_backups(output_dir, dry_run=True)
        # 删除 .bak 也是写操作，与回填/主爬互斥
        return _with_lock(
            output_dir, lambda: _print_clean_backups(output_dir, dry_run=False)
        )

    # dry-run 只读、不写文件（也不建目录），不参与互斥锁，避免预览被长跑任务阻塞
    if args.dry_run:
        return _run_backfill(argv)

    # 与主爬互斥：避免和 weibo.py 同时改写同一批 markdown（整文件读改写会互相覆盖）
    return _with_lock(output_dir, lambda: _run_backfill(argv))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        # main() 正常路径会把中断转成「保存进度 + 写部分报告」；
        # 这里只兜底更早阶段（读取配置/扫描文件）的中断。
        logger.warning(
            "用户中断；若已进入处理阶段，进度与部分报告已保存，可下次用 --resume 继续"
        )
        sys.exit(130)
