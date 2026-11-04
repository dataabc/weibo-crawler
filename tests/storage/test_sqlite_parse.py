"""SQLite 存储后端的解析/插入测试。"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests import _support  # noqa: E402

_support.bootstrap()

import weibo  # noqa: E402
from util.storage import sqlite as sqlite_mod  # noqa: E402


def make_wb(tmpdir=None, **attrs):
    wb = weibo.Weibo.__new__(weibo.Weibo)
    wb.remove_html_tag = 0
    wb.comment_pic_download = 0
    wb.store_binary_in_sqlite = 0
    wb.user = {"screen_name": "测试用户"}
    wb.user_config = {"user_id": "1"}
    if tmpdir:
        wb.sqlite_db_path = os.path.join(tmpdir, "weibodata.db")
    for key, value in attrs.items():
        setattr(wb, key, value)
    return wb


def weibo_row(**overrides):
    base = {
        "user_id": "1",
        "id": "100",
        "bid": "b1",
        "screen_name": "测试用户",
        "text": "正文",
        "article_url": "",
        "topics": "话题",
        "pics": "",
        "video_url": "",
        "live_photo_url": "",
        "location": "上海",
        "full_created_at": "2024-01-01 00:00:00",
        "source": "iPhone",
        "attitudes_count": 1,
        "comments_count": 2,
        "reposts_count": 3,
        "retweet_id": "",
        "at_users": "",
    }
    base.update(overrides)
    return base


class ParseSqliteUserTest(unittest.TestCase):
    def test_maps_all_fields(self):
        user = {
            "id": "1",
            "screen_name": "测试用户",
            "gender": "f",
            "followers_count": 10,
            "follow_count": 20,
            "birthday": "1990-01-01",
            "location": "上海",
            "ip_location": "上海",
            "education": "某大学",
            "company": "某公司",
            "registration_time": "2010-01-01",
            "profile_url": "https://m.weibo.cn/profile/1",
            "avatar_hd": "https://x/a.jpg",
            "description": "简介",
        }
        row = sqlite_mod.parse_sqlite_user(make_wb(), user)
        self.assertEqual(row["id"], "1")
        self.assertEqual(row["nick_name"], "测试用户")
        self.assertEqual(row["follower_count"], 10)
        self.assertEqual(row["follow_count"], 20)
        self.assertEqual(row["ip_location"], "上海")
        self.assertEqual(row["avatar_url"], "https://x/a.jpg")

    def test_ip_location_defaults_to_empty(self):
        user = {
            "id": "1",
            "screen_name": "n",
            "gender": "m",
            "followers_count": 0,
            "follow_count": 0,
            "birthday": "",
            "location": "",
            "education": "",
            "company": "",
            "registration_time": "",
            "profile_url": "",
            "avatar_hd": "",
            "description": "",
        }
        self.assertEqual(sqlite_mod.parse_sqlite_user(make_wb(), user)["ip_location"], "")

    def test_empty_user_returns_none(self):
        self.assertIsNone(sqlite_mod.parse_sqlite_user(make_wb(), {}))


class ParseSqliteWeiboTest(unittest.TestCase):
    def test_maps_all_fields(self):
        row = sqlite_mod.parse_sqlite_weibo(make_wb(), weibo_row(edited=True, edit_count=2))
        self.assertEqual(row["id"], "100")
        self.assertEqual(row["created_at"], "2024-01-01 00:00:00")
        self.assertEqual(row["topics"], "话题")
        self.assertTrue(row["edited"])
        self.assertEqual(row["edit_count"], 2)

    def test_edited_defaults_when_missing(self):
        row = sqlite_mod.parse_sqlite_weibo(make_wb(), weibo_row())
        self.assertFalse(row["edited"])
        self.assertEqual(row["edit_count"], 0)

    def test_empty_weibo_returns_none(self):
        self.assertIsNone(sqlite_mod.parse_sqlite_weibo(make_wb(), {}))


class ParseSqliteCommentTest(unittest.TestCase):
    def _comment(self, **overrides):
        comment = {
            "id": "c1",
            "bid": "cb1",
            "rootid": "",
            "created_at": "2024-01-01T00:00:00",
            "user": {"id": "u1", "screen_name": "评论者", "avatar_hd": "https://x/u.jpg"},
            "text": "评论正文",
            "pic": {"large": {"url": ""}},
            "like_count": 5,
        }
        comment.update(overrides)
        return comment

    def test_maps_all_fields(self):
        row = sqlite_mod.parse_sqlite_comment(
            make_wb(), self._comment(), {"id": "100"}
        )
        self.assertEqual(row["id"], "c1")
        self.assertEqual(row["weibo_id"], "100")
        self.assertEqual(row["user_id"], "u1")
        self.assertEqual(row["user_screen_name"], "评论者")
        self.assertEqual(row["user_avatar_url"], "https://x/u.jpg")
        self.assertEqual(row["text"], "评论正文")
        self.assertEqual(row["like_count"], 5)

    def test_remove_html_tag_strips_tags(self):
        wb = make_wb(remove_html_tag=1)
        row = sqlite_mod.parse_sqlite_comment(
            wb, self._comment(text="<b>加粗</b>\n换行"), {"id": "100"}
        )
        self.assertEqual(row["text"], "加粗换行")

    def test_missing_user_is_tolerated(self):
        row = sqlite_mod.parse_sqlite_comment(
            make_wb(), self._comment(user=None), {"id": "100"}
        )
        self.assertEqual(row["user_id"], "")
        self.assertEqual(row["user_screen_name"], "")

    def test_empty_comment_returns_none(self):
        self.assertIsNone(sqlite_mod.parse_sqlite_comment(make_wb(), {}, {"id": "100"}))


class ParseSqliteRepostTest(unittest.TestCase):
    def _repost(self, **overrides):
        repost = {
            "id": "r1",
            "bid": "rb1",
            "created_at": "2024-01-01T00:00:00",
            "user": {"id": "u1", "screen_name": "转发者", "profile_image_url": "https://x/p.jpg"},
            "raw_text": "转发内容//@某人: 原微博",
            "attitudes_count": 3,
        }
        repost.update(overrides)
        return repost

    def test_maps_and_truncates_quoted_text(self):
        row = sqlite_mod.parse_sqlite_repost(make_wb(), self._repost(), {"id": "100"})
        self.assertEqual(row["id"], "r1")
        self.assertEqual(row["weibo_id"], "100")
        self.assertEqual(row["user_screen_name"], "转发者")
        self.assertEqual(row["text"], "转发内容")
        self.assertEqual(row["like_count"], 3)

    def test_placeholder_when_text_empty(self):
        for raw in ("", "Repost", None):
            row = sqlite_mod.parse_sqlite_repost(
                make_wb(), self._repost(raw_text=raw), {"id": "100"}
            )
            self.assertEqual(row["text"], "转发微博")


class SqliteInsertTest(unittest.TestCase):
    def test_sqlite_insert_skips_empty_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb = make_wb(tmpdir)
            con = wb.get_sqlite_connection()
            sqlite_mod.sqlite_insert(wb, con, {}, "user")  # 不抛异常即可
            con.close()

    def test_insert_and_read_back_user(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb = make_wb(tmpdir)
            user = {
                "id": "1",
                "screen_name": "测试用户",
                "gender": "f",
                "followers_count": 1,
                "follow_count": 2,
                "birthday": "",
                "location": "",
                "education": "",
                "company": "",
                "registration_time": "",
                "profile_url": "",
                "avatar_hd": "",
                "description": "",
            }
            con = wb.get_sqlite_connection()
            sqlite_mod.sqlite_insert_user(wb, con, user)
            row = con.execute("SELECT nick_name FROM user WHERE id='1'").fetchone()
            con.close()
            self.assertEqual(row[0], "测试用户")

    def test_insert_weibo_and_comments(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb = make_wb(tmpdir)
            con = wb.get_sqlite_connection()
            sqlite_mod.sqlite_insert_weibo(wb, con, weibo_row())
            con.close()
            wb.sqlite_insert_comments(
                {"id": "100"},
                [
                    {
                        "id": "c1",
                        "text": "一级",
                        "user": {"id": "u", "screen_name": "n"},
                        "comments": [
                            {"id": "c2", "text": "二级", "user": {"id": "u2", "screen_name": "n2"}}
                        ],
                    }
                ],
            )
            con = wb.get_sqlite_connection()
            ids = {row[0] for row in con.execute("SELECT id FROM comments").fetchall()}
            con.close()
            self.assertEqual(ids, {"c1", "c2"})

    def test_insert_file_requires_binary_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb = make_wb(tmpdir, store_binary_in_sqlite=0)
            sqlite_mod.insert_file_sqlite(wb, "/tmp/a.jpg", "100", "https://x/a.jpg", b"data")
            self.assertFalse(os.path.exists(wb.sqlite_db_path))

    def test_insert_file_with_binary_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb = make_wb(tmpdir, store_binary_in_sqlite=1)
            sqlite_mod.insert_file_sqlite(wb, "/tmp/a.jpg", "100", "https://x/a.jpg", b"data")
            con = wb.get_sqlite_connection()
            row = con.execute("SELECT path FROM bins").fetchone()
            con.close()
            self.assertEqual(row[0], "/tmp/a.jpg")

    def test_insert_file_skips_missing_extension_or_binary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wb = make_wb(tmpdir, store_binary_in_sqlite=1)
            sqlite_mod.insert_file_sqlite(wb, "/tmp/noext", "100", "u", b"data")
            sqlite_mod.insert_file_sqlite(wb, "/tmp/a.jpg", "100", "u", b"")
            self.assertFalse(os.path.exists(wb.sqlite_db_path))


if __name__ == "__main__":
    unittest.main()
