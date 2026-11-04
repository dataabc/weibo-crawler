'util/http_client.py 单元测试。'

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from util.http import http_client  # noqa: E402


class _Response:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {"ok": 1}


class _Session:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Response()


class _BadSession:
    def get(self, url, **kwargs):
        raise ValueError("bad json")


class HttpClientTest(unittest.TestCase):
    def test_random_headers_disabled_returns_fallback(self):
        fallback = {"a": 1}
        result = http_client.random_headers(0, {"user_agents": ["x"]}, fallback)
        self.assertIs(result, fallback)

    def test_random_headers_enabled(self):
        result = http_client.random_headers(
            1,
            {
                "user_agents": ["UA-1"],
                "accept_languages": ["zh-CN"],
                "referer_list": ["https://m.weibo.cn/"],
            },
            {},
        )
        self.assertEqual(result["user-agent"], "UA-1")
        self.assertEqual(result["accept-language"], "zh-CN")
        self.assertEqual(result["Referer"], "https://m.weibo.cn/")

    def test_fetch_json_success(self):
        session = _Session()
        payload, status = http_client.fetch_json(session, "https://x", {"p": 1})
        self.assertEqual(payload, {"ok": 1})
        self.assertEqual(status, 200)
        self.assertEqual(session.calls[0][1]["params"], {"p": 1})

    def test_fetch_json_bad_json_returns_500(self):
        payload, status = http_client.fetch_json(_BadSession(), "https://x", {})
        self.assertEqual(payload, {})
        self.assertEqual(status, 500)
