import argparse
from time import sleep

import schedule

import const
import weibo
from util.misc.notify import push_deer


def main(schedule_interval):
    """
    主函数，用于设置定时任务和执行微博爬虫脚本。

    Parameters:
        schedule_interval (int): 循环间隔，以分钟为单位。

    Returns:
        None
    """
    schedule.every(schedule_interval).minutes.do(weibo.main)  # 每隔指定的时间间隔执行一次main函数
    weibo.logger.info('循环间隔设置为%d分钟', schedule_interval)

    weibo.main()  # 立即执行一次
    while True:
        try:
            schedule.run_pending()
            sleep(1)
        except KeyboardInterrupt:
            break
        except Exception as error:
            # 先记日志再判断通知：旧实现把 logger.exception 缩进在 if NOTIFY 内，
            # 默认 NOTIFY=False 时调度循环异常完全没有日志（2026-10-31 审计 C23）。
            weibo.logger.exception(error)
            if const.NOTIFY["NOTIFY"]:
                push_deer(f"weibo-crawler运行出错, 错误为{error}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('schedule_interval', type=int, help='循环间隔（分钟）')
    args = parser.parse_args()

    main(args.schedule_interval)
