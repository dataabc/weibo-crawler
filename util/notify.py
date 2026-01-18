import requests
import const


def push_deer(append_str):
    params = {
        'pushkey': const.NOTIFY['PUSH_KEY'],
        'text': append_str,
    }
    # 使用HTTPS，如果在这个环境中遇到证书问题，可以设置 verify=False
    requests.get(url="https://api2.pushdeer.com/message/push", params=params, verify=False)
