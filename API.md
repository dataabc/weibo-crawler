# Weibo API 说明文档

本文档详细介绍了基于 Flask 框架构建的 Weibo API 的各个端点及其使用方法。API 主要用于刷新微博数据、查询任务状态以及获取微博信息。

## 目录
1. [概述](#概述)
2. [配置说明](#配置说明)
3. [API 端点](#api-端点)
    - [刷新微博数据](#刷新微博数据)
    - [查询任务状态](#查询任务状态)
    - [获取所有微博](#获取所有微博)
    - [获取单条微博详情](#获取单条微博详情)
4. [定时任务](#定时任务)
5. [错误处理](#错误处理)
6. [日志记录](#日志记录)

---

## 概述

该 API 旨在通过定时任务和手动触发的任务来抓取和管理微博数据。主要功能包括：
- 刷新指定用户的微博数据
- 查询任务的执行状态
- 获取所有抓取到的微博
- 获取单条微博的详细信息

## 配置说明

在运行 API 之前，需要配置service.py相关参数。配置项包括用户ID列表、数据库路径、日志配置、下载选项等。

以下是主要配置项的说明：

```python
config = {
    "user_id_list": [
        "6067225218", 
        "1445403190"
    ],
    "only_crawl_original": 1,
    "since_date": 1,
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
```

- **user_id_list**: 需要抓取微博的用户ID列表。
- **only_crawl_original**: 是否仅抓取原创微博（1为是，0为否）。
- **since_date**: 起始日期。
- **start_page**: 起始页码。
- **write_mode**: 数据存储模式，可选择 CSV、JSON、SQLite 等。
- **original_pic_download**: 是否下载原创微博的图片（1为是，0为否）。
- **retweet_pic_download**: 是否下载转发微博的图片（1为是，0为否）。
- **original_video_download**: 是否下载原创微博的视频（1为是，0为否）。
- **retweet_video_download**: 是否下载转发微博的视频（1为是，0为否）。
- **download_comment**: 是否下载评论（1为是，0为否）。
- **comment_max_download_count**: 评论的最大下载数量。
- **download_repost**: 是否下载转发信息（1为是，0为否）。
- **repost_max_download_count**: 转发的最大下载数量。
- **user_id_as_folder_name**: 是否使用用户ID作为文件夹名称（1为是，0为否）。
- **remove_html_tag**: 是否移除HTML标签（1为是，0为否）。
- **cookie**: 用于认证的微博Cookie。
- **mysql_config**: MySQL数据库的配置信息。
- **mongodb_URI**: MongoDB的连接URI。
- **post_config**: POST请求的配置，包括API URL和Token。

## API 端点

### 刷新微博数据

**URL:** `/refresh`

**方法:** `POST`

**描述:** 手动触发刷新指定用户的微博数据。

**请求参数:**

- `user_id_list` (可选): 需要刷新微博数据的用户ID列表。格式为JSON数组。例如：
  ```json
  {
      "user_id_list": ["6067225218", "1445403190"]
  }
  ```

**响应:**

- **202 Accepted**
  ```json
  {
      "task_id": "任务ID",
      "status": "Task started",
      "state": "PENDING",
      "progress": 0,
      "user_id_list": ["6067225218", "1445403190"]
  }
  ```
- **400 Bad Request** (参数无效)
  ```json
  {
      "error": "Invalid user_id_list parameter"
  }
  ```
- **409 Conflict** (已有任务在运行)
  ```json
  {
      "task_id": "当前运行的任务ID",
      "status": "Task already running",
      "state": "PROGRESS",
      "progress": 50
  }
  ```

### 查询任务状态

**URL:** `/task/<task_id>`

**方法:** `GET`

**描述:** 查询指定任务的状态和进度。

**URL 参数:**

- `task_id` (必需): 需要查询的任务ID。

**响应:**

- **200 OK**
  ```json
  {
      "state": "任务状态",
      "progress": 进度百分比,
      "result": {
          "message": "微博列表已刷新"
      }
  }
  ```
  或者在任务失败时：
  ```json
  {
      "state": "FAILED",
      "progress": 50,
      "error": "错误信息"
  }
  ```
- **404 Not Found** (任务不存在)
  ```json
  {
      "error": "Task not found"
  }
  ```

### 获取所有微博

**URL:** `/weibos`

**方法:** `GET`

**描述:** 获取数据库中所有抓取到的微博，按创建时间倒序排列。

**响应:**

- **200 OK**
  ```json
  [
      {
          "id": "微博ID",
          "content": "微博内容",
          "created_at": "创建时间",
          ...
      },
      ...
  ]
  ```
- **500 Internal Server Error** (服务器错误)
  ```json
  {
      "error": "错误信息"
  }
  ```

### 获取单条微博详情

**URL:** `/weibos/<weibo_id>`

**方法:** `GET`

**描述:** 获取指定ID的微博详细信息。

**URL 参数:**

- `weibo_id` (必需): 需要获取的微博ID。

**响应:**

- **200 OK**
  ```json
  {
      "id": "微博ID",
      "content": "微博内容",
      "created_at": "创建时间",
      ...
  }
  ```
- **404 Not Found** (微博不存在)
  ```json
  {
      "error": "Weibo not found"
  }
  ```
- **500 Internal Server Error** (服务器错误)
  ```json
  {
      "error": "错误信息"
  }
  ```

## 定时任务

API 启动后，会在后台启动一个定时任务线程，每隔10分钟自动触发一次刷新任务，以确保微博数据的及时更新。如果当前有任务正在运行，定时任务会跳过本次执行。

**定时任务行为:**
- 检查是否有正在运行的任务。
- 如果没有正在运行的任务，则创建并启动一个新的刷新任务。
- 如果有任务在运行，则等待下一个周期。

**错误处理:**
- 如果定时任务执行过程中发生错误，会记录日志并在1分钟后重试。

## 错误处理

API 在处理请求时可能会遇到各种错误，主要包括：

- **400 Bad Request:** 请求参数无效。
- **404 Not Found:** 请求的资源不存在（如任务ID或微博ID）。
- **409 Conflict:** 资源冲突，如已有任务在运行。
- **500 Internal Server Error:** 服务器内部错误。

错误响应的格式统一为包含 `error` 字段的 JSON 对象，例如：

```json
{
    "error": "错误信息"
}
```

## 日志记录

API 使用 Python 的 `logging` 模块进行日志记录。日志文件存储在 `log/` 目录下，默认日志配置文件为 `logging.conf`。主要记录以下内容：

- 服务启动和关闭日志
- 任务的启动、完成和失败日志
- 定时任务的执行情况
- 异常和错误信息

确保 `log/` 目录存在，API 会在启动时自动创建该目录（如果不存在）。

---

## 启动和运行

确保所有依赖库已安装，并正确配置了 `logging.conf` 文件和数据库路径。启动 API 的命令如下：

```bash
python service.py
```

API 将在默认的 `5000` 端口启动，并自动开始监听请求和执行定时任务。

---

## 注意事项

- **并发任务控制:** API 限制同一时间只能运行一个刷新任务，以避免数据冲突和资源竞争。
- **数据存储:** 默认使用 SQLite 数据库存储微博数据，配置中可根据需要调整为其他存储方式（如 MySQL、MongoDB）。
- **安全性:** 确保 `cookie` 和数据库的敏感信息安全存储，避免泄露。

如有任何疑问或问题，请参考源代码中的注释或联系开发团队。
