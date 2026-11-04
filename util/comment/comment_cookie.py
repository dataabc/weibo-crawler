"""评论/转发抓取的 Cookie 刷新逻辑。

``_get_weibo_comments_cookie`` / ``_get_weibo_comments_nocookie`` /
``_get_weibo_reposts_cookie``：分页抓取评论/转发，处理 Cookie 失效重试、
回调写库/CSV；依赖的 wb（session/headers 等）由 Weibo 实例提供。
"""

from __future__ import annotations

import logging
import random
from time import sleep

logger = logging.getLogger("weibo")


def _get_weibo_comments_cookie(wb, weibo, cur_count, max_count, max_id, on_downloaded):
    """
    :weibo standardlized weibo
    :cur_count  已经下载的评论数
    :max_count 最大允许下载数
    :max_id 微博返回的max_id参数
    :on_downloaded 下载完成时的实例方法回调
    """
    if cur_count >= max_count:
        return

    id = weibo["id"]
    params = {"mid": id}
    if max_id:
        params["max_id"] = max_id
    url = "https://m.weibo.cn/comments/hotflow?max_id_type=0"
    req = wb.session.get(
        url,
        params=params,
        headers=wb.headers,
    )
    json = None
    error = False
    try:
        json = req.json()
    except Exception:  # noqa: BLE001 兜底：单点失败不阻断主流程
        # 没有cookie会抓取失败
        # 微博日期小于某个日期的用这个url会被403 需要用老办法尝试一下
        error = True

    if error:
        # 无 cookie 时单次最多约 50 条
        wb._get_weibo_comments_nocookie(weibo, 0, max_count, 1, on_downloaded)
        return

    data = json.get("data")
    if not data:
        # 新接口没有抓取到的老接口也试一下
        wb._get_weibo_comments_nocookie(weibo, 0, max_count, 1, on_downloaded)
        return

    comments = data.get("data")
    count = len(comments)
    if count == 0:
        # 没有了可以直接跳出递归
        return

    if on_downloaded:
        on_downloaded(weibo, comments)

    # 随机睡眠一下
    if max_count % 40 == 0:
        sleep(random.randint(1, 5))

    cur_count += count
    max_id = data.get("max_id")

    if max_id == 0:
        return

    wb._get_weibo_comments_cookie(
        weibo, cur_count, max_count, max_id, on_downloaded
    )


def _get_weibo_comments_nocookie(wb, weibo, cur_count, max_count, page, on_downloaded):
    """
    :weibo standardlized weibo
    :cur_count  已经下载的评论数
    :max_count 最大允许下载数
    :page 下载的页码 从 1 开始
    :on_downloaded 下载完成时的实例方法回调
    """
    if cur_count >= max_count:
        return
    id = weibo["id"]
    url = "https://m.weibo.cn/api/comments/show?id={id}&page={page}".format(
        id=id, page=page
    )
    req = wb.session.get(url)
    json = None
    try:
        json = req.json()
    except Exception:  # noqa: BLE001 兜底：单点失败不阻断主流程
        logger.warning("未能抓取完整评论 微博id: {id}".format(id=id))
        return

    data = json.get("data")
    if not data:
        return
    comments = data.get("data")
    count = len(comments)
    if count == 0:
        # 没有了可以直接跳出递归
        return

    if on_downloaded:
        on_downloaded(weibo, comments)

    cur_count += count
    page += 1

    # 随机睡眠一下
    if page % 2 == 0:
        sleep(random.randint(1, 5))

    req_page = data.get("max")

    if req_page == 0:
        return

    if page > req_page:
        return
    wb._get_weibo_comments_nocookie(
        weibo, cur_count, max_count, page, on_downloaded
    )


def _get_weibo_reposts_cookie(wb, weibo, cur_count, max_count, page, on_downloaded):
    """
    :weibo standardlized weibo
    :cur_count  已经下载的转发数
    :max_count 最大允许下载数
    :page 下载的页码 从 1 开始
    :on_downloaded 下载完成时的实例方法回调
    """
    if cur_count >= max_count:
        return
    id = weibo["id"]
    url = "https://m.weibo.cn/api/statuses/repostTimeline"
    params = {"id": id, "page": page}
    req = wb.session.get(
        url,
        params=params,
        headers=wb.headers,
    )

    json = None
    try:
        json = req.json()
    except Exception:  # noqa: BLE001 兜底：单点失败不阻断主流程
        logger.warning(
            "未能抓取完整转发 微博id: {id}".format(id=id)
        )
        return

    data = json.get("data")
    if not data:
        return
    reposts = data.get("data")
    count = len(reposts)
    if count == 0:
        # 没有了可以直接跳出递归
        return

    if on_downloaded:
        on_downloaded(weibo, reposts)

    cur_count += count
    page += 1

    # 随机睡眠一下
    if page % 2 == 0:
        sleep(random.randint(2, 5))

    req_page = data.get("max")

    if req_page == 0:
        return

    if page > req_page:
        return
    wb._get_weibo_reposts_cookie(weibo, cur_count, max_count, page, on_downloaded)
