#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""微博全量存档核对（只读，CLI）。

用途：枚举平台时间线，与本地存档做双向检查，输出两类名单：
1) 本轮未抓到的已存档微博（本地有、平台未返回；可能已删除/不可见/漏枚举）；
2) 未保存的微博（平台有、本地没有）。
只读：不写存档、不下载媒体、不改配置、不推进用户列表第三列；需要补齐请自行跑全量抓取。
与「本轮存档核对」共用 util/archive_check_core.py 的比对与报告逻辑。

示例：
    python weibo_archive_check_full.py --users 1234567890
    python weibo_archive_check_full.py --users 1234567890 1234567891 --since 2023-01-01
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from datetime import datetime

import weibo
from util import archive_check_core, archive_index
from util.archive_check_core import DTFORMAT, EnumerationStatus

logger = logging.getLogger("weibo")

PROJECT_DIR = os.path.dirname(os.path.realpath(__file__))
DEFAULT_MAX_PAGES = 300
_ILLEGAL_FILENAME_RE = re.compile(r'[\\/:*?"<>|]')


def _parse_since(text: str | None) -> datetime | None:
    """解析 --since：支持 YYYY-MM-DD 或 YYYY-MM-DDTHH:MM:SS。"""
    if not text:
        return None
    for fmt in (DTFORMAT, "%Y-%m-%d"):
        try:
            return datetime.strptime(text.strip(), fmt)
        except ValueError:
            continue
    raise SystemExit(f"--since 格式错误（应为 YYYY-MM-DD 或 YYYY-MM-DDTHH:MM:SS）：{text}")


def _safe_name(name: str | None) -> str:
    return _ILLEGAL_FILENAME_RE.sub("_", name or "unknown")


def _iter_mblog_cards(cards):
    """从页面 cards 中取出微博卡片（与 get_one_page 的卡片处理保持一致）。"""
    for card in cards or []:
        if not isinstance(card, dict):
            continue
        current = card
        if getattr(current, "get", None) and current.get("card_type") == 11:
            group = current.get("card_group") or []
            if group:
                current = group[0] or current
        if current.get("card_type") == 9 and isinstance(current.get("mblog"), dict):
            yield current


def _created_at_of(wb: "weibo.Weibo", mblog: dict) -> datetime | None:
    """把卡片里的 created_at 标准化为 datetime；解析失败返回 None。"""
    raw = mblog.get("created_at")
    if not raw:
        return None
    try:
        standard, _ = wb.standardize_date(str(raw))
        return datetime.strptime(standard, DTFORMAT)
    except Exception:
        return None


def enumerate_platform(
    wb: "weibo.Weibo",
    audit_since: datetime,
    page_count: int,
    max_pages: int,
) -> tuple[dict[str, datetime | None], EnumerationStatus]:
    """轻量枚举平台时间线。

    返回：
    - platform: {id: 发布时间}（已按 only_crawl_original 过滤；包含置顶旧帖）；
    - enumeration: 本轮平台枚举是否顺利完成。
    """
    enumeration = EnumerationStatus(page_count=page_count)
    platform: dict[str, datetime | None] = {}
    page_limit = min(page_count, max_pages) if max_pages else page_count
    if page_count > page_limit:
        enumeration.reason = f"页数 {page_count} 超过上限 {page_limit}，未覆盖全部历史"

    hit_since = False
    for page in range(1, page_limit + 1):
        enumeration.pages_done = page
        js = wb.get_weibo_json(page)
        if not js:
            enumeration.page_errors.append(page)
            enumeration.ok = False
            enumeration.reason = f"第{page}页请求失败（已重试仍无数据）"
            return platform, enumeration
        if not js.get("ok", 1):
            # 接口返回无更多数据：接近末尾视为正常结束，否则视为异常提前结束
            if page >= max(1, page_count - 1):
                enumeration.api_ended = True
                enumeration.ok = True
                enumeration.reason = "接口返回无更多数据，已覆盖到末尾"
            else:
                enumeration.ok = False
                enumeration.reason = f"第{page}/{page_count}页接口提前结束，疑似分页上限或异常"
            break

        cards = (js.get("data") or {}).get("cards") or []
        page_mblogs = 0
        for card in _iter_mblog_cards(cards):
            mblog = card["mblog"]
            wid = str(mblog.get("id") or "")
            if not wid:
                continue
            page_mblogs += 1
            dt = _created_at_of(wb, mblog)
            is_pinned = mblog.get("mblogtype", 0) == 2
            is_retweet = bool(mblog.get("retweeted_status"))

            if wb.only_crawl_original and is_retweet:
                # 存档策略只存原创：转发不进入平台集合，但时间线仍要按日期判断下界
                if dt is not None and dt < audit_since and not is_pinned:
                    hit_since = True
                continue

            if dt is not None and dt < audit_since and not is_pinned:
                # 早于检查起点：不进入本次检查集合，并标记已到达下界
                hit_since = True
                continue
            platform[wid] = dt

        if wb.anti_ban_enabled:
            wb.update_crawl_stats(weibo_count=page_mblogs)
            wb.check_batch_delay()
            need_rest, reason = wb.should_pause_session()
            if need_rest:
                logger.warning("平台枚举暂停：%s", reason)
                wb.perform_anti_ban_rest()
                wb.reset_crawl_stats()

        if hit_since:
            enumeration.hit_since_date = True
            enumeration.ok = True
            enumeration.reason = "命中检查范围起点，已扫到范围开头"
            break
    else:
        # 循环自然结束：扫完了所有页
        if page_limit >= page_count:
            enumeration.ok = True
            enumeration.reason = "已扫完所有页"
        else:
            enumeration.ok = False

    if enumeration.page_errors:
        enumeration.ok = False
    if not enumeration.reason or enumeration.reason == "未开始":
        enumeration.reason = "枚举结束"
    return platform, enumeration


