{
    "user_id_list": "user_id_list.txt", // 用户ID列表文件路径或用户ID数组，格式：每行一个用户ID，或 ["123456", "789012"]
    "only_crawl_original": 0, // 是否只爬取原创微博 0 = 爬取全部（原创+转发），1 = 只爬取原创微博
    "since_date": "2026-01-01", // 开始爬取的时间范围（格式：YYYY-MM-DD），只爬取该日期之后发布的微博
    "start_page": 1, // 从第几页开始爬取，通常保持为1，断点续传时可调整
    "page_weibo_count": 20, // 每页爬取的微博数量（10-50之间），建议：10-20，太大可能被限制
    "write_mode": [
        "markdown",
        "csv",
        "json"
    ], // 输出格式（可多选），可选项：csv, json, mysql, mongo, sqlite, post, markdown，示例：["markdown"] 或 ["markdown", "csv", "json"]
    "original_pic_download": 1, // 是否下载原创微博图片 0 = 不下载，1 = 下载
    "retweet_pic_download": 1, // 是否下载转发微博图片 0 = 不下载，1 = 下载
    "original_video_download": 1, // 是否下载原创微博视频 0 = 不下载，1 = 下载
    "retweet_video_download": 0, // 是否下载转发微博视频 0 = 不下载，1 = 下载
    "original_live_photo_download": 1, // 是否下载原创微博Live Photo 0 = 不下载，1 = 下载
    "retweet_live_photo_download": 0, // 是否下载转发微博Live Photo 0 = 不下载，1 = 下载
    "download_comment": 0, // 是否下载评论 0 = 不下载，1 = 下载
    "comment_max_download_count": 100, // 单条微博最大评论下载数量， 仅当 download_comment = 1 时生效
    "download_repost": 1, // 是否下载转发 0 = 不下载，1 = 下载
    "repost_max_download_count": 100, // 单条微博最大转发下载数量， 仅当 download_repost = 1 时生效
    "output_directory": "weibo_data", // 数据输出目录
    "user_id_as_folder_name": 0, // 是否以用户ID作为子文件夹名称 0 = 否，1 = 是
    "remove_html_tag": 1, // 是否移除微博内容中的HTML标签 0 = 否，1 = 是
    "cookie": "YOUR_WEIBO_COOKIE_HERE",
    // 微博Cookie（重要！）
    // 登录微博后，在浏览器开发者工具中获取
    // 格式：SCF=...; SUB=...; SUBP=...; ALF=...; SSOLoginState=...
    "sqlite_db_path": "weibo_data/weibo_data.sqlite", // SQLite数据库文件路径，仅当 write_mode 包含 sqlite 时生效
    "mysql_config": {
        "host": "localhost",
        "port": 3306,
        "user": "root",
        "password": "123456",
        "charset": "utf8mb4"
    },
    // MySQL数据库配置（仅当 write_mode 包含 mysql 时需要）
    "store_binary_in_sqlite": 0, // 是否在SQLite中存储二进制数据（图片、视频） 0 = 只存路径，1 = 存储二进制数据（文件会很大）
    "mongodb_URI": "mongodb://[username:password@]host[:port][/[defaultauthdb][?options]]",
    "post_config": {
        "api_url": "https://api.example.com",
        "api_token": ""
    },
    // MongoDB连接字符串（仅当 write_mode 包含 mongo 时需要）
    "anti_ban_config": {
        "enabled": true, // 是否启用反封禁机制
        "max_weibo_per_session": 500, // 单次运行最大微博数量，达到此数量后自动暂停，防止一次性爬取过多
        "batch_size": 50, // 批次大小，每爬取这么多条微博后，执行批次延迟
        "batch_delay": 30, // 批次延迟时间（秒），每批次后的强制等待时间
        "request_delay_min": 8, // 请求延迟最小值（秒）
        "request_delay_max": 15, // 请求延迟最大值（秒）
        "max_session_time": 600, // 最大单次运行时间（秒），达到此时间后强制休息
        "max_api_errors": 5, // 最大API错误次数，达到此次数后强制休息
        "rest_time_min": 600, // 休息时间最小值（秒），触发休息条件后暂停的时间
        "random_rest_probability": 0.01, // 每次休息后，随机触发额外休息的概率
        "user_agents": [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
            "Mozilla/5.0 (Linux; Android 14; SM-S9180) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.113 Mobile Safari/537.36"
        ],
        "accept_languages": [
            "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
            "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "zh-TW,zh;q=0.9,en;q=0.8"
        ],
        "referer_list": [
            "https://m.weibo.cn/",
            "https://weibo.com/",
            "https://www.weibo.com/"
        ]
    },
    "write_time_in_exif": 1, // 是否将微博发布时间写入EXIF
    "change_file_time": 1 // 是否修改文件系统时间
}