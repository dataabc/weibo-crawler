import requests
import const


def push_deer(append_str):
    params = {
        'pushkey': const.NOTIFY['PUSH_KEY'],
        'text': append_str,
    }
    # 这里为了避免证书验证，使用http而非https
    requests.get(url="http://api2.pushdeer.com/message/push", params=params)
