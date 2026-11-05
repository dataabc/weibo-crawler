"""文件存储后端：CSV / JSON。

``write_csv`` 按 ID 去重追加；``write_json`` 原子写 + 损坏文件备份重建；
``update_json_data`` 按 ID 合并（重试/补抓不重复）。sqlite/mongo 后端仍在
weibo.py（下一阶段迁移）。依赖的 wb 方法由 Weibo 实例提供。
"""

from __future__ import annotations

import csv
import json
import logging
import os
from datetime import datetime

logger = logging.getLogger("weibo")


def write_csv(wb, wrote_count) -> None:
    """将爬到的信息写入csv文件（按 ID 去重，重试/补抓不产生重复行）。

    写入前按文件已有 ID 过滤，重抓同一窗口不会产生重复行。
    """
    write_info = wb.get_write_info(wrote_count)
    result_headers = wb.get_result_headers()
    # 转成 list：get_write_info 返回 OrderedDict，dict.values() 不可下标，
    # 去重时需要按列取值（values() 不可下标）
    result_data = [list(w.values()) for w in write_info]
    file_path = wb.get_filepath("csv")
    if result_headers and result_headers[0] == "id" and os.path.isfile(file_path):
        existing_ids = wb._read_csv_ids(file_path)
        new_rows = [
            row for row in result_data if str(row[0]).strip() not in existing_ids
        ]
        skipped = len(result_data) - len(new_rows)
        if skipped:
            logger.info("CSV 去重：跳过 %d 条已存在的微博", skipped)
        result_data = new_rows
    if result_data:
        wb.csv_helper(result_headers, result_data, file_path)


def csv_helper(wb, headers, result_data, file_path) -> None:
    """将指定信息写入csv文件"""
    is_first_write = not os.path.isfile(file_path)
    # Python 2 分支（codecs.BOM_UTF8 + ab 模式）已删：项目要求 Python >= 3.10，
    # py3 的 utf-8-sig 已负责写 BOM。
    with open(file_path, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        if is_first_write:
            writer.writerows([headers])
        writer.writerows(result_data)
    if headers[0] == "id":
        logger.info("%d条微博写入csv文件完毕,保存路径:", wb.got_count)
    else:
        logger.info("%s 信息写入csv文件完毕，保存路径:", wb.user["screen_name"])
    logger.info(file_path)


def _read_csv_ids(wb, file_path):
    """读取已有微博 CSV 的第一列 ID 集合（用于重试/补抓去重）。"""
    ids = set()
    try:
        with open(file_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            next(reader, None)  # 跳过表头
            for row in reader:
                if row:
                    ids.add(str(row[0]).strip())
    except (OSError, csv.Error) as e:
        logger.warning("读取已有 CSV 失败，跳过 CSV 去重: %s", e)
    return ids


def write_json(wb, wrote_count) -> None:
    """将爬到的信息写入json文件（原子写 + 损坏文件自动备份重建）。

    原子写 + 损坏文件自动备份重建：中断/断电不会毁掉整个 JSON，
    读取失败也不会阻断后续落盘步骤。
    """
    path = wb.get_filepath("json")
    data = {}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("JSON 根节点不是对象")
        except (ValueError, OSError) as e:
            # 损坏文件先改名保留现场，再降级为空数据重建；不让它阻断后续落盘
            backup_path = (
                f"{path}.corrupt.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            )
            try:
                os.replace(path, backup_path)
                logger.error(
                    "JSON 结果文件损坏或无法读取（%s），已备份为 %s；本轮将重建该文件",
                    e,
                    backup_path,
                )
            except OSError as backup_error:
                logger.error(
                    "JSON 结果文件损坏或无法读取（%s），且备份失败（%s）；本轮将重建该文件",
                    e,
                    backup_error,
                )
            data = {}
    weibo_info = wb.weibo[wrote_count:]
    data = wb.update_json_data(data, weibo_info)
    wb._atomic_write_text(path, json.dumps(data, ensure_ascii=False))
    logger.info("%d条微博写入json文件完毕,保存路径:", wb.got_count)
    logger.info(path)


def update_json_data(wb, data, weibo_info) -> object:
    """更新 json 数据：已存在的同 ID 微博用新值覆盖，不存在的追加。

    始终按 ID 合并，保证重试/补抓不产生重复对象。
    """
    data["user"] = wb.user
    weibo_list = data.get("weibo")
    if not isinstance(weibo_list, list):
        weibo_list = []
        data["weibo"] = weibo_list
    index = {}
    for i, old in enumerate(weibo_list):
        if isinstance(old, dict) and old.get("id") is not None:
            index[str(old.get("id"))] = i
    for new in weibo_info:
        if not isinstance(new, dict) or new.get("id") is None:
            weibo_list.append(new)
            continue
        key = str(new.get("id"))
        if key in index:
            weibo_list[index[key]] = new
        else:
            index[key] = len(weibo_list)
            weibo_list.append(new)
    return data
