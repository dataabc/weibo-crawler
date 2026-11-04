"""SQLite 存储后端。

建表/连接/插入/查询/解析；依赖的 wb 属性（sqlite_db_path、
store_binary_in_sqlite 等）由 Weibo 实例提供。mongo 后端仍在 weibo.py。
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from collections import OrderedDict
from pathlib import Path

from util.media import media_check
from util.report import reporting

logger = logging.getLogger("weibo")


def sqlite_insert(wb, con, data, table) -> None:
    """通用 INSERT OR REPLACE（原代码引用但方法缺失，迁移时补齐）。"""
    if not data:
        return
    columns = list(data.keys())
    placeholders = ",".join("?" for _ in columns)
    sql = "INSERT OR REPLACE INTO %s (%s) VALUES (%s)" % (
        table, ",".join(columns), placeholders
    )
    con.execute(sql, list(data.values()))
    con.commit()


def get_sqlite_connection(wb) -> object:
    """等价原 Weibo.get_sqlite_connection。"""
    path = wb.get_sqlte_path()
    con = sqlite3.connect(path)
    # 无论库文件是否新建都执行 CREATE TABLE IF NOT EXISTS，避免缺表时
    # 评论与转发入库失败。
    try:
        wb.create_sqlite_table(connection=con)
    except sqlite3.Error as e:
        logger.warning("初始化 SQLite 表结构失败（继续使用已有表）: %s", e)
    return con


def create_sqlite_table(wb, connection) -> None:
    """等价原 Weibo.create_sqlite_table。"""
    sql = wb.get_sqlite_create_sql()
    cur = connection.cursor()
    cur.executescript(sql)
    connection.commit()


def get_sqlte_path(wb) -> object:
    """等价原 Weibo.get_sqlte_path。"""
    return wb.sqlite_db_path


def get_sqlite_create_sql(wb) -> object:
    """等价原 Weibo.get_sqlite_create_sql。"""
    create_sql = """
            CREATE TABLE IF NOT EXISTS user (
                id varchar(64) NOT NULL
                ,nick_name varchar(64) NOT NULL
                ,gender varchar(6)
                ,follower_count integer
                ,follow_count integer
                ,birthday varchar(10)
                ,location varchar(32)
                ,ip_location varchar(32)
                ,edu varchar(32)
                ,company varchar(32)
                ,reg_date DATETIME
                ,main_page_url text
                ,avatar_url text
                ,bio text
                ,PRIMARY KEY (id)
            );

            CREATE TABLE IF NOT EXISTS weibo (
                id varchar(20) NOT NULL
                ,bid varchar(12) NOT NULL
                ,user_id varchar(20)
                ,screen_name varchar(30)
                ,text varchar(2000)
                ,article_url varchar(100)
                ,topics varchar(200)
                ,at_users varchar(1000)
                ,pics varchar(3000)
                ,video_url varchar(1000)
                ,live_photo_url varchar(1000)
                ,location varchar(100)
                ,created_at DATETIME
                ,source varchar(30)
                ,attitudes_count INT
                ,comments_count INT
                ,reposts_count INT
                ,retweet_id varchar(20)
                ,edited BOOLEAN DEFAULT 0
                ,edit_count INT DEFAULT 0
                ,PRIMARY KEY (id)
            );

            CREATE TABLE IF NOT EXISTS bins (
                id integer PRIMARY KEY AUTOINCREMENT
                ,ext varchar(10) NOT NULL /*file extension*/
                ,data blob NOT NULL
                ,weibo_id varchar(20)
                ,comment_id varchar(20)
                ,path text
                ,url text
            );

            CREATE TABLE IF NOT EXISTS comments (
                id varchar(20) NOT NULL
                ,bid varchar(20) NOT NULL
                ,weibo_id varchar(32) NOT NULL
                ,root_id varchar(20)
                ,user_id varchar(20) NOT NULL
                ,created_at varchar(20)
                ,user_screen_name varchar(64) NOT NULL
                ,user_avatar_url text
                ,text varchar(1000)
                ,pic_url text
                ,like_count integer
                ,PRIMARY KEY (id)
            );

            CREATE TABLE IF NOT EXISTS reposts (
                id varchar(20) NOT NULL
                ,bid varchar(20) NOT NULL
                ,weibo_id varchar(32) NOT NULL
                ,user_id varchar(20) NOT NULL
                ,created_at varchar(20)
                ,user_screen_name varchar(64) NOT NULL
                ,user_avatar_url text
                ,text varchar(1000)
                ,like_count integer
                ,PRIMARY KEY (id)
            );
            """
    return create_sql


def insert_file_sqlite(wb, file_path, weibo_id, url, binary) -> None:
    """等价原 Weibo.insert_file_sqlite。"""
    if not weibo_id:
        return
    if wb.store_binary_in_sqlite != 1:  # 新增配置判断
        return
    extension = Path(file_path).suffix
    if not extension:
        return
    if len(binary) <= 0:
        return

    file_data = OrderedDict()
    file_data["weibo_id"] = weibo_id
    file_data["ext"] = extension
    file_data["data"] = binary  # 仅当启用时存储二进制
    file_data["path"] = file_path
    file_data["url"] = url

    con = wb.get_sqlite_connection()
    wb.sqlite_insert(con, file_data, "bins")
    con.close()


def sqlite_exist_file(wb, url) -> bool:
    """等价原 Weibo.sqlite_exist_file。"""
    if not os.path.exists(wb.get_sqlte_path()):
        return True
    con = wb.get_sqlite_connection()
    cur = con.cursor()

    query_sql = """SELECT url FROM bins WHERE path=? """
    count = cur.execute(query_sql, (url,)).fetchone()
    con.close()
    if count is None:
        return False

    return True


def sqlite_insert_weibo(wb, con, weibo) -> None:
    """等价原 Weibo.sqlite_insert_weibo。"""
    sqlite_weibo = wb.parse_sqlite_weibo(weibo)
    wb.sqlite_insert(con, sqlite_weibo, "weibo")


def parse_sqlite_weibo(wb, weibo) -> object:
    """等价原 Weibo.parse_sqlite_weibo。"""
    if not weibo:
        return
    sqlite_weibo = OrderedDict()
    sqlite_weibo["user_id"] = weibo["user_id"]
    sqlite_weibo["id"] = weibo["id"]
    sqlite_weibo["bid"] = weibo["bid"]
    sqlite_weibo["screen_name"] = weibo["screen_name"]
    sqlite_weibo["text"] = weibo["text"]
    sqlite_weibo["article_url"] = weibo["article_url"]
    sqlite_weibo["topics"] = weibo["topics"]
    sqlite_weibo["pics"] = weibo["pics"]
    sqlite_weibo["video_url"] = weibo["video_url"]
    sqlite_weibo["live_photo_url"] = weibo["live_photo_url"]
    sqlite_weibo["location"] = weibo["location"]
    sqlite_weibo["created_at"] = weibo["full_created_at"]
    sqlite_weibo["source"] = weibo["source"]
    sqlite_weibo["attitudes_count"] = weibo["attitudes_count"]
    sqlite_weibo["comments_count"] = weibo["comments_count"]
    sqlite_weibo["reposts_count"] = weibo["reposts_count"]
    sqlite_weibo["retweet_id"] = weibo["retweet_id"]
    sqlite_weibo["at_users"] = weibo["at_users"]
    sqlite_weibo["edited"] = weibo.get("edited", False)
    sqlite_weibo["edit_count"] = weibo.get("edit_count", 0)
    return sqlite_weibo


def sqlite_insert_comments(wb, weibo, comments) -> None:
    """等价原 Weibo.sqlite_insert_comments。"""
    if not comments or len(comments) == 0:
        return
    con = wb.get_sqlite_connection()
    for comment in comments:
        data = wb.parse_sqlite_comment(comment, weibo)
        wb.sqlite_insert(con, data, "comments")
        if "comments" in comment and isinstance(comment["comments"], list):
            for c in comment["comments"]:
                data = wb.parse_sqlite_comment(c, weibo)
                wb.sqlite_insert(con, data, "comments")
    con.close()


def parse_sqlite_comment(wb, comment, weibo) -> object:
    """等价原 Weibo.parse_sqlite_comment。"""
    if not comment:
        return
    sqlite_comment = OrderedDict()
    sqlite_comment["id"] = comment.get("id", "")

    wb._try_get_value("bid", "bid", sqlite_comment, comment)
    wb._try_get_value("root_id", "rootid", sqlite_comment, comment)
    wb._try_get_value("created_at", "created_at", sqlite_comment, comment)
    sqlite_comment["weibo_id"] = weibo.get("id", "")

    # 用户/正文/图片字段一律容错取值：接口偶发缺字段不能让整批落盘中断
    user = comment.get("user") or {}
    sqlite_comment["user_id"] = user.get("id", "")
    sqlite_comment["user_screen_name"] = user.get("screen_name", "")
    wb._try_get_value("user_avatar_url", "avatar_hd", sqlite_comment, user)
    text_raw = comment.get("text") or ""
    if wb.remove_html_tag:
        sqlite_comment["text"] = re.sub('<[^<]+?>', '', text_raw).replace('\n', '').strip()
    else:
        sqlite_comment["text"] = text_raw

    sqlite_comment["pic_url"] = ""
    pic = comment.get("pic") or {}
    large = pic.get("large") or {}
    sqlite_comment["pic_url"] = large.get("url", "") or ""
    if sqlite_comment["pic_url"] and wb.comment_pic_download:
        pic_url = sqlite_comment["pic_url"]

        # 评论图片目录：weibo/<用户目录>/<用户昵称>_comments_img
        csv_path = wb.get_filepath("csv")
        user_dir = os.path.dirname(csv_path)
        if not os.path.isdir(user_dir):
            os.makedirs(user_dir)
        screen_name = wb.user.get("screen_name") or str(
            wb.user_config.get("user_id", "")
        )
        safe_screen_name = reporting.safe_name(screen_name)
        pic_path = os.path.join(user_dir, f"{safe_screen_name}_comments_img")
        if not os.path.exists(pic_path):
            os.makedirs(pic_path)

        # 文件名包含 微博用户昵称 + weibo_id + 评论用户昵称 + comments
        # 为避免重名，如果已存在则在末尾追加 _1/_2/... 序号
        weibo_id = sqlite_comment["weibo_id"]
        comment_user = sqlite_comment.get("user_screen_name", "")
        safe_comment_user = re.sub(r'[\\/:*?"<>|]', "_", str(comment_user))
        base_name = "{screen_name}_{weibo_id}_{comment_user}_comments".format(
            screen_name=safe_screen_name,
            weibo_id=weibo_id,
            comment_user=safe_comment_user,
        )
        pic_name = base_name + ".jpg"
        idx = 1
        while os.path.exists(os.path.join(pic_path, pic_name)):
            pic_name = f"{base_name}_{idx}.jpg"
            idx += 1
        pic_full_path = os.path.join(pic_path, pic_name)
        if not os.path.exists(pic_full_path):
            last_error = ""
            for attempt in range(1, 3):
                try:
                    response = wb.session.get(pic_url, timeout=10)
                    response.raise_for_status()
                    data = response.content
                    ok, reason, _kind, detected_ext = media_check.validate_bytes(
                        data,
                        pic_url,
                        response.headers.get("Content-Type", ""),
                        ".jpg",
                    )
                    if not ok:
                        last_error = reason
                        logger.warning(
                            "评论图片校验失败(%d/2): %s（%s）",
                            attempt, pic_url[:80], reason,
                        )
                        continue
                    if detected_ext and detected_ext != ".jpg":
                        pic_full_path = (
                            os.path.splitext(pic_full_path)[0] + detected_ext
                        )
                    with open(pic_full_path, "wb") as f:
                        f.write(data)
                    logger.info("评论图片下载成功: %s", pic_full_path)
                    last_error = ""
                    break
                except Exception as e:  # noqa: BLE001 兜底：失败不阻断
                    last_error = str(e)
                    logger.warning("下载评论图片失败(%d/2): %s", attempt, e)
            if last_error:
                wb.media_failure_count = (
                    getattr(wb, "media_failure_count", 0) + 1
                )
                logger.warning(
                    "评论图片下载失败，已放弃: %s（%s）",
                    pic_url[:80], last_error,
                )
    wb._try_get_value("like_count", "like_count", sqlite_comment, comment)
    return sqlite_comment


def sqlite_insert_reposts(wb, weibo, reposts) -> None:
    """等价原 Weibo.sqlite_insert_reposts。"""
    if not reposts or len(reposts) == 0:
        return
    con = wb.get_sqlite_connection()
    for repost in reposts:
        data = wb.parse_sqlite_repost(repost, weibo)
        wb.sqlite_insert(con, data, "reposts")

    con.close()


def parse_sqlite_repost(wb, repost, weibo) -> object:
    """等价原 Weibo.parse_sqlite_repost。"""
    if not repost:
        return
    sqlite_repost = OrderedDict()
    sqlite_repost["id"] = repost.get("id", "")

    wb._try_get_value("bid", "bid", sqlite_repost, repost)
    wb._try_get_value("created_at", "created_at", sqlite_repost, repost)
    sqlite_repost["weibo_id"] = weibo.get("id", "")

    user = repost.get("user") or {}
    sqlite_repost["user_id"] = user.get("id", "")
    sqlite_repost["user_screen_name"] = user.get("screen_name", "")
    wb._try_get_value(
        "user_avatar_url", "profile_image_url", sqlite_repost, user
    )
    text = repost.get("raw_text")
    if text:
        text = text.split("//", 1)[0]
    if text is None or text == "" or text == "Repost":
        text = "转发微博"
    sqlite_repost["text"] = text
    wb._try_get_value("like_count", "attitudes_count", sqlite_repost, repost)
    return sqlite_repost


def sqlite_insert_user(wb, con, user) -> None:
    """等价原 Weibo.sqlite_insert_user。"""
    sqlite_user = wb.parse_sqlite_user(user)
    wb.sqlite_insert(con, sqlite_user, "user")


def parse_sqlite_user(wb, user) -> object:
    """等价原 Weibo.parse_sqlite_user。"""
    if not user:
        return
    sqlite_user = OrderedDict()
    sqlite_user["id"] = user["id"]
    sqlite_user["nick_name"] = user["screen_name"]
    sqlite_user["gender"] = user["gender"]
    sqlite_user["follower_count"] = user["followers_count"]
    sqlite_user["follow_count"] = user["follow_count"]
    sqlite_user["birthday"] = user["birthday"]
    sqlite_user["location"] = user["location"]
    sqlite_user["ip_location"] = user.get("ip_location", "")
    sqlite_user["edu"] = user["education"]
    sqlite_user["company"] = user["company"]
    sqlite_user["reg_date"] = user["registration_time"]
    sqlite_user["main_page_url"] = user["profile_url"]
    sqlite_user["avatar_url"] = user["avatar_hd"]
    sqlite_user["bio"] = user["description"]
    return sqlite_user
