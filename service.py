from weibo import Weibo, handle_config_renaming
import const
import logging
import logging.config
import os
from flask import Flask, jsonify, request
import sqlite3
import json
from concurrent.futures import ThreadPoolExecutor
import threading
import uuid
import time
from datetime import datetime

# 1896820725 天津股侠 2024-12-09T16:47:04

DATABASE_PATH = './weibo/weibodata.db'
print(DATABASE_PATH)

# 如果日志文件夹不存在，则创建
if not os.path.isdir("log/"):
    os.makedirs("log/")
logging_path = os.path.split(os.path.realpath(__file__))[0] + os.sep + "logging.conf"
logging.config.fileConfig(logging_path)
logger = logging.getLogger("api")

config = {
    "user_id_list": ["6067225218", "1445403190"],
    "only_crawl_original": 1,
    "since_date": "2024-12-08",
    "start_page": 1,
    "write_mode": [
        "csv",
        "json",
        "sqlite"
    ],
    "original_pic_download": 0,
    "retweet_pic_download": 0,
    "original_video_download": 0,
    "retweet_video_download": 0,
    "download_comment": 0,
    "comment_max_download_count": 100,
    "download_repost": 0,
    "repost_max_download_count": 100,
    "user_id_as_folder_name": 0,
    "remove_html_tag": 1,
    "cookie": "your weibo cookie",
    "mysql_config": {
        "host": "localhost",
        "port": 3306,
        "user": "root",
        "password": "123456",
        "charset": "utf8mb4"
    },
    "mongodb_URI": "mongodb://[username:password@]host[:port][/[defaultauthdb][?options]]",
    "post_config": {
        "api_url": "https://api.example.com",
        "api_token": ""
    }
}

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False  # 确保JSON响应中的中文不会被转义
app.config['JSONIFY_MIMETYPE'] = 'application/json;charset=utf-8'

# 添加线程池和任务状态跟踪
executor = ThreadPoolExecutor(max_workers=1)  # 限制只有1个worker避免并发爬取
tasks = {}  # 存储任务状态

# 在executor定义后添加任务锁相关变量
current_task_id = None
task_lock = threading.Lock()

def get_running_task():
    """获取当前运行的任务信息"""
    if current_task_id and current_task_id in tasks:
        task = tasks[current_task_id]
        if task['state'] in ['PENDING', 'PROGRESS']:
            return current_task_id, task
    return None, None

def get_config():
    # 重命名一些key, 但向前兼容
    handle_config_renaming(config, oldName="filter", newName="only_crawl_original")
    handle_config_renaming(config, oldName="result_dir_name", newName="user_id_as_folder_name")
    return config

def run_refresh_task(task_id):
    global current_task_id
    try:
        tasks[task_id]['state'] = 'PROGRESS'
        tasks[task_id]['progress'] = 0
        
        config = get_config()
        wb = Weibo(config)
        tasks[task_id]['progress'] = 50
        
        wb.start()  # 爬取微博信息
        tasks[task_id]['progress'] = 100
        tasks[task_id]['state'] = 'SUCCESS'
        tasks[task_id]['result'] = {"message": "微博列表已刷新"}
        
    except Exception as e:
        tasks[task_id]['state'] = 'FAILED'
        tasks[task_id]['error'] = str(e)
        logger.exception(e)
    finally:
        with task_lock:
            if current_task_id == task_id:
                current_task_id = None

@app.route('/refresh', methods=['POST'])
def refresh():
    global current_task_id
    
    # 检查是否有正在运行的任务
    with task_lock:
        running_task_id, running_task = get_running_task()
        if running_task:
            return jsonify({
                'task_id': running_task_id,
                'status': 'Task already running',
                'state': running_task['state'],
                'progress': running_task['progress']
            }), 409  # 409 Conflict
        
        # 创建新任务
        task_id = str(uuid.uuid4())
        tasks[task_id] = {
            'state': 'PENDING',
            'progress': 0,
            'created_at': datetime.now().isoformat()
        }
        current_task_id = task_id
        
    executor.submit(run_refresh_task, task_id)
    return jsonify({
        'task_id': task_id,
        'status': 'Task started',
        'state': 'PENDING',
        'progress': 0
    }), 202

@app.route('/task/<task_id>', methods=['GET'])
def get_task_status(task_id):
    task = tasks.get(task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404
        
    response = {
        'state': task['state'],
        'progress': task['progress']
    }
    
    if task['state'] == 'SUCCESS':
        response['result'] = task.get('result')
    elif task['state'] == 'FAILED':
        response['error'] = task.get('error')
        
    return jsonify(response)

@app.route('/weibos', methods=['GET'])
def get_weibos():
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        # 按created_at倒序查询所有微博
        cursor.execute("SELECT * FROM weibo ORDER BY created_at DESC")
        columns = [column[0] for column in cursor.description]
        weibos = []
        for row in cursor.fetchall():
            weibo = dict(zip(columns, row))
            weibos.append(weibo)
        conn.close()
        res1 = json.dumps(weibos, ensure_ascii=False)
        print(res1)
        res = jsonify(weibos)
        print(res)
        return res, 200
    except Exception as e:
        logger.exception(e)
        return {"error": str(e)}, 500

@app.route('/weibos/<weibo_id>', methods=['GET'])
def get_weibo_detail(weibo_id):
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM weibo WHERE id=?", (weibo_id,))
        columns = [column[0] for column in cursor.description]
        row = cursor.fetchone()
        conn.close()
        
        if row:
            weibo = dict(zip(columns, row))
            return jsonify(weibo), 200
        else:
            return {"error": "Weibo not found"}, 404
    except Exception as e:
        logger.exception(e)
        return {"error": str(e)}, 500

if __name__ == "__main__":
    app.run(debug=True)