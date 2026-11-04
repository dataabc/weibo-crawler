"""单条微博解析。"""

from __future__ import annotations

import logging

from util.core import archive_state

logger = logging.getLogger("weibo")


def get_one_weibo(wb, info) -> object:
    """获取一条微博的全部信息"""
    try:
        weibo_info = info["mblog"]
        weibo_id = weibo_info["id"]
        retweeted_status = weibo_info.get("retweeted_status")
        is_long = (
            True if weibo_info.get("pic_num") > 9 else weibo_info.get("isLongText")
        )
        if retweeted_status and retweeted_status.get("id"):  # 转发
            retweet_id = retweeted_status.get("id")
            is_long_retweet = retweeted_status.get("isLongText")
            if is_long:
                weibo = wb.get_long_weibo(weibo_id)
                if not weibo:
                    weibo = wb.parse_weibo(weibo_info)
                    weibo["long_text_failed"] = True
            else:
                weibo = wb.parse_weibo(weibo_info)
            if is_long_retweet:
                retweet = wb.get_long_weibo(retweet_id)
                if not retweet:
                    retweet = wb.parse_weibo(retweeted_status)
                    retweet["long_text_failed"] = True
            else:
                retweet = wb.parse_weibo(retweeted_status)
            (
                retweet["created_at"],
                retweet["full_created_at"],
            ) = wb.standardize_date(retweeted_status["created_at"])
            weibo["retweet"] = retweet
        else:  # 原创
            if is_long:
                weibo = wb.get_long_weibo(weibo_id)
                if not weibo:
                    weibo = wb.parse_weibo(weibo_info)
                    weibo["long_text_failed"] = True
            else:
                weibo = wb.parse_weibo(weibo_info)
        weibo["created_at"], weibo["full_created_at"] = wb.standardize_date(
            weibo_info["created_at"]
        )
        edit_count = weibo_info.get("edit_count", 0)
        weibo["edited"] = edit_count > 0
        weibo["edit_count"] = edit_count
        # 可见性与最后编辑时间（编辑检测与变化报告用）
        weibo["edit_at"] = str(weibo_info.get("edit_at") or "")
        weibo["visible"] = archive_state.normalize_visibility(
            weibo_info.get("visible")
        )
        return weibo
    except Exception as e:  # noqa: BLE001 兜底：单点失败不阻断主流程
        # 单条微博解析失败不静默：计数并在本轮结束时告警，
        # 便于发现接口字段变化导致的大面积漏抓。
        wb.crawl_success = getattr(wb, "crawl_success", None) or {}
        wb.crawl_success["parse_failures"] = (
            int(wb.crawl_success.get("parse_failures", 0)) + 1
        )
        logger.exception("解析微博失败（已跳过该条）: %s", e)
