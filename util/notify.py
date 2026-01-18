import requests
import const


def push_deer(append_str):
    params = {
        'pushkey': const.NOTIFY['PUSH_KEY'],
        'text': append_str,
    }
    # 使用HTTPS请求发送通知
    requests.get(url="https://api2.pushdeer.com/message/push", params=params)
