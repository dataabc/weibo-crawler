import csv
import os

from util.core import atomic_io


def insert_or_update_user(logger, headers, result_data, file_path) -> object:
    """插入或更新用户csv。不存在则插入，最新抓取微博id不填，存在则先不动，返回已抓取最新微博id和日期"""
    first_write = True if not os.path.isfile(file_path) else False
    if os.path.isfile(file_path):
        # 文件已存在，直接查看有没有，有就直接return了
        with open(file_path, 'r', encoding='utf-8-sig') as f:
            for line in f:
                if line.split(',')[0] == result_data[0][0]:
                    return line.split(',')[len(line.split(',')) - 1].replace('\n', '')

    # 没有或者新建
    result_data[0].append('')
    with open(file_path, 'a', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        if first_write:
            writer.writerows([headers])
        writer.writerows(result_data)
    logger.info('{} 信息写入csv文件完毕，保存路径: {}'.format(result_data[0][1], file_path))
    return ''


def update_last_weibo_id(userid, new_last_weibo_msg, file_path) -> None:
    """更新用户csv中的最新微博id（原子写，避免进程中断损坏 users.csv）。"""
    lines = []
    with open(file_path, 'r', encoding='utf-8-sig') as f:
        for line in f:
            if line.split(',')[0] == str(userid):
                line = line.replace(line.split(
                    ',')[len(line.split(',')) - 1], new_last_weibo_msg + '\n')
            lines.append(line)
    # 保留 utf-8-sig（BOM），与其它 CSV 读取/写入路径一致，Excel 打开中文不乱码
    atomic_io.atomic_write_bytes(file_path, "".join(lines).encode("utf-8-sig"))
