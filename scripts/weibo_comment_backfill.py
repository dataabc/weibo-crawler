#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""微博评论全量回填脚本。

对已存档 Markdown 里的微博逐条抓取评论（一级评论 + 二级回复），把评论块
写回对应微博块。支持全量/按用户/按时间范围，分批执行、断点续跑、dry-run。

用法示例::

    python scripts/weibo_comment_backfill.py --dry-run                 # 只看规模，不写文件
    python scripts/weibo_comment_backfill.py --max-weibo 20            # 先小批试跑
    python scripts/weibo_comment_backfill.py                    # 全量回填（默认全量）
    python scripts/weibo_comment_backfill.py --users 用户A --since 2026-01-01
    python scripts/weibo_comment_backfill.py --resume           # 断点续跑（默认）

安全开关：config.json 中 ``weibo_comment_backfill`` 必须为 1，否则直接退出。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

import requests

# 脚本位于 scripts/，项目根目录是其上一级；把项目根加入 sys.path 以导入 util 与 weibo
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from util import archive_index, comment_fetch, comment_markdown, config_migrate, media_check
DTFORMAT = "%Y-%m-%dT%H:%M:%S"
PROGRESS_EVERY = 10
BACKUP_RE = re.compile(r"\.md\.bak\.\d{8}_\d{6}(_\d+)?$")

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
    parser.add_argument("--config", default=os.path.join(ROOT_DIR, "config.json"), help="配置文件路径")
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
    parser.add_argument("--no-backup", action="store_true", help="不生成 .bak 备份")
    parser.add_argument(
        "--clean-backups",
        action="store_true",
        help="删除由本功能生成的 .bak 备份（*.md.bak.<时间戳>），不执行回填",
    )
    parser.add_argument("--no-resume", action="store_true", help="不复用断点进度，从头开始")
    parser.add_argument("--force", action="store_true", help="忽略进度记录，重抓已处理微博")
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
    except Exception as exc:
        logger.warning("session 预热失败（继续）: %s", exc)
    return session


def _report_dir(config: dict, override: str | None, key: str) -> str:
    output_dir = str(config.get("output_directory") or "weibo_data")
    if not os.path.isabs(output_dir):
        output_dir = os.path.join(ROOT_DIR, output_dir)
    value = override or config.get(key) or "report"
    if not os.path.isabs(value):
        value = os.path.join(output_dir, value)
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


def save_progress(path: str, done: set[str], stats: dict) -> None:
    payload = {
        "updated_at": datetime.now().strftime(DTFORMAT),
        "done": sorted(done),
        "stats": stats,
    }
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


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
        path = file_name if os.path.isabs(file_name) else os.path.join(ROOT_DIR, file_name)
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
        if isinstance(allowed_users, (list, tuple)):
            allowed = {
                str(item).strip() for item in allowed_users if str(item).strip()
            }
            if allowed:
                return allowed
        return None
    return None


def download_comment_images(session, result, md_path: str, image_dir_name: str):
    image_dir = os.path.join(os.path.dirname(md_path), image_dir_name)
    os.makedirs(image_dir, exist_ok=True)
    comments = []
    for root in result.roots:
        comments.append(root)
        comments.extend(root.replies)
    for comment in comments:
        if not comment.pic_url:
            continue
        stamp = (comment.created_at or "unknown").replace(" ", "_").replace(":", "-")
        target = os.path.join(image_dir, "%s_%s.jpg" % (stamp, comment.id))
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
            with open(target, "wb") as handle:
                handle.write(data)
            comment.pic_file = os.path.basename(target)
        except Exception as exc:
            logger.debug("评论图片下载失败 %s: %s", comment.pic_url, exc)


