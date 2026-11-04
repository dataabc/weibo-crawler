"""本轮写入失败报告。"""

from __future__ import annotations

import logging
import os
from datetime import datetime

from util.report import reporting

logger = logging.getLogger("weibo")


def generate_write_failure_report(wb) -> None:
    """生成本轮「写入失败报告」。

    只要有写入/读取失败（后端落盘失败、Markdown 读取失败等），无论本轮
    抓取是否顺利完成，都单独生成一份报告：失败不能只留在日志里。
    无失败时不生成，不产生噪音。
    """
    failures = getattr(wb, "run_failures", None)
    if not failures:
        return
    if not wb.user or not wb.user_config:
        return
    try:
        user_id = str(wb.user_config.get("user_id", "unknown"))
        screen_name = str(wb.user.get("screen_name") or user_id)

        lines = [
            "本轮运行写入失败报告",
            f"用户: {screen_name} (ID: {user_id})",
            f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"失败项: {len(failures)} 项",
            "说明: 以下步骤失败但主流程已继续；失败详情见下方清单。",
            "=" * 60,
        ]
        for idx, item in enumerate(failures, start=1):
            lines.append(
                f"{idx}. [{item.get('time', '?')}] {item.get('stage', '?')}"
            )
            if item.get("target"):
                lines.append(f"   目标: {item['target']}")
            if item.get("error"):
                lines.append(f"   原因: {item['error']}")
            lines.append("")
        lines.append("报告完毕。")

        base_dir = wb._report_output_dir(
            getattr(wb, "crawl_summary_dir", "report")
        )
        os.makedirs(base_dir, exist_ok=True)
        report_path = os.path.join(
            base_dir,
            reporting.report_filename(
                reporting.PREFIX_WRITE_FAILURES, user_id, screen_name
            ),
        )
        wb._atomic_write_text(report_path, "\n".join(lines) + "\n")
        logger.warning(
            "本轮写入失败报告已生成: %s（%d 项失败）", report_path, len(failures)
        )
    except Exception as e:  # noqa: BLE001 兜底：单点失败不阻断主流程
        logger.exception("生成写入失败报告失败: %s", e)
