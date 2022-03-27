import csv
import os
import const


def insert_or_update_user(logger, headers, result_data, file_path):
    """插入或更新用户csv。不存在则插入，最新抓取微博id不填，存在则先不动，返回已抓取最新微博id和日期"""
    first_write = True if not os.path.isfile(file_path) else False
    if os.path.isfile(file_path):
        # 文件已存在，直接查看有没有，有就直接return了
        with open(file_path, 'r', encoding='utf-8') as f:
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


def update_last_weibo_id(userid, new_last_weibo_msg, file_path):
    """更新用户csv中的最新微博id"""
    lines = []
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.split(',')[0] == str(userid):
                line = line.replace(line.split(
                    ',')[len(line.split(',')) - 1], new_last_weibo_msg + '\n')
            lines.append(line)
        f.close()
    with open(file_path, 'w', encoding='utf-8') as f:
        for line in lines:
            f.write(line)
