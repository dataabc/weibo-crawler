
import datetime


# def convert_to_days_ago(date_str, how_many_days):
#     """将日期字符串转换为多少天前的日期字符串"""
#     date_str = datetime.datetime.strptime(date_str, '%Y-%m-%d')
#     date_str = date_str + datetime.timedelta(days=-how_many_days)
#     return date_str.strftime('%Y-%m-%d')

def convert_to_days_ago(date_str, how_many_days):
    """将日期字符串转换为多少天前的日期字符串"""
    date_str = datetime.datetime.strptime(date_str, '%Y-%m-%dT%H:%M:%S')
    date_str = date_str + datetime.timedelta(days=-how_many_days)
    return date_str.strftime('%Y-%m-%dT%H:%M:%S')
