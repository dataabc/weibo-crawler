"""不可见微博隔离区审核。

判定 → 复制到「已不可见」→ 媒体补全 → 恢复可见时删副本；依赖的 wb
（weibo 列表/状态索引/报告目录等）由 Weibo 实例提供。
"""

from __future__ import annotations

import logging
import os

from util.core import archive_state
from util.core import errors

logger = logging.getLogger("weibo")


def review_invisible_archive(wb) -> None:
    """不可见微博隔离区审核（全量轮：判定→复制→恢复→报告）。"""
    if not getattr(wb, "invisible_archive_enabled", 1):
        return
    if wb.user_config.get("query_list"):
        return
    cov = wb.crawl_success or {}
    # 本轮未实际爬取（如用户信息获取失败被跳过）时直接返回：否则会去解析
    # 不存在的 markdown 路径，产生无意义的 KeyError/路径错误日志。
    if not cov.get("attempted"):
        return
    if not cov.get("success"):
        return
    try:
        state = wb._ensure_archive_state()
    except Exception as e:  # noqa: BLE001 兜底：状态索引异常不阻断
        # 报告需区分「未启用」与「状态索引不可用」，避免静默失效无痕迹。
        logger.warning(
            "隔离区审核跳过：状态索引读取失败（%s）",
            errors.describe_exception(e),
        )
        return
    user_dir = wb.get_filepath("markdown")
    base = str(getattr(wb, "invisible_archive_dir", "已不可见") or "已不可见")
    archive_root = base if os.path.isabs(base) else os.path.join(user_dir, base)
    captured = {str(w.get("id")) for w in getattr(wb, "weibo", []) or []}
    window_start = str(cov.get("window_start") or "")
    earliest = min((str(i.get("created_at") or "") for i in state.get("weibos", {}).values() if isinstance(i, dict) and i.get("created_at")), default="")
    if not (window_start and earliest and window_start[:19] <= earliest[:19]):
        return  # 增量轮不做消失判定
    threshold = int(getattr(wb, "invisible_confirm_rounds", 2) or 2)
    new_isolated, restored, kept = [], [], 0
    for wid, item in list(state.get("weibos", {}).items()):
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "active")
        if wid in captured:
            if status in ("suspect_invisible", "isolated"):
                wb._remove_isolated_copy(archive_root, wid)
                item["status"] = "active"
                item["missing_rounds"] = 0
                restored.append({"weibo_id": wid, "result": "已恢复，删除隔离副本"})
            continue
        item["missing_rounds"] = int(item.get("missing_rounds") or 0) + 1
        if item["missing_rounds"] >= threshold and status != "isolated":
            if wb._copy_to_isolated(archive_root, wid, item, user_dir):
                item["status"] = "isolated"
                new_isolated.append({"weibo_id": wid, "result": f"第{item['missing_rounds']}轮未抓到，已复制到隔离区"})
            else:
                item["status"] = "suspect_invisible"
        elif status == "isolated":
            kept += 1
    try:
        archive_state.save_state(state, wb._state_path, wb._atomic_write_text)
    except Exception as e:  # noqa: BLE001
        logger.warning("保存隔离区状态失败: %s", e)
    backfilled = wb._backfill_isolated_media(archive_root)
    wb.invisible_review = {
        "new_isolated": new_isolated,
        "restored": restored,
        "kept": kept,
        "backfilled_media": backfilled,
    }
    logger.info(
        "隔离区审核：新隔离 %d 条，恢复可见 %d 条，仍隔离 %d 条",
        len(new_isolated), len(restored), kept,
    )
