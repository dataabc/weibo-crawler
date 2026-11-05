"""微博分页抓取。

``get_one_page`` 抓取并解析一页，``get_pages`` 负责分页循环、结束判定与
防封禁统计；依赖的 wb 方法（get_weibo_json/initialize_info/write_data 等）
由 Weibo 实例提供。搬运自 weibo.py，逻辑与行为保持不变。
"""

from __future__ import annotations

import logging
import random
import sys
import time
from datetime import datetime
from time import sleep

import const
from tqdm import tqdm

from util.core import archive_index
from util.core import csvutil
from util.core import errors
from util.core.dateutil import convert_to_days_ago
from util.core.time_format import DTFORMAT
from util.misc.notify import push_deer

logger = logging.getLogger("weibo")


def _is_foreign_weibo(one_weibo: dict, user_id) -> bool:
    """判断时间线卡片里的 mblog 是否属于别的作者（推荐/广告/热门卡片）。

    ``only_crawl_original=1`` 只过滤「转发」，但时间线接口有时会混入其他作者的
    推荐/广告原微博；这类卡片没有 retweet 字段，会被误当成原创存档，需要按作者
    再过滤一次。
    """
    target = str(user_id or "").strip()
    if not target:
        return False
    return str(one_weibo.get("user_id") or "").strip() != target



