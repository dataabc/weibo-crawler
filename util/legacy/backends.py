"""MySQL / MongoDB / POST 推送后端。

本模块是自包含实现；``Weibo`` 提供同名薄封装，``write_mode`` 含
``mysql``/``mongo``/``mongodb``/``post`` 时由 ``write_data`` /
``user_to_database`` 调用。

- **未验证**：未在真实数据库/服务上运行过；
- **失败不退出主流程**：依赖缺失（pymongo/pymysql 未装）或数据库连不上时
  记 warning 并返回 False，其他写入后端继续。

⚠️ 这些后端**没有在真实 MySQL/MongoDB/POST 接口上验证过**（见
``docs/未验证功能.md``），可能存在字段/连接/事务层面的 bug。
"""

from __future__ import annotations

import copy
import logging

logger = logging.getLogger("weibo")


def user_to_mongodb(wb):
    """将爬取的用户信息写入MongoDB数据库"""
    user_list = [wb.user]
    info_to_mongodb(wb, "user", user_list)
    logger.info("%s信息写入MongoDB数据库完毕", wb.user["screen_name"])


def user_to_mysql(wb):
    """将爬取的用户信息写入MySQL数据库"""
    mysql_config = {
        "host": "localhost",
        "port": 3306,
        "user": "root",
        "password": "123456",
        "charset": "utf8mb4",
    }
    # 创建'weibo'数据库
    create_database = """CREATE DATABASE IF NOT EXISTS weibo DEFAULT
                     CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"""
    if not mysql_create_database(wb, mysql_config, create_database):
        return False
    # 创建'user'表
    create_table = """
            CREATE TABLE IF NOT EXISTS user (
            id varchar(20) NOT NULL,
            screen_name varchar(30),
            gender varchar(10),
            statuses_count INT,
            followers_count INT,
            follow_count INT,
            registration_time varchar(20),
            sunshine varchar(20),
            birthday varchar(40),
            location varchar(200),
            ip_location varchar(50),
            education varchar(200),
            company varchar(200),
            description varchar(400),
            profile_url varchar(200),
            profile_image_url varchar(200),
            avatar_hd varchar(200),
            urank INT,
            mbrank INT,
            verified BOOLEAN DEFAULT 0,
            verified_type INT,
            verified_reason varchar(140),
            PRIMARY KEY (id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
    if not mysql_create_table(wb, mysql_config, create_table):
        return False
    if not mysql_insert(wb, mysql_config, "user", [wb.user]):
        return False
    logger.info("%s信息写入MySQL数据库完毕", wb.user["screen_name"])


def write_post(wb, wrote_count):
    """将爬到的信息通过POST发出"""
    data = {}
    data['user'] = wb.user
    weibo_info = wb.weibo[wrote_count:]
    if data.get('weibo'):
        data['weibo'] += weibo_info
    else:
        data['weibo'] = weibo_info

    if data:
        wb.send_post_request_with_token(wb.post_config["api_url"], data, wb.post_config["api_token"], 3, 2)
        logger.info(u'%d条微博通过POST发送到 %s', len(data['weibo']), wb.post_config["api_url"])
    else:
        logger.info(u'没有获取到微博，略过API POST')


def info_to_mongodb(wb, collection, info_list):
    """将爬取的信息写入MongoDB数据库"""
    try:
        import pymongo
    except ImportError:
        logger.warning("系统中可能没有安装 pymongo，跳过 MongoDB 写入（pip install pymongo）")
        return False
    try:
        from pymongo import MongoClient

        client = MongoClient(wb.mongodb_URI)
        db = client["weibo"]
        collection = db[collection]
        if len(wb.write_mode) > 1:
            new_info_list = copy.deepcopy(info_list)
        else:
            new_info_list = info_list
        for info in new_info_list:
            if not collection.find_one({"id": info["id"]}):
                collection.insert_one(info)
            else:
                collection.update_one({"id": info["id"]}, {"$set": info})
    except pymongo.errors.ServerSelectionTimeoutError:
        logger.warning("MongoDB 连接失败（未安装或未启动），跳过本次写入")
        return False
    return True


def weibo_to_mongodb(wb, wrote_count):
    """将爬取的微博信息写入MongoDB数据库"""
    info_to_mongodb(wb, "weibo", wb.weibo[wrote_count:])
    logger.info("%d条微博写入MongoDB数据库完毕", wb.got_count)


def mysql_create(wb, connection, sql):
    """创建MySQL数据库或表"""
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql)
    finally:
        connection.close()


def mysql_create_database(wb, mysql_config, sql):
    """创建MySQL数据库"""
    try:
        import pymysql
    except ImportError:
        logger.warning("系统中可能没有安装 pymysql，跳过 MySQL 写入（pip install pymysql）")
        return False
    try:
        if wb.mysql_config:
            mysql_config = wb.mysql_config
        connection = pymysql.connect(**mysql_config)
        mysql_create(wb, connection, sql)
    except pymysql.OperationalError:
        logger.warning("MySQL 连接失败（未安装或未正确配置 mysql_config），跳过本次写入")
        return False
    return True


def mysql_create_table(wb, mysql_config, sql):
    """创建MySQL表；依赖缺失/连接失败时返回 False（不退出主流程）。"""
    try:
        import pymysql
    except ImportError:
        logger.warning("系统中可能没有安装 pymysql，跳过 MySQL 写入（pip install pymysql）")
        return False

    if wb.mysql_config:
        mysql_config = wb.mysql_config
    mysql_config["db"] = "weibo"
    try:
        connection = pymysql.connect(**mysql_config)
    except pymysql.OperationalError:
        logger.warning("MySQL 连接失败（未安装或未正确配置 mysql_config），跳过建表")
        return False
    mysql_create(wb, connection, sql)
    return True


def mysql_insert(wb, mysql_config, table, data_list):
    """向 MySQL 表插入或更新数据；只取建表列，返回是否成功。

    只取建表列：解析结果里新增的字段（如 live_photo_indices、links）
    不会造成 MySQL 1054 错误。
    """
    import pymysql

    if not data_list:
        return True
    # 只保留 weibo 建表语句里存在的列（见 weibo_to_mysql 的 CREATE TABLE）
    allowed = (
        "id", "bid", "user_id", "screen_name", "text", "article_url",
        "topics", "at_users", "pics", "video_url", "live_photo_url",
        "location", "created_at", "source", "attitudes_count",
        "comments_count", "reposts_count", "retweet_id", "edited",
        "edit_count",
    )
    rows = [
        {key: data.get(key) for key in allowed if key in data}
        for data in data_list
        if isinstance(data, dict)
    ]
    rows = [row for row in rows if row]
    if not rows:
        logger.warning("MySQL 写入跳过：没有可写入的建表列（表 %s）", table)
        return False
    keys = ", ".join(rows[0].keys())
    values = ", ".join(["%s"] * len(rows[0]))
    if wb.mysql_config:
        mysql_config = dict(wb.mysql_config)
    else:
        mysql_config = dict(mysql_config)
    mysql_config["db"] = "weibo"
    connection = pymysql.connect(**mysql_config)
    cursor = connection.cursor()
    sql = """INSERT INTO {table}({keys}) VALUES ({values}) ON
             DUPLICATE KEY UPDATE""".format(
        table=table, keys=keys, values=values
    )
    update = ",".join([" {key} = values({key})".format(key=key) for key in rows[0]])
    sql += update
    try:
        cursor.executemany(sql, [tuple(data.values()) for data in rows])
        connection.commit()
        return True
    except Exception as e:  # noqa: BLE001 兜底：失败不阻断
        connection.rollback()
        logger.error("MySQL 写入失败（表 %s）: %s", table, e)
        return False
    finally:
        connection.close()


def weibo_to_mysql(wb, wrote_count):
    """将爬取的微博信息写入MySQL数据库"""
    mysql_config = {
        "host": "localhost",
        "port": 3306,
        "user": "root",
        "password": "123456",
        "charset": "utf8mb4",
    }
    # 创建'weibo'表
    create_table = """
            CREATE TABLE IF NOT EXISTS weibo (
            id varchar(20) NOT NULL,
            bid varchar(12) NOT NULL,
            user_id varchar(20),
            screen_name varchar(30),
            text text,
            article_url varchar(100),
            topics varchar(200),
            at_users varchar(1000),
            pics varchar(3000),
            video_url varchar(1000),
            live_photo_url varchar(1000),
            location varchar(100),
            created_at DATETIME,
            source varchar(30),
            attitudes_count INT,
            comments_count INT,
            reposts_count INT,
            retweet_id varchar(20),
            edited BOOLEAN DEFAULT 0,
            edit_count INT DEFAULT 0,
            PRIMARY KEY (id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
    if not mysql_create_table(wb, mysql_config, create_table):
        return False

    # 要插入的微博列表
    weibo_list = []
    # 要插入的转发微博列表
    retweet_list = []
    if len(wb.write_mode) > 1:
        info_list = copy.deepcopy(wb.weibo[wrote_count:])
    else:
        info_list = wb.weibo[wrote_count:]
    for w in info_list:
        w["created_at"] = w["full_created_at"]
        del w["full_created_at"]

        if "retweet" in w:
            r = w["retweet"]
            r["retweet_id"] = ""
            r["created_at"] = r["full_created_at"]
            del r["full_created_at"]
            retweet_list.append(r)
            w["retweet_id"] = r["id"]
            del w["retweet"]
        else:
            w["retweet_id"] = ""
        weibo_list.append(w)
    # 在'weibo'表中插入或更新微博数据
    retweet_ok = mysql_insert(wb, mysql_config, "weibo", retweet_list)
    weibo_ok = mysql_insert(wb, mysql_config, "weibo", weibo_list)
    if retweet_ok and weibo_ok:
        logger.info("%d条微博写入MySQL数据库完毕", wb.got_count)
    else:
        logger.error(
            "MySQL 写入未完成（转发 %s / 微博 %s）；请检查表结构与 mysql_config",
            "成功" if retweet_ok else "失败",
            "成功" if weibo_ok else "失败",
        )
