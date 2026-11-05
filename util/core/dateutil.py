"""日期小工具（主流程仅 fetch_pages 使用 convert_to_days_ago）。"""

import datetime

from util.core.time_format import DTFORMAT


def convert_to_days_ago(date_str, how_many_days) -> object:
    """将日期字符串转换为多少天前的日期字符串（格式口径见 time_format.DTFORMAT）。"""
    date_str = datetime.datetime.strptime(date_str, DTFORMAT)
    date_str = date_str + datetime.timedelta(days=-how_many_days)
    return date_str.strftime(DTFORMAT)