def get_one_page(wb, page) -> object:
    """获取一页的全部微博"""
    try:
        js = wb.get_weibo_json(page)
        if js is None:
            # 接口无更多数据：分页自然结束
            wb.crawl_success["api_ended"] = True
            logger.info("第%d页接口返回无更多数据，分页结束", page)
            return True
        if not js:
            # 请求重试耗尽（get_weibo_json 返回 {}）：本页失败，交给完成判定处理
            wb.crawl_success.setdefault("page_errors", []).append(page)
            wb.crawl_success["success"] = False
            wb.crawl_success["reason"] = f"第{page}页请求失败"
            return
        if js.get("ok"):
            weibos = js["data"]["cards"]

            if wb.query:
                weibos = weibos[0]["card_group"]
            # 本轮内不变的配置只解析一次
            since_date = datetime.strptime(wb.user_config["since_date"], DTFORMAT)
            end_date = (
                datetime.strptime(wb.user_config["end_date"], DTFORMAT)
                if wb.user_config.get("end_date")
                else None
            )
            # 如果需要检查cookie，在循环第一个人的时候，就要看看仅自己可见的信息有没有，要是没有直接报错
            for w in weibos:
                if w["card_type"] == 11:
                    temp = w.get("card_group",[0])
                    if len(temp) >= 1:
                        w = temp[0] or w
                if w["card_type"] == 9:
                    one_weibo = wb.get_one_weibo(w)
                    if one_weibo:
                        if not getattr(wb, "query", "") and _is_foreign_weibo(
                            one_weibo, (wb.user or {}).get("id")
                        ):
                            wb.skipped_foreign_count = (
                                getattr(wb, "skipped_foreign_count", 0) + 1
                            )
                            logger.warning(
                                "跳过非本人微博（疑似推荐/广告/热门卡片）: id=%s 作者=%s",
                                one_weibo.get("id"),
                                one_weibo.get("screen_name"),
                            )
                            continue
                        if (
                            const.CHECK_COOKIE["CHECK"]
                            and (not const.CHECK_COOKIE["CHECKED"])
                            and one_weibo["text"].startswith(
                                const.CHECK_COOKIE["HIDDEN_WEIBO"]
                            )
                        ):
                            const.CHECK_COOKIE["CHECKED"] = True
                            logger.info("cookie检查通过")
                            if const.CHECK_COOKIE["EXIT_AFTER_CHECK"]:
                                return True
                        if one_weibo["id"] in wb.weibo_id_list:
                            continue
                        created_at = datetime.strptime(one_weibo["created_at"], DTFORMAT)
                        # end_date 过滤：微博按从新到旧排列，晚于截止时间的跳过继续
                        if end_date is not None:
                            if created_at > end_date:
                                # 检查是否为置顶微博
                                is_pinned = w.get("mblog", {}).get("mblogtype", 0) == 2
                                if is_pinned:
                                    logger.debug(f"[置顶微博] 微博ID={one_weibo['id']}, 发布时间={created_at}, 是置顶微博，跳过但继续检查后续微博")
                                else:
                                    logger.debug(f"[截止日期过滤] 微博ID={one_weibo['id']}, 发布时间={created_at}, 截止时间={end_date}, 已跳过")
                                continue
                        if const.MODE == "append":
                            # append模式：增量获取微博
                            if wb.first_crawler:
                                # 记录最新微博id，写入上次抓取id的csv
                                wb.latest_weibo_id = str(one_weibo["id"])
                                csvutil.update_last_weibo_id(
                                    one_weibo["user_id"],
                                    str(one_weibo["id"]) + " " + one_weibo["created_at"],
                                    wb.user_csv_file_path,
                                )
                                wb.first_crawler = False
                            if str(one_weibo["id"]) == wb.last_weibo_id:
                                if const.CHECK_COOKIE["CHECK"] and (
                                    not const.CHECK_COOKIE["CHECKED"]
                                ):
                                    # 已经爬取过最新的了，只是没检查到cookie，一旦检查通过，直接放行
                                    const.CHECK_COOKIE["EXIT_AFTER_CHECK"] = True
                                    continue
                                if wb.last_weibo_id == wb.latest_weibo_id:
                                    logger.info(
                                        "{} 用户没有发新微博".format(
                                            wb.user["screen_name"]
                                        )
                                    )
                                else:
                                    logger.info(
                                        "增量获取微博完毕，将最新微博id从 {} 变更为 {}".format(
                                            wb.last_weibo_id, wb.latest_weibo_id
                                        )
                                    )
                                # append 模式的正常结束信号（供 _finalize_crawl_success 判定）
                                wb.crawl_success["append_done"] = True
                                return True
                            # 上一次标记的微博被删了：把上一条微博时间记录推前两天，多抓点评论或者微博内容修改
                            since_date = datetime.strptime(
                                convert_to_days_ago(wb.last_weibo_date, 1),
                                DTFORMAT,
                            )
                        if created_at < since_date:
                            # 检查是否为置顶微博
                            is_pinned = w.get("mblog", {}).get("mblogtype", 0) == 2
                            if is_pinned:
                                logger.debug(f"[置顶微博] 微博ID={one_weibo['id']}, 发布时间={created_at}, 是置顶微博，跳过但继续检查后续微博")
                                continue

                            logger.debug(f"[日期过滤] 微博ID={one_weibo['id']}, 发布时间={created_at}, 起始时间={since_date}, 已跳过")
                            # 如果要检查还没有检查cookie，不能直接跳出
                            if const.CHECK_COOKIE["CHECK"] and (
                                not const.CHECK_COOKIE["CHECKED"]
                            ):
                                continue
                            else:
                                wb.crawl_success["hit_since_date"] = True
                                logger.info(
                                    "{}已获取{}({})的第{}页{}微博{}".format(
                                        "-" * 30,
                                        wb.user["screen_name"],
                                        wb.user["id"],
                                        page,
                                        '包含"' + wb.query + '"的'
                                        if wb.query
                                        else "",
                                        "-" * 30,
                                    )
                                )
                                return True
                        else:
                            logger.debug(f"[日期通过] 微博ID={one_weibo['id']}, 发布时间={created_at}, 起始时间={since_date}")
                        if str(one_weibo["id"]) in getattr(wb, "known_invisible_ids", set()):
                            wb.skipped_invisible_count = (
                                getattr(wb, "skipped_invisible_count", 0) + 1
                            )
                            logger.info("跳过已确认删除、隐藏或人工忽略的微博（不写入）: %s", one_weibo["id"])
                            continue
                        if (not wb.only_crawl_original) or ("retweet" not in one_weibo.keys()):
                            wb.weibo.append(one_weibo)
                            wb.weibo_id_list.append(one_weibo["id"])
                            wb.got_count += 1

                            # 防封禁：累计统计
                            # 暂停判断挪到整页处理完后进行，避免在页处理中途退出导致当前页剩余微博被跳过而丢失
                            wb.update_crawl_stats(weibo_count=1)

                            # 这里是系统日志输出，尽量别太杂
                            logger.info(
                                "已获取用户 {} 的微博，内容为 {}".format(
                                    wb.user["screen_name"], one_weibo["text"]
                                )
                            )
                        else:
                            wb.filtered_retweet_count = (
                                getattr(wb, "filtered_retweet_count", 0) + 1
                            )
                            logger.info("正在过滤转发微博")

            # 防封禁：当前页全部微博处理完毕后再判断是否需要休息，
            # 避免页处理中途退出,当前页剩余微博被永久跳过。
            if wb.anti_ban_enabled:
                should_pause, reason = wb.should_pause_session()
                if should_pause:
                    logger.warning(f"触发防封禁暂停: {reason}")
                    return "need_rest"

            if const.CHECK_COOKIE["CHECK"] and not const.CHECK_COOKIE["CHECKED"]:
                logger.warning("经检查，cookie无效，系统退出")
                if const.NOTIFY["NOTIFY"]:
                    push_deer("经检查，cookie无效，系统退出")
                sys.exit()
        else:
            wb.crawl_success["api_ended"] = True
            return True
        logger.info(
            "{}已获取{}({})的第{}页微博{}".format(
                "-" * 30, wb.user["screen_name"], wb.user["id"], page, "-" * 30
            )
        )
    except Exception as e:  # noqa: BLE001 兜底：失败不阻断
        wb.crawl_success.setdefault("page_errors", []).append(page)
        wb.crawl_success["success"] = False
        wb.crawl_success["reason"] = f"第{page}页异常: {e}"
        logger.exception(e)