def audit_one_user(
    wb: "weibo.Weibo",
    user_config: dict,
    args: argparse.Namespace,
) -> int:
    """检查一个用户；返回 0=枚举顺利完成，2=枚举未顺利完成，1=失败。"""
    wb.initialize_info(user_config)
    wb.query = ""
    if wb.get_user_info() != 0:
        logger.error("获取用户信息失败，跳过检查: %s", user_config.get("user_id"))
        return 1

    user_id = str(user_config.get("user_id", ""))
    screen_name = wb.user.get("screen_name", user_id)
    logger.info("=" * 70)
    logger.info("开始检查用户: %s (%s)", screen_name, user_id)

    user_dir = wb.get_filepath("markdown")
    exclude_dirs = getattr(
        wb, "invisible_weibo_dirs", archive_index.DEFAULT_EXCLUDE_DIRS
    )
    local_entries, scan_stats = archive_index.collect_archive_entries(
        user_dir, None, None, exclude_dirs
    )
    invisible_ids = archive_index.collect_invisible_weibo_ids(user_dir, exclude_dirs)
    logger.info(
        "本地活跃存档: %d 条（扫描 %d 个文件，跳过 %d 个）；已确认不可见的微博 %d 条",
        len(local_entries), scan_stats.files_scanned, scan_stats.files_skipped,
        len(invisible_ids),
    )

    # 检查范围：--since 优先；否则取「全局 since_date 与存档最早日期」中较早者
    since = _parse_since(args.since)
    if since is None:
        candidates = []
        try:
            candidates.append(datetime.strptime(wb.since_date, DTFORMAT))
        except (ValueError, TypeError):
            pass
        dated = [dt for dt, _path in local_entries.values() if dt is not None]
        if dated:
            candidates.append(min(dated))
        since = min(candidates) if candidates else datetime(2011, 1, 1)
    until = datetime.now()
    logger.info("检查范围: %s ~ %s", since.strftime(DTFORMAT), until.strftime(DTFORMAT))

    page_count = wb.get_page_count() or 0
    if page_count <= 0:
        logger.error("无法获取页数（用户信息异常），跳过检查")
        return 1

    platform, enumeration = enumerate_platform(wb, since, page_count, args.max_pages)
    diff = archive_check_core.compare_archive(
        local_entries, platform, invisible_ids, since, until
    )
    unknown_dates = sum(1 for dt, _path in local_entries.values() if dt is None)
    logger.info(
        "检查完成：平台 %d 条，本地存档 %d 条，未抓到 %d 条，未保存的微博 %d 条",
        len(platform), len(local_entries), len(diff.not_returned), len(diff.not_saved),
    )

    report_dir = wb._report_output_dir(
        getattr(wb, "archive_check_full_dir", "report")
    )
    os.makedirs(report_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(
        report_dir,
        f"weibo_archive_check_full_{user_id}_{_safe_name(screen_name)}_{stamp}.txt",
    )
    header_lines = [
        f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        (
            f"本轮枚举是否顺利完成: {'是' if enumeration.ok else '否'}"
            f"（{enumeration.reason}；页码 {enumeration.pages_done}/{enumeration.page_count}；"
            f"页级失败 {len(enumeration.page_errors)}）"
        ),
        f"平台枚举: {len(platform)} 条（已按 only_crawl_original 过滤）",
        f"本地存档: {len(local_entries)} 条（无日期 {unknown_dates} 条未参与）",
        "说明: 本地文件路径相对于 output_directory。",
    ]
    if not enumeration.ok:
        header_lines.append("!! 本轮枚举未顺利完成，下面的名单不能作为删除依据 !!")
    summary_lines = [
        f"range_start: {since.strftime(DTFORMAT)}",
        f"range_end: {until.strftime(DTFORMAT)}",
        f"enumeration_ok: {'true' if enumeration.ok else 'false'}",
        f"enumeration_reason: {enumeration.reason}",
        f"pages_done: {enumeration.pages_done}",
        f"page_count: {enumeration.page_count}",
        f"page_errors: {len(enumeration.page_errors)}",
        f"platform_count: {len(platform)}",
        f"archive_count: {len(local_entries)}",
        f"archive_unknown_dates: {unknown_dates}",
        f"invisible_weibo_count: {len(invisible_ids)}",
        f"not_returned_count: {len(diff.not_returned)}",
        f"not_saved_count: {len(diff.not_saved)}",
    ]
    base_dir = os.path.join(PROJECT_DIR, wb.output_directory)
    report = archive_check_core.render_report(
        "全量存档核对报告",
        user_id,
        screen_name,
        diff,
        header_lines=header_lines,
        range_start=since,
        range_end=until,
        summary_lines=summary_lines,
        base_dir=base_dir,
    )
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"[{screen_name}] 核对报告: {report_path}")
    print(
        f"[{screen_name}] 未抓到 {len(diff.not_returned)} 条，"
        f"未保存的微博 {len(diff.not_saved)} 条，"
        f"枚举={'顺利完成' if enumeration.ok else '未顺利完成'}"
    )
    return 0 if enumeration.ok else 2