def process_one(
    session, headers, item, options, render_options, backup, dry_run, pic_download,
    allowed_users=None, backup_state=None,
):
    result = comment_fetch.fetch_comments(
        session, headers, item["weibo_id"], options
    )
    if allowed_users:
        result = comment_fetch.filter_comments(result, allowed_users)
    record = {
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
    if not result.roots:
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
    changed = comment_markdown.update_markdown_file(
        item["md_path"], item["weibo_id"], block, backup=backup, dry_run=dry_run,
        backup_state=backup_state,
    )
    record["status"] = "已写入" if changed else "未变化"
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


def write_report(report_dir: str, stats: dict, results: list[dict], elapsed: float) -> str:
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
        "未变化: %d 条" % stats.get("unchanged", 0),
        "失败: %d 条" % stats.get("failed", 0),
        "跳过（断点已完成）: %d 条" % stats.get("skipped", 0),
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
    failed = [item for item in results if item["status"] == "失败"]
    for item in failed:
        lines.append(
            "失败 | 用户: %s | 微博id: %s | %s"
            % (item["user"], item["weibo_id"], item.get("note", ""))
        )
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return path


def main(argv=None) -> int:
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

    report_dir = _report_dir(config, args.report_dir, "weibo_comment_backfill_dir")
    exclude_dirs = config.get("invisible_weibo_dirs") or list(archive_index.DEFAULT_EXCLUDE_DIRS)
    since_dt = _parse_day(args.since)
    until_dt = _parse_day(args.until, end_of_day=True)

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
    for item in items:
        user = item["user"]
        if user in user_progress_path:
            continue
        safe_user = re.sub(r'[\\/:*?"<>|]', "_", user)
        path = os.path.join(report_dir, "comment_backfill_progress_%s.json" % safe_user)
        user_progress_path[user] = path
        progress = load_progress(path)
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

    session = build_session(config)

    stats = {
        "planned": len(pending),
        "processed": 0,
        "written": 0,
        "empty": 0,
        "unchanged": 0,
        "failed": 0,
        "skipped": len(items) - len(pending),
        "roots": 0,
        "replies": 0,
        "requests": 0,
        "errors": 0,
        "filtered_weibo": 0,
    }
    results: list[dict] = []
    start_time = time.time()
    consecutive_errors = 0

    for index, item in enumerate(pending, start=1):
        user = item["user"]
        try:
            record = process_one(
                session, HEADERS, item, options, render_options, backup,
                dry_run=False, pic_download=pic_download,
                allowed_users=user_allowed.get(user),
            )
            if record.get("filtered_users"):
                stats["filtered_weibo"] += 1
            if record["status"] == "已写入":
                stats["written"] += 1
            elif record["status"] == "无评论":
                stats["empty"] += 1
            elif record["status"] == "未变化":
                stats["unchanged"] += 1
            stats["roots"] += record["roots"]
            stats["replies"] += record["replies"]
            stats["requests"] += record["requests"]
            stats["errors"] += record["errors"]
            user_done[user].add(item["weibo_id"])
            consecutive_errors = 0
        except Exception as exc:
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
            stats["failed"] += 1
            consecutive_errors += 1
        results.append(record)
        stats["processed"] += 1

        if index % PROGRESS_EVERY == 0:
            for user_name, done in user_done.items():
                save_progress(user_progress_path[user_name], done, stats)
            logger.info(
                "进度 %d/%d：已写入 %d，无评论 %d，失败 %d，跳过 %d",
                index, len(pending), stats["written"], stats["empty"],
                stats["failed"], stats["skipped"],
            )
        if consecutive_errors >= args.max_consecutive_errors:
            logger.error("连续失败 %d 次，提前停止；进度已保存", consecutive_errors)
            break
        if index % max(1, args.batch_size) == 0:
            logger.info("批次完成，休息 %.0f 秒", args.batch_rest)
            time.sleep(max(0.0, args.batch_rest))

    for user_name, done in user_done.items():
        save_progress(user_progress_path[user_name], done, stats)

    elapsed = time.time() - start_time
    report_path = write_report(report_dir, stats, results, elapsed)
    logger.info(
        "回填结束：处理 %d 条，已写入 %d 条，无评论 %d 条，失败 %d 条，耗时 %.0f 秒",
        stats["processed"], stats["written"], stats["empty"], stats["failed"], elapsed,
    )
    logger.info("回填报告: %s", report_path)
    return 0 if stats["failed"] == 0 else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        logger.warning("用户中断；进度文件已保存，可下次用 --resume 继续")
        sys.exit(130)