def get_pages(wb) -> None:
    """获取全部微博"""
    wb.crawl_success = {
        "attempted": False,
        "success": False,
        "reason": "未开始",
        "hit_since_date": False,
        "api_ended": False,
        "page_errors": [],
        "pages_processed": 0,
    }
    try:
        # 用户id不可用
        if wb.get_user_info() != 0:
            wb.crawl_success["reason"] = "用户信息获取失败"
            return
        logger.info("准备搜集 {} 的微博".format(wb.user["screen_name"]))
        wb.crawl_success["attempted"] = True
        wb.crawl_success["reason"] = "爬取中"

        # 已确认不可见的微博（已确认不可见的微博 + ignore_ids.txt）：本轮不写入，避免全量运行把人工删除的帖子补回
        wb.known_invisible_ids = set()
        if "markdown" in wb.write_mode:
            # 先占位：get_filepath 本身失败时 except 里仍要能安全取到目标
            md_dir = ""
            try:
                md_dir = wb.get_filepath("markdown")
                wb.known_invisible_ids = archive_index.collect_invisible_weibo_ids(
                    md_dir, wb._archive_exclude_dirs()
                )
                wb._known_invisible_loaded = True
                if wb.known_invisible_ids:
                    logger.info("已加载已确认不可见的微博 %d 条（不写入/不补回）", len(wb.known_invisible_ids))
            except Exception as e:  # noqa: BLE001 兜底：失败不阻断
                # 失败时 known_invisible_ids 保持空集，全量轮存在把人工
                # 删除/隐藏的微博补回存档的风险（数据污染），必须留堆栈并写进
                # 失败报告，不能只留一行 warning。
                if errors.is_programming_error(e):
                    logger.exception(
                        "读取已确认不可见的微博出现程序性错误（疑似代码缺陷；"
                        "本轮按空集继续，存在补回已删除微博的风险）: %s",
                        e,
                    )
                else:
                    logger.exception(
                        "读取已确认不可见的微博（已确认不可见的微博/"
                        "ignore_ids.txt）失败，本轮按空集继续: %s",
                        e,
                    )
                wb._record_write_failure(
                    "不可见清单读取", md_dir, errors.describe_exception(e)
                )
                # 标记失败，写盘后核对时回退重算，不用空集做核对
                wb._known_invisible_load_failed = True

        # 防封禁：初始化爬取统计
        if wb.anti_ban_enabled:
            wb.crawl_stats["start_time"] = time.time()
            cfg = wb.anti_ban_config
            logger.info("🛡️ 防封禁模式已启用")
            logger.info("┌────────────────────────────────────┐")
            logger.info("│ 每会话最大微博数: %-17d│", cfg['max_weibo_per_session'])
            logger.info("│ 批次大小: %-8d 批次延迟: %3d秒 │", cfg['batch_size'], cfg['batch_delay'])
            logger.info("│ 请求延迟: %d-%d秒                   │", cfg['request_delay_min'], cfg['request_delay_max'])
            logger.info("│ 最大会话时间: %-7d秒            │", cfg['max_session_time'])
            logger.info("│ 最大API错误数: %-20d│", cfg['max_api_errors'])
            logger.info("└────────────────────────────────────┘")

        if const.MODE == "append" and (
            "first_crawler" not in wb.__dict__ or wb.first_crawler is False
        ):

            wb.first_crawler = True
        since_date = datetime.strptime(wb.user_config["since_date"], DTFORMAT)
        today = datetime.today()
        if since_date <= today:    # since_date 若为未来则无需执行
            page_count = wb.get_page_count()
            wb.crawl_success["page_count"] = page_count
            if page_count is None:
                wb.crawl_success["reason"] = (
                    "微博页数计算失败（检查 page_weibo_count 配置与用户 statuses_count）"
                )
                return
            wrote_count = 0
            page1 = 0
            random_pages = random.randint(1, 5)
            wb.start_date = datetime.now().strftime(DTFORMAT)
            wb.crawl_success["window_start"] = wb.user_config["since_date"]
            wb.crawl_success["window_end"] = wb.start_date
            wb._snapshot_archived_ids()
            pages = range(wb.start_page, page_count + 1)
            last_page = 0
            for page in tqdm(pages, desc="Progress"):
                last_page = page
                wb.crawl_success["pages_processed"] = last_page
                is_end = wb.get_one_page(page)

                # 防封禁：检查是否需要休息
                if is_end == "need_rest":
                    # 先写入已爬取的数据
                    wb.write_data(wrote_count)
                    wrote_count = wb.got_count

                    # 执行休息
                    wb.perform_anti_ban_rest()

                    # 重置统计，继续爬取
                    wb.reset_crawl_stats()
                    continue

                if is_end:
                    break

                # 防封禁：页失败/API 错误累积时检查暂停条件（仅在出现错误时调用，
                # 避免正常页触发随机休息）。
                if wb.anti_ban_enabled and (
                    wb.crawl_success.get("page_errors")
                    or wb.crawl_stats.get("api_errors", 0)
                    >= wb.anti_ban_config.get("max_api_errors", 5)
                ):
                    pause, pause_reason = wb.should_pause_session()
                    if pause:
                        logger.warning(
                            "防封禁触发暂停条件（%s），本轮提前结束并保留已抓数据",
                            pause_reason,
                        )
                        wb.crawl_success["reason"] = f"防封禁暂停: {pause_reason}"
                        break

                # 防封禁：检查批次延迟
                if wb.anti_ban_enabled:
                    wb.check_batch_delay()

                if page % 20 == 0:  # 每爬20页写入一次文件
                    wb.write_data(wrote_count)
                    wrote_count = wb.got_count

                # 防封禁：使用更保守的延迟，可按配置调整
                if wb.anti_ban_enabled:
                    # 如果启用了防封禁，使用更保守的延迟
                    if (page - page1) % random_pages == 0 and page < page_count:
                        delay = random.randint(8, 12)  # 更保守的延迟
                        sleep(delay)
                        page1 = page
                        random_pages = random.randint(1, 5)
                else:
                    # 默认延迟逻辑
                    if (page - page1) % random_pages == 0 and page < page_count:
                        sleep(random.randint(6, 10))
                        page1 = page
                        random_pages = random.randint(1, 5)

            wb.write_data(wrote_count)  # 将剩余不足20页的微博写入文件
            wb._finalize_crawl_success(last_page, page_count)
        else:
            wb.crawl_success["reason"] = "起始时间在未来，本轮未爬取"

        # 防封禁：输出统计信息
        if wb.anti_ban_enabled:
            session_time = time.time() - wb.crawl_stats["start_time"]
            logger.info(f"防封禁统计: 微博={wb.crawl_stats['weibo_count']}, 请求={wb.crawl_stats['request_count']}, 错误={wb.crawl_stats['api_errors']}, 耗时={int(session_time)}秒")

        parse_failures = int(wb.crawl_success.get("parse_failures", 0) or 0)
        if parse_failures:
            logger.warning(
                "本轮有 %d 条微博解析失败（已跳过、未写入）；"
                "若数量明显增加，请检查接口字段是否变化",
                parse_failures,
            )
        logger.info("微博爬取完成，共爬取%d条微博", wb.got_count)
    except Exception as e:  # noqa: BLE001 兜底：失败不阻断
        wb.crawl_success["success"] = False
        wb.crawl_success["reason"] = f"异常中断: {e}"
        logger.exception(e)
