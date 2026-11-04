#!/usr/bin/env python3
"""单条/多条微博状态探针：分别用 cookie 与无 cookie 请求 statuses/show。

用法：
    .venv/bin/python scripts/probe_weibo_status.py <微博ID> [<微博ID> ...]
    .venv/bin/python scripts/probe_weibo_status.py --no-cookie-only <ID>
    .venv/bin/python scripts/probe_weibo_status.py --detail <ID>

输出每条的原始 JSON 和解析字段（ok/errno/msg/visible/user），用于验证
公开、仅粉丝可见、好友圈、仅自己可见、已删除等状态。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

PROJECT_ROOT = os.getcwd()
for path in (PROJECT_ROOT, os.path.join(PROJECT_ROOT, "scripts")):
    if path not in sys.path:
        sys.path.insert(0, path)

import requests  # noqa: E402

import weibo_comment_backfill as bf  # noqa: E402
from util.config import config_paths  # noqa: E402

STATUS_URL = "https://m.weibo.cn/statuses/show"
DETAIL_URL = "https://m.weibo.cn/detail/%s"


def probe(session, headers, weibo_id: str, label: str) -> dict:
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    print("=" * 70)
    print(f"[{label}] 微博ID: {weibo_id}  查询时间: {started}")
    try:
        response = session.get(
            STATUS_URL, params={"id": weibo_id}, headers=headers, timeout=20
        )
        print("HTTP:", response.status_code, " 响应长度:", len(response.content))
        print("原始响应:", response.text[:1000])
        try:
            payload = response.json()
        except ValueError:
            print("解析: 非 JSON 响应")
            return {}
        data = payload.get("data") if isinstance(payload, dict) else None
        print(
            "解析: ok=%r errno=%r msg=%r error_type=%r extra=%r"
            % (
                payload.get("ok"),
                payload.get("errno"),
                payload.get("msg"),
                payload.get("error_type"),
                payload.get("extra"),
            )
        )
        if isinstance(data, dict):
            user = data.get("user") or {}
            print(
                "作者: %s (uid=%s)  visible=%r  发布时间=%r  正文开头=%r"
                % (
                    user.get("screen_name"),
                    user.get("id"),
                    data.get("visible"),
                    data.get("created_at"),
                    str(data.get("text") or "")[:40].replace("\n", " "),
                )
            )
        else:
            print("data: 无")
        return payload
    except Exception as exc:  # noqa: BLE001
        print("请求异常:", type(exc).__name__, exc)
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description="微博状态探针（cookie / 无 cookie）")
    parser.add_argument("ids", nargs="+", help="微博ID")
    parser.add_argument("--no-cookie-only", action="store_true", help="只跑无 cookie 请求")
    parser.add_argument("--cookie-only", action="store_true", help="只跑 cookie 请求")
    parser.add_argument("--detail", action="store_true", help="额外请求详情页")
    parser.add_argument("--delay", type=float, default=1.0, help="每条间隔秒")
    args = parser.parse_args()

    config_path, _ = config_paths.config_files(PROJECT_ROOT)
    config = bf.load_config(config_path)
    cookie_session = bf.build_session(config)
    headers = dict(bf.HEADERS or {})
    anon_headers = dict(headers)
    anon_headers.pop("Cookie", None)
    anon_session = requests.Session()
    anon_session.headers.update(anon_headers)

    for weibo_id in args.ids:
        if not args.no_cookie_only:
            probe(cookie_session, headers, weibo_id, "cookie")
        if not args.cookie_only:
            probe(anon_session, anon_headers, weibo_id, "无 cookie")
        if args.detail:
            print("-" * 70)
            print("[detail] HTTP 响应开头:")
            try:
                resp = cookie_session.get(
                    DETAIL_URL % weibo_id, headers=headers, timeout=20
                )
                print("HTTP:", resp.status_code, " len:", len(resp.content))
                print(resp.text[:600])
            except Exception as exc:  # noqa: BLE001
                print("detail 异常:", exc)
        time.sleep(max(0.0, args.delay))
    return 0


if __name__ == "__main__":
    sys.exit(main())
