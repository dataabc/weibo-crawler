"""本轮「确认已删除」微博清单（写到每个用户的存档目录）。

口径：
- 候选 = 抓取前主存档里有、本轮抓取没返回、且在本次核对范围（since_date~crawl_end）
  内的微博；
- 对候选逐条调用 ``statuses/show``：接口明确「该微博不存在/已删除」才写入清单；
  「暂无查看权限」等受限可见、以及状态接口异常（unknown）都不算已删除；
- 仅在完整抓取（非 append 增量、非 query 关键词模式）且本轮抓取成功时生成。

输出：``<用户目录>/已删除微博.txt``，每行 ``微博ID<TAB>发布日期``（固定文件名，每次覆盖）。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

from util.core import archive_index, atomic_io
from util.core.time_format import DTFORMAT
from util.core.weibo_visibility import check_weibo_visibility

logger = logging.getLogger("weibo")

FILE_NAME = "已删除微博.txt"


def _fmt_date(value) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    try:
        return datetime.strptime(str(value), DTFORMAT).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return str(value or "")


def render_file(wb, candidates, deleted, counts: dict) -> str:
    user = wb.user or {}
    user_id = wb.user_config.get("user_id", "unknown")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# 本轮确认已删除微博",
        "",
        "用户: %s (ID: %s)" % (user.get("screen_name", "unknown"), user_id),
        "生成时间: %s" % now,
        "候选（本地有、本轮未返回）: %d 条" % len(candidates),
        "核验结果: 已删除 %d、受限可见 %d、仍可见 %d、无法判断 %d"
        % (
            counts.get("deleted", 0),
            counts.get("restricted", 0),
            counts.get("visible", 0),
            counts.get("unknown", 0),
        ),
        "确认已删除: %d 条" % len(deleted),
        "格式: 微博ID<TAB>发布日期",
        "",
    ]
    for weibo_id, date_value in deleted:
        lines.append("%s\t%s" % (weibo_id, _fmt_date(date_value)))
    return "\n".join(lines) + "\n"


def generate(wb) -> str | None:
    """生成/覆盖用户目录下的「已删除微博.txt」；返回文件路径（跳过时 None）。"""
    if not int(getattr(wb, "weibo_deleted_report", 1) or 0):
        return None
    if "markdown" not in (wb.write_mode or []):
        return None
    if getattr(wb, "query", ""):
        logger.info("已删除微博清单：query 关键词模式跳过")
        return None
    import const

    if getattr(const, "MODE", "overwrite") == "append":
        logger.info("已删除微博清单：append 增量模式跳过（只覆盖最近窗口，不代表已删除）")
        return None
    cov = getattr(wb, "crawl_success", {}) or {}
    if not cov.get("success"):
        logger.info("已删除微博清单：本轮抓取未顺利完成，跳过")
        return None
    if getattr(wb, "archived_entries_before", None) is None or getattr(wb, "check_start_dt", None) is None:
        logger.info("已删除微博清单：缺少抓取前存档快照或核对范围，跳过")
        return None
    if not wb.user or not wb.user_config:
        return None
    try:
        md_dir = wb.get_filepath("markdown")
        crawl_end = (
            datetime.strptime(wb.start_date, DTFORMAT)
            if wb.start_date
            else datetime.now()
        )
        crawled = {str(i) for i in getattr(wb, "crawled_ids_this_run", ()) if i}
        known_gone = set(getattr(wb, "known_invisible_ids", ()) or ())
        if not known_gone:
            try:
                known_gone = archive_index.collect_invisible_weibo_ids(
                    md_dir, wb._archive_exclude_dirs()
                )
            except Exception:  # noqa: BLE001 兜底：不影响主流程
                known_gone = set()
        candidates = []
        for weibo_id, (created_at, _path) in (wb.archived_entries_before or {}).items():
            wid = str(weibo_id)
            if created_at is None or wid in crawled or wid in known_gone:
                continue
            if created_at < wb.check_start_dt or created_at > crawl_end:
                continue
            candidates.append((wid, created_at))
        candidates.sort(key=lambda item: item[1], reverse=True)
        logger.info(
            "已删除微博清单：候选 %d 条（本地有、本轮未返回），开始逐条核验状态",
            len(candidates),
        )
        deleted = []
        counts = {"deleted": 0, "restricted": 0, "visible": 0, "unknown": 0}
        for weibo_id, created_at in candidates:
            kind, reason = check_weibo_visibility(
                wb.session, wb.headers, weibo_id
            )
            counts[kind] = counts.get(kind, 0) + 1
            if kind == "deleted":
                deleted.append((weibo_id, created_at))
            elif kind != "visible":
                logger.info(
                    "已删除微博清单：%s 状态=%s（%s），不列入已删除",
                    weibo_id,
                    kind,
                    reason,
                )
        target = os.path.join(md_dir, FILE_NAME)
        atomic_io.atomic_write_text(target, render_file(wb, candidates, deleted, counts))
        logger.info(
            "已删除微博清单: %s（候选 %d，确认已删除 %d）",
            target,
            len(candidates),
            len(deleted),
        )
        return target
    except Exception as exc:  # noqa: BLE001 兜底：报告失败不阻断主流程
        logger.warning("生成已删除微博清单失败: %s", exc)
        wb._record_write_failure("已删除微博清单", "", str(exc))
        return None