def _full_check_enabled(config: dict) -> bool:
    """配置总开关：weibo_archive_check_full=0 时禁止运行（默认 0 = 禁用）。"""
    return config.get("weibo_archive_check_full", 0) != 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="微博全量存档核对（只读）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--users", nargs="*", default=None,
        help="要检查的用户 ID（默认：user_id_list 中的全部用户）",
    )
    parser.add_argument(
        "--since", default=None,
        help="检查起点（YYYY-MM-DD 或 YYYY-MM-DDTHH:MM:SS）；默认取存档最早日期与全局 since_date 的较早者",
    )
    parser.add_argument(
        "--max-pages", type=int, default=DEFAULT_MAX_PAGES,
        help=f"单用户最多枚举多少页（默认 {DEFAULT_MAX_PAGES}，防止失控）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logger.info("模式：只读检查（不写存档、不下载媒体）")

    try:
        config = weibo.get_config()
        if not _full_check_enabled(config):
            logger.info(
                "配置中 weibo_archive_check_full=0（默认关闭），已禁用全量存档核对；"
                "如需使用请把该配置改为 1"
            )
            return 0
        wb = weibo.Weibo(config)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("初始化失败: %s", e)
        return 1

    wanted = None
    if args.users:
        wanted = {str(uid) for uid in args.users}
    targets = [
        user_config
        for user_config in wb.user_config_list
        if wanted is None or str(user_config.get("user_id")) in wanted
    ]
    if not targets:
        logger.error("没有匹配的检查用户（检查 --users 与 user_id_list 配置）")
        return 1

    exit_code = 0
    for user_config in targets:
        try:
            code = audit_one_user(wb, user_config, args)
        except Exception as e:  # noqa: BLE001
            logger.exception("检查用户 %s 失败: %s", user_config.get("user_id"), e)
            code = 1
        exit_code = max(exit_code, code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
