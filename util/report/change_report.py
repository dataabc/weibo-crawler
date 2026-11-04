"""变化报告。

纯渲染模块：把「本轮抓取检测到的变化」渲染成文本报告，便于单测与复用。
数据来源（由调用方 weibo.py 提供）：
- 编辑变化：本轮检测到内容指纹变化的已存档微博（含旧版快照路径）；
- 可见性变化：visible.type/list_id 变化的微博；
- 疑似不可见/删除：状态索引里有、本轮没抓到的微博；
- 恢复可见：之前疑似不可见、本轮又抓到的微博；
- 隔离区审核：未启用时可为空。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

from util.core import archive_state
from util.report import reporting

LINE = "=" * 60


logger = logging.getLogger("weibo")


def render_change_report(
    *,
    user_id: str,
    screen_name: str,
    window_start: str = "",
    window_end: str = "",
    crawl_ok: bool = True,
    crawl_reason: str = "",
    pages: int = 0,
    captured_count: int = 0,
    edit_changes: list[dict] | None = None,
    visibility_changes: list[dict] | None = None,
    missing_weibos: list[dict] | None = None,
    missing_complete: bool = True,
    restored_weibos: list[dict] | None = None,
    invisible_review: list[dict] | None = None,
    long_text_failures: list[dict] | None = None,
    generated_at: str | None = None,
) -> str:
    """渲染变化报告文本。"""
    generated_at = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    edit_changes = edit_changes or []
    visibility_changes = visibility_changes or []
    missing_weibos = missing_weibos or []
    restored_weibos = restored_weibos or []
    invisible_review = invisible_review or []
    long_text_failures = long_text_failures or []
    lines = [
        "微博存档变化报告",
        f"生成时间: {generated_at}",
        f"账号: {screen_name}（{user_id}）",
        f"本轮抓取窗口: {window_start or '未知'} → {window_end or '未知'}",
        (
            f"本轮抓取: {'顺利完成' if crawl_ok else '未顺利完成'}（{pages} 页）"
            + (f"，原因: {crawl_reason}" if crawl_reason and not crawl_ok else "")
        ),
        LINE,
        f"一、编辑变化: {len(edit_changes)} 条",
    ]
    if not edit_changes:
        lines.append("（本轮无编辑变化）")
    for item in edit_changes:
        lines.append(
            "微博id: {wid} | 发布时间: {created} | 最后编辑时间: {new} | 旧版快照: {snapshot}".format(
                wid=item.get("weibo_id", ""),
                created=item.get("created_at", ""),
                new=item.get("new_edit_at") or "未知",
                snapshot=item.get("snapshot") or "未保存",
            )
        )
    lines.append(LINE)
    lines.append(f"二、可见性变化: {len(visibility_changes)} 条")
    if not visibility_changes:
        lines.append("（本轮无可见性变化）")
    for item in visibility_changes:
        lines.append(
            "微博id: {wid} | 旧: {old} | 新: {new}".format(
                wid=item.get("weibo_id", ""),
                old=item.get("old_label") or item.get("old"),
                new=item.get("new_label") or item.get("new"),
            )
        )
    lines.append(LINE)
    lines.append(f"三、疑似不可见/删除: {len(missing_weibos)} 条")
    if not missing_complete:
        lines.append(
            "（本轮为增量抓取，只覆盖最近窗口，未抓到的历史微博不代表已删除/不可见，本轮不列明细）"
        )
        missing_weibos = []
    elif not crawl_ok:
        lines.append("（本轮抓取未顺利完成，名单不完整，仅供参考）")
    if not missing_weibos:
        if missing_complete:
            lines.append("（本轮没有已存档微博缺席）")
    else:
        lines.append(
            "说明: 全量抓取时该名单才完整；需要确认请打开链接人工核实。"
        )
    for item in missing_weibos[:500]:
        lines.append(
            "微博id: {wid} | 发布时间: {created} | 状态: {status} | 连续未见到: {miss} 轮".format(
                wid=item.get("weibo_id", ""),
                created=item.get("created_at", ""),
                status=item.get("status", "active"),
                miss=item.get("missing_rounds", 0),
            )
        )
    if len(missing_weibos) > 500:
        lines.append(f"……其余 {len(missing_weibos) - 500} 条省略")
    lines.append(LINE)
    lines.append(f"四、恢复可见: {len(restored_weibos)} 条")
    if not restored_weibos:
        lines.append("（本轮没有恢复可见的微博）")
    for item in restored_weibos:
        lines.append(
            "微博id: {wid} | 发布时间: {created} | 原状态: {old}".format(
                wid=item.get("weibo_id", ""),
                created=item.get("created_at", ""),
                old=item.get("old_status", ""),
            )
        )
    lines.append(LINE)
    lines.append(f"五、隔离区审核: {len(invisible_review)} 条")
    if not invisible_review:
        lines.append("（隔离区审核未启用或本轮无结果）")
    for item in invisible_review:
        lines.append(
            "微博id: {wid} | 审核结果: {result}".format(
                wid=item.get("weibo_id", ""), result=item.get("result", "")
            )
        )
    lines.append(LINE)
    lines.append(f"六、长文获取失败: {len(long_text_failures)} 条")
    if not long_text_failures:
        lines.append("（本轮没有长文详情页获取失败）")
    for item in long_text_failures:
        lines.append(
            "微博id: {wid} | 发布时间: {created} | 处理: {note} | md: {md}".format(
                wid=item.get("weibo_id", ""),
                created=item.get("created_at", ""),
                note=(
                    "已保留旧存档、本轮不更新"
                    if item.get("archived")
                    else "新微博仅保存截断正文"
                ),
                md=item.get("md_path") or "—",
            )
        )
    lines.append(LINE)
    lines.append("统计:")
    lines.append(f"captured_count: {captured_count}")
    lines.append(f"edit_changes: {len(edit_changes)}")
    lines.append(f"visibility_changes: {len(visibility_changes)}")
    lines.append(f"missing_weibos: {len(missing_weibos)}")
    lines.append(f"restored_weibos: {len(restored_weibos)}")
    lines.append(f"invisible_review: {len(invisible_review)}")
    lines.append(f"long_text_failures: {len(long_text_failures)}")
    lines.append("报告完毕。")
    return "\n".join(lines) + "\n"


def generate(wb) -> None:
    """生成本轮变化报告（编辑/可见性/疑似不可见/恢复可见）。

    报告数据：
    - 编辑变化：``wb.edit_changes``（含旧版快照路径）；
    - 可见性变化：``wb.visibility_changes``（本轮 vs 状态索引里上一轮的值）；
    - 疑似不可见/删除：状态索引里有、本轮抓不到的已存档微博（增量抓取时
      未覆盖的历史也会出现，报告里会注明，避免误导）；
    - 恢复可见：之前 suspect/isolated、本轮又抓到的微博（状态恢复 active）；
    - 隔离区审核：未启用时留空。
    """
    if not getattr(wb, "weibo_change_report", 1):
        return
    try:
        state = wb._ensure_archive_state()
    except Exception as e:  # noqa: BLE001
        logger.warning("变化报告：状态索引不可用，本轮跳过: %s", e)
        return
    user_id = str(wb.user_config.get("user_id") or "unknown")
    screen_name = str(wb.user.get("screen_name") or user_id)
    captured_ids = {
        str(w.get("id") or "") for w in getattr(wb, "weibo", []) or []
    }
    missing = []
    restored = []
    # 判断本轮是否覆盖全量（抓取窗口起点 <= 存档最早微博日期）
    window_start = str((wb.crawl_success or {}).get("window_start") or "")
    earliest_created = min(
        (
            str(item.get("created_at") or "")
            for item in state.get("weibos", {}).values()
            if isinstance(item, dict) and item.get("created_at")
        ),
        default="",
    )
    full_crawl = bool(
        window_start
        and earliest_created
        and window_start[:19] <= earliest_created[:19]
    )
    for wid, item in list(state.get("weibos", {}).items()):
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or archive_state.STATUS_ACTIVE)
        if wid in captured_ids:
            if status in (
                archive_state.STATUS_SUSPECT,
                archive_state.STATUS_ISOLATED,
            ):
                restored.append(
                    {
                        "weibo_id": wid,
                        "created_at": item.get("created_at", ""),
                        "old_status": status,
                    }
                )
                item["status"] = archive_state.STATUS_ACTIVE
                item["missing_rounds"] = 0
        else:
            missing.append(
                {
                    "weibo_id": wid,
                    "created_at": item.get("created_at", ""),
                    "status": status,
                    "missing_rounds": int(item.get("missing_rounds") or 0),
                }
            )
    success = wb.crawl_success or {}
    report = render_change_report(
        user_id=user_id,
        screen_name=screen_name,
        window_start=str(success.get("window_start") or ""),
        window_end=str(success.get("window_end") or ""),
        crawl_ok=bool(success.get("success", True)),
        crawl_reason=str(success.get("reason") or ""),
        pages=int(success.get("pages_processed") or 0),
        captured_count=len(captured_ids),
        edit_changes=list(getattr(wb, "edit_changes", []) or []),
        visibility_changes=list(getattr(wb, "visibility_changes", []) or []),
        missing_weibos=missing,
        missing_complete=full_crawl,
        restored_weibos=restored,
        invisible_review=(
            (wb.invisible_review.get("new_isolated", []) + wb.invisible_review.get("restored", []))
            if getattr(wb, "invisible_review", None) else []
        ),
        long_text_failures=list(getattr(wb, "long_text_failures", []) or []),
    )
    base_dir = wb._report_output_dir(
        getattr(wb, "change_report_dir", "report")
    )
    report_path = os.path.join(
        base_dir,
        reporting.report_filename(
            reporting.PREFIX_CHANGE_REPORT,
            user_id,
            screen_name,
        ),
    )
    try:
        if not os.path.isdir(base_dir):
            os.makedirs(base_dir)
        wb._atomic_write_text(report_path, report)
        logger.info("变化报告已生成: %s", report_path)
    except Exception as e:  # noqa: BLE001
        wb._record_write_failure("变化报告", report_path, str(e))
    try:
        archive_state.save_state(state, wb._state_path, wb._atomic_write_text)
    except Exception as e:  # noqa: BLE001
        wb._record_write_failure("状态索引保存", wb._state_path, str(e))
