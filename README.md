# Weibo Crawler

> **⚠️ 重要：本项目在原版基础上进行了重构，可能存在破坏性更新与兼容性问题，建议谨慎更新。**
>
> 如果从原版或旧版本升级，请务必按以下顺序操作：
>
> 1. **先备份**：代码目录、`config/config.json`、数据目录（`output_directory`）、
>    `archive_state_*.json`、评论回填进度文件；
> 2. **先在测试环境部署并完整跑一轮**（抓取 → 存档 → 生成报告 → 评论回填 → 媒体下载），
>    检查输出目录结构、报告内容、日志无异常；
> 3. **确认测试环境无问题后，再部署到生产环境**；
> 4. 部署后运行 `python scripts/weibo_selfcheck.py` 与 `python weibo.py --check-config` 做自检。
>
> 未验证功能与已知限制见 [docs/未验证功能.md](./docs/未验证功能.md)；部署与升级检查见 [docs/部署与验证.md](./docs/部署与验证.md)。

- [Weibo Crawler](#weibo-crawler)
  - [功能](#功能)
  - [输出](#输出)
  - [实例](#实例)
  - [运行环境](#运行环境)
  - [使用说明](#使用说明)
    - [1.下载脚本](#1下载脚本)
    - [2.安装依赖](#2安装依赖)
    - [3.程序设置](#3程序设置)
    - [4.设置数据库（可选）](#4设置数据库可选)
    - [5.运行脚本](#5运行脚本)
    - [6.按需求修改脚本（可选）](#6按需求修改脚本可选)
    - [7.定期自动爬取微博（可选）](#7定期自动爬取微博可选)
    - [8.使用docker](#8使用docker)
  - [如何获取user\_id](#如何获取user_id)
  - [添加cookie与不添加cookie的区别（可选）](#添加cookie与不添加cookie的区别可选)
  - [如何获取cookie（可选）](#如何获取cookie可选)
  - [如何检测cookie是否有效（可选）](#如何检测cookie是否有效可选)
  - [API服务](#api服务)

## 功能

连续爬取**一个**或**多个**新浪微博用户（如[示例用户](https://weibo.cn/u/1000000005)、[示例用户3](https://weibo.cn/u/1000000006)）的数据，并将结果信息写入文件。写入信息几乎包括了用户微博的所有数据，主要有**用户信息**和**微博信息**两大类，前者包含用户昵称、关注数、粉丝数、微博数等等；后者包含微博正文、发布时间、发布工具、评论数等等，因为内容太多，这里不再赘述，详细内容见[输出](#输出)部分。具体的写入文件类型如下：

* 写入**csv文件**（默认）
* 写入**json文件**（可选）
* 写入**MySQL数据库**（可选，**未在真实服务验证**）
* 写入**MongoDB数据库**（可选，**未在真实服务验证**）
* 通过 **POST** 推送结果（可选，**未在真实服务验证**）
* 写入**SQLite数据库**（可选，未在现场验证）
* 写入**Markdown 存档**（可选，按天/月组织；支持评论区、编辑记录、媒体本地化）
* 下载用户**原创**微博中的原始**图片**（可选）
* 下载用户**转发**微博中的原始**图片**（可选）
* 下载用户**原创**微博中的**视频**（可选）
* 下载用户**转发**微博中的**视频**（可选）
* 下载用户**原创**微博**Live Photo**中的**视频**（可选）
* 下载用户**转发**微博**Live Photo**中的**视频**（可选）
* 下载用户**原创和转发**微博下的评论（含一级评论与二级回复；可选），开启 markdown 时可写入对应微博块
* 下载用户**原创和转发**微博下的转发（可选）
* 爬取结束后自动执行**本轮存档核对**（可选，需开启markdown存档），提示本轮未抓到的已存档微博与已抓到但未保存到本地的微博
* 爬取结束后自动生成**本轮抓取情况报告**（可选，需开启markdown存档）：每个账号一份 txt，记录本轮抓到多少条、其中已抓取过多少条、新抓到多少条，以及新抓到微博的 ID 和日期
* 爬取结束后自动执行**媒体完整性检查**（可选，需开启markdown存档）：检查 markdown 引用的本地图片/视频/Live Photo 是否存在、为空或明显损坏
* 使用独立脚本执行**全量存档核对**（可选，只读），枚举平台完整时间线做双向比对，列出两类名单
* 使用独立脚本执行**存量评论回填**（可选），对已存档 markdown 微博逐条抓评论并写回对应微博块，支持分批、断点续跑与 dry-run
* 配置键自动迁移：旧键名（如 `missing_weibo_report` → `weibo_archive_check`）启动时自动映射；可用 `python weibo.py --update-config` 写回 `config/config.json`（自动备份，尽量保留注释与排版），先看差异可运行 `python weibo.py --check-config`

> **未验证功能提示**：MySQL / MongoDB / POST 写入后端、SQLite、Docker、LLM 内容分析与三个运维脚本**未在真实环境验证**（依赖缺失或连接失败会告警跳过），可能存在 bug。完整清单与验证方式见 [docs/未验证功能.md](./docs/未验证功能.md)。

如果你只对用户信息感兴趣，而不需要爬用户的微博，也可以通过设置实现只爬取微博用户信息的功能。程序也可以实现**爬取结果自动更新**，即：现在爬取了目标用户的微博，几天之后，目标用户可能又发新微博了。通过设置，可以实现每隔几天**增量爬取**用户这几天发的新微博。具体方法见[定期自动爬取微博](#7定期自动爬取微博可选)。

## 输出

**用户信息**

* 用户id：微博用户id，如"1000000005"
* 用户昵称：微博用户昵称，如"示例用户"
* 性别：微博用户性别
* 生日：用户出生日期
* 所在地：用户所在地
* 教育经历：用户上学时学校的名字
* 公司：用户所属公司名字
* 阳光信用：用户的阳光信用
* 微博注册时间：用户微博注册日期
* 微博数：用户的全部微博数（转发微博+原创微博）
* 粉丝数：用户的粉丝数
* 关注数：用户关注的微博数量
* 简介：用户简介
* 主页地址：微博移动版主页url，如<https://m.weibo.cn/u/1000000005?uid=1000000005&luicode=10000011&lfid=1005051000000005>
* 头像url：用户头像url
* 高清头像url：用户高清头像url
* 微博等级：用户微博等级
* 会员等级：微博会员用户等级，普通用户该等级为0
* 是否认证：用户是否认证，为布尔类型
* 认证类型：用户认证类型，如个人认证、企业认证、政府认证等
* 认证信息：为认证用户特有，用户信息栏显示的认证信息

***
**微博信息**

* 微博id：微博的id，为一串数字形式
* 微博bid：微博的bid，与[cookie版](https://github.com/dataabc/weiboSpider)中的微博id是同一个值
* 微博内容：微博正文
* 头条文章url：微博中头条文章的url，如果微博中存在头条文章，就获取该头条文章的url，否则该值为''
* 原始图片url：原创微博图片和转发微博转发理由中图片的url，若某条微博存在多张图片，则每个url以英文逗号分隔，若没有图片则值为''
* 视频url: 微博中的视频url和Live Photo中的视频url，若某条微博存在多个视频，则每个url以英文分号分隔，若没有视频则值为''
* 微博发布位置：位置微博中的发布位置
* 微博发布时间：微博发布时的时间，精确到天
* 点赞数：微博被赞的数量
* 转发数：微博被转发的数量
* 评论数：微博被评论的数量
* 微博发布工具：微博的发布工具，如iPhone客户端、HUAWEI Mate 20 Pro等，若没有则值为''
* 话题：微博话题，即两个#中的内容，若存在多个话题，每个url以英文逗号分隔，若没有则值为''
* @用户：微博@的用户，若存在多个@用户，每个url以英文逗号分隔，若没有则值为''
* 原始微博：为转发微博所特有，是转发微博中那条被转发的微博，存储为字典形式，包含了上述微博信息中的所有内容，如微博id、微博内容等等
* 结果文件：保存在 output_directory 指定的文件夹（默认 weibo_data）下以用户昵称为名的文件夹里，名字为"user_id.csv"形式
* 微博图片：微博中的图片，保存在用户目录下的「原创微博图片」「转发微博图片」文件夹（目录名由 `download_folder_name` 配置；md 内链接路径由 `markdown_file_dir` 配置）
* 微博视频：微博中的视频，保存在以用户昵称为名的文件夹下的「原创微博视频」「转发微博视频」文件夹里（目录名由 `download_folder_name` 配置）
* **报告目录结构**：所有账号级报告按用户分目录保存——`output_directory/report/<用户目录名>/`（用户目录名与数据目录一致：`user_id_as_folder_name=0` 用昵称，=1 用用户 ID；非法文件名字符替换为下划线）。`not_downloaded_record_dir="report"` 的失败记录也落在该子目录。跨用户的回填总报告（`weibo_comment_backfill_<时间>.txt`）保存在 `output_directory/report/` 根目录。
* **编辑变更记录**：状态索引 `archive_state_<用户ID>.json` + `编辑历史/YYYY-MM/` 快照记录编辑。主爬把本轮抓到的微博与上次快照比较（正文/图片/视频/Live Photo/头条文章/话题 + 编辑次数），有变化才保存快照并在 md 的微博块内写一行 `<!-- edited_at: … -->`（隐藏注释；点赞/评论/转发等计数变化不算编辑）。
* 存档核对报告：开启 `weibo_archive_check` 后，每次爬取结束会在 `output_directory/report/` 生成 `weibo_archive_check_<用户ID>_<昵称>_<时间>.txt`，每条记录包含微博链接、发布日期、本地文件路径；**即使无差异也会生成**，报告里标注「核对结果: 无差异」；
* 抓取情况报告：开启 `weibo_crawl_summary` 后，每次爬取顺利完成会在 `output_directory/report/` 为每个账号生成 `weibo_crawl_summary_<用户ID>_<昵称>_<时间>.txt`，记录本轮抓到微博数、其中已抓取过与新抓到的数量，以及新抓到微博的 ID、发布日期与链接；
* 媒体完整性报告：开启 `weibo_media_check` 后，每次爬取结束会在 `output_directory/report/` 为每个账号生成 `weibo_media_check_<用户ID>_<昵称>_<时间>.txt`，列出 markdown 引用的本地媒体中「缺失 / 空文件 / 损坏或异常」的清单；
* 全量存档核对报告：运行 `scripts/weibo_archive_check_full.py` 后，在 `output_directory/report/` 生成 `weibo_archive_check_full_<用户ID>_<昵称>_<时间>.txt`，列出「本轮未抓到的已存档微博」与「未保存的微博」两类名单。**检查范围默认从该用户「本地存档最早日期」开始**（只核对已存档；配置起点到存档起点之间的历史不会被当成「未保存」），需要检查更早历史时用 `--since` 显式指定。
* 评论块：开启 `download_comment` 且 `write_mode` 含 `markdown` 时，本轮新写入 Markdown 的微博会**在该微博块内、`---` 分隔线之前**写入评论块：`#### 评论` 标题 + 评论标记（`<!-- comments: 微博ID total=… saved=… -->`）；正文中一级评论用引用块（`> **用户名** · 时间` + `> 正文`）、二级回复用嵌套引用（`> >`），一级与二级均按时间升序；一级之间、正文与二级之间用单独一行 `>` 分隔。评论标记与 `<!-- /comments -->` 在 Obsidian/普通 Markdown 中不显示。
* 评论下载情况报告：开启 `weibo_comment_summary` 后，每次爬取结束在 `output_directory/report/` 为每个账号生成 `weibo_comment_summary_<用户ID>_<昵称>_<时间>.txt`，记录本轮新微博的评论抓取条数、请求数与失败数。
* 评论回填报告与进度：运行 `scripts/weibo_comment_backfill.py` 后，在 `weibo_comment_backfill_dir`（默认 `output_directory/report/`）生成跨用户的总报告 `weibo_comment_backfill_<时间>.txt`（根目录），以及每用户进度 `<weibo_comment_backfill_dir>/<用户目录名>/comment_backfill_progress.json`（兼容读取平铺的 `comment_backfill_progress_<用户>.json`）。

## 实例

以爬取示例用户的微博为例，我们需要修改**config/config.json**文件（首次使用先 `cp config/config.json.example config/config.json`；根目录 `config.json` 仍兼容旧部署），文件内容如下：

```
{
    "user_id_list": ["1000000005"],
    "only_crawl_original": 1,
    "since_date": "1900-01-01",
    "query_list": [],
    "write_mode": ["csv", "json"],
    "original_pic_download": 1,
    "retweet_pic_download": 0,
    "original_video_download": 1,
    "retweet_video_download": 0,
    "cookie": "your cookie"
}
```

对于上述参数的含义以及取值范围，这里仅作简单介绍，详细信息见[程序设置](#3程序设置)。
>**user_id_list**代表我们要爬取的微博用户的user_id，可以是一个或多个，也可以是文件路径，微博用户示例用户的user_id为1000000005，具体如何获取user_id见[如何获取user_id](#如何获取user_id)；
**only_crawl_original**的值为1代表爬取全部原创微博，值为0代表爬取全部微博（原创+转发）；
**since_date**代表我们要爬取since_date日期之后发布的微博，因为我要爬示例用户的全部原创微博，所以since_date设置了一个非常早的值；
query_list代表要爬取的微博关键词，为空（[]）则爬取全部；
**write_mode**代表结果文件的保存类型，我想要把结果写入csv文件和json文件，所以它的值为["csv", "json"]；要写 Markdown 存档就在列表里加入 "markdown"（组织方式见下文）；
**original_pic_download**值为1代表下载原创微博中的图片，值为0代表不下载；
**retweet_pic_download**值为1代表下载转发微博中的图片，值为0代表不下载；
**original_video_download**值为1代表下载原创微博中的视频，值为0代表不下载；
**retweet_video_download**值为1代表下载转发微博中的视频，值为0代表不下载；
**cookie**是可选参数，可填可不填，具体区别见[添加cookie与不添加cookie的区别](#添加cookie与不添加cookie的区别可选)。

配置完成后运行程序：

```bash
python weibo.py
```

程序会自动生成 output_directory 指定的文件夹（默认 weibo_data），我们以后爬取的所有微博都被存储在该文件夹里。然后程序在该文件夹下生成一个名为"示例用户"的文件夹，示例用户的所有微博爬取结果都在这里。"示例用户"文件夹里包含一个 csv 文件，以及图片、视频等媒体文件夹（图片目录默认「原创微博图片」「转发微博图片」，视频目录默认「原创微博视频」「转发微博视频」，名称由 `download_folder_name` 配置），分别用来存储下载到的图片与视频。如果你设置了保存数据库功能，这些信息也会保存在数据库里，数据库设置见[设置数据库](#4设置数据库可选)部分。

**csv文件结果如下所示：**
![](https://github.com/dataabc/media/blob/master/weibo-crawler/images/weibo_csv.png)*1000000005.csv*

本csv文件是爬取“全部微博”(原创微博+转发微博)的结果文件。因为示例用户很多微博本身都没有图片、发布工具、位置、话题和@用户等信息，所以当这些内容没有时对应位置为空。"是否原创"列用来标记是否为原创微博，
当为转发微博时，文件中还包含转发微博的信息。为了简便起见，姑且将转发微博中被转发的原始微博称为**源微博**，它的用户id、昵称、微博id等都在名称前加上源字，以便与目标用户自己发的微博区分。对于转发微博，程序除了获取用户原创部分的信息，还会获取**源用户id**、**源用户昵称**、**源微博id**、**源微博正文**、**源微博原始图片url**、**源微博位置**、**源微博日期**、**源微博工具**、**源微博点赞数**、**源微博评论数**、**源微博转发数**、**源微博话题**、**源微博@用户**等信息。原创微博因为没有这些转发信息，所以对应位置为空。若爬取的是"全部**原创**微博"，则csv文件中不会包含"是否原创"及其之后的转发属性列；

为了说明json结果文件格式，这里以示例用户2019年12月27日到2019年12月28日发的2条微博为例。

**json结果文件格式如下：**

```
{
    "user": {
        "id": "1000000005",
        "screen_name": "示例用户",
        "gender": "f",
        "birthday": "双子座",
        "location": "上海",
        "education": "上海戏剧学院",
        "company": "嘉行传媒",
        "registration_time": "2010-07-02",
        "sunshine": "信用极好",
        "statuses_count": 1121,
        "followers_count": 66395881,
        "follow_count": 250,
        "description": "一只喜欢默默表演的小透明。工作联系jaywalk@jaywalk.com.cn 🍒",
        "profile_url": "https://m.weibo.cn/u/1000000005?uid=1000000005&luicode=10000011&lfid=1005051000000005",
        "profile_image_url": "https://tvax2.sinaimg.cn/crop.0.0.1080.1080.180/63885668ly8gb5sqc19mqj20u00u0mz5.jpg?KID=imgbed,tva&Expires=1584108150&ssig=Zay1N7KhK1",
        "avatar_hd": "https://wx2.sinaimg.cn/orj480/63885668ly8gb5sqc19mqj20u00u0mz5.jpg",
        "urank": 44,
        "mbrank": 7,
        "verified": true,
        "verified_type": 0,
        "verified_reason": "嘉行传媒签约演员　"
    },
    "weibo": [
        {
            "user_id": 1000000005,
            "screen_name": "示例用户",
            "id": 4454572602912349,
            "bid": "ImTGkcdDn",
            "text": "今天的#星光大赏#  ",
            "pics": "https://wx3.sinaimg.cn/large/63885668ly1gacppdn1nmj21yi2qp7wk.jpg,https://wx4.sinaimg.cn/large/63885668ly1gacpphkj5gj22ik3t0b2d.jpg,https://wx4.sinaimg.cn/large/63885668ly1gacppb4atej22yo4g04qr.jpg,https://wx2.sinaimg.cn/large/63885668ly1gacpn0eeyij22yo4g04qr.jpg",
            "video_url": "",
            "location": "",
            "created_at": "2019-12-28",
            "source": "",
            "attitudes_count": 551894,
            "comments_count": 182010,
            "reposts_count": 1000000,
            "topics": "星光大赏",
            "at_users": ""
        },
        {
            "user_id": 1000000005,
            "screen_name": "示例用户",
            "id": 4454081098040623,
            "bid": "ImGTzxJJt",
            "text": "我最爱用的娇韵诗双萃精华穿上限量“金”装啦，希望阿丝儿们跟我一起在新的一年更美更年轻，喜笑颜开没有细纹困扰！限定新春礼盒还有祝福悄悄话，大家了解一下～",
            "pics": "",
            "video_url": "",
            "location": "",
            "created_at": "2019-12-27",
            "source": "",
            "attitudes_count": 190840,
            "comments_count": 43523,
            "reposts_count": 1000000,
            "topics": "",
            "at_users": "",
            "retweet": {
                "user_id": 1684832145,
                "screen_name": "法国娇韵诗",
                "id": 4454028484570123,
                "bid": "ImFwIjaTF",
                "text": "#点萃成金 年轻焕新# 将源自天然的植物力量，转化为滴滴珍贵如金的双萃精华。这份点萃成金的独到匠心，只为守护娇粉们的美丽而来。点击视频，与@示例用户 一同邂逅新年限量版黄金双萃，以闪耀开运金，送上新春宠肌臻礼。 跟着迪迪选年货，还有双重新春惊喜，爱丽丝们看这里！ 第一重参与微淘活动邀请好友关注娇韵诗天猫旗舰店，就有机会赢取限量款热巴新年礼盒，打开就能聆听仙女迪亲口送出的新春祝福哦！点击网页链接下单晒热巴同款黄金双萃，并且@法国娇韵诗，更有机会获得热巴亲笔签名的礼盒哦！ 第二重转评说出新年希望娇韵诗为你解决的肌肤愿望，截止至1/10，小娇将从铁粉中抽取1位娇粉送出限量版热巴定制礼盒，抽取3位娇粉送出热巴明信片1张～ #示例用户代言娇韵诗#养成同款御龄美肌，就从现在开始。法国娇韵诗的微博视频",
                "pics": "",
                "video_url": "http://f.video.weibocdn.com/003vQjnRlx07zFkxIMjS010412003bNx0E010.mp4?label=mp4_hd&template=852x480.25.0&trans_finger=62b30a3f061b162e421008955c73f536&Expires=1578322522&ssig=P3ozrNA3mv&KID=unistore,video",
                "location": "",
                "created_at": "2019-12-27",
                "source": "微博 weibo.com",
                "attitudes_count": 18389,
                "comments_count": 3201,
                "reposts_count": 1000000,
                "topics": "点萃成金 年轻焕新,示例用户代言娇韵诗",
                "at_users": "示例用户,法国娇韵诗"
            }
        }
    ]
}
```

*1000000005.json*

**下载的图片如下所示：**

![](https://github.com/dataabc/media/blob/master/weibo-crawler/images/img.png)*图片文件夹（名称由 `download_folder_name` 配置）*

本次下载了788张图片，大小一共1.21GB，包括她原创微博中的所有图片。图片名为yyyymmdd+微博id的形式，若某条微博存在多张图片，则图片名中还会包括它在微博图片中的序号。若某张图片下载失败，程序会把“微博id:本地文件路径:下载URL”以UTF-8写入该图片所在目录的not_downloaded.txt里；重新下载成功（或文件已存在且非空）后会自动清除对应记录，0字节的空文件会被视为未下载并重下；若图片全部下载成功则不会留下记录；

**下载的视频如下所示：**
![](https://github.com/dataabc/media/blob/master/weibo-crawler/images/video.png)*视频文件夹：「原创微博视频」「转发微博视频」*

本次下载了66个视频，是她原创微博中的视频和原创微博Live Photo中的视频，视频名为yyyymmdd+微博id的形式。有三个视频因为网络原因下载失败，程序把它们以“微博id:本地文件路径:下载URL”的形式写到各自视频实际所在目录的not_downloaded.txt里（与图片规则相同：UTF-8、成功后自动清除、空文件重下）。`not_downloaded_record` 默认为 0（不产生失败记录），设为 1 才会记录；记录位置默认跟随媒体目录，也可以用 `not_downloaded_record_dir` 改为 report 目录或自定义目录。

写入后端支持 csv、json、sqlite、markdown，以及 MySQL / MongoDB / POST（后三者**未在真实服务验证**；依赖缺失或连接失败只告警跳过，不阻断其他后端）。SQLite 无需安装服务端，见[设置数据库](#4设置数据库可选)。

## 运行环境

* 语言：Python 3.10+
* 系统：Windows / Linux / macOS

## 使用说明

### 1.下载脚本

```bash
git clone <本仓库地址>          # 或直接下载 ZIP 解压
```

将本项目下载到本地任意目录，进入该目录后按下方步骤安装依赖与配置；

### 2.安装依赖

```bash
pip install -r requirements.txt        # 核心依赖（抓取/存档/报告）
# 可选：MySQL / MongoDB / POST 后端的依赖（未验证；不装也能正常运行）
pip install -r requirements-legacy.txt
```

### 3.程序设置

打开**config/config.json**文件（首次使用先复制模板：`cp config/config.json.example config/config.json`；根目录 `config.json` 仍兼容旧部署），你会看到如下内容：

```
{
    "user_id_list": ["1000000005"],
    "only_crawl_original": 1,
    "remove_html_tag": 1,
    "since_date": "2018-01-01",
    "start_page": 1,
    "page_weibo_count": 10,
    "write_mode": ["csv"],
    "original_pic_download": 1,
    "retweet_pic_download": 0,
    "original_video_download": 1,
    "retweet_video_download": 0,
    "original_live_photo_download": 1,
    "retweet_live_photo_download": 0,
    "download_comment":1,
    "comment_max_download_count":1000,
    "download_repost": 1,
    "repost_max_download_count": 1000,
    "user_id_as_folder_name": 0,
    "cookie": "your cookie",
    "store_binary_in_sqlite": 0,
    "write_time_in_exif": 1,
    "change_file_time": 1
}
```

下面讲解每个参数的含义与设置方法。

**设置user_id_list**

user_id_list是我们要爬取的微博的id，可以是一个，也可以是多个，例如：

```
"user_id_list": ["1000000004", "1000000005", "1000000006"],
```

上述代码代表我们要连续爬取user_id分别为“1000000004”、 “1000000005”、 “1000000006”的三个用户的微博，具体如何获取user_id见[如何获取user_id](#如何获取user_id)。

user_id_list的值也可以是文件路径，我们可以把要爬的所有微博用户的user_id都写到txt文件里，然后把文件的位置路径赋值给user_id_list。

在txt文件中，每个user_id占一行，也可以在user_id后面加注释（可选），如用户昵称等信息，user_id和注释之间必需要有空格，文件名任意，类型为txt，位置位于本程序的同目录下，文件内容示例如下：

```
1000000004 示例用户2
1000000005 示例用户
1000000006 示例用户3
```

假如文件叫 `config/user_id_list.txt`（配置相关文件统一放在 `config/` 目录；模板见 `config/user_id_list.txt.example`），则 user_id_list 设置代码为：

```
"user_id_list": "config/user_id_list.txt",
```

**设置only_crawl_original**

only_crawl_original控制爬取范围，值为1代表爬取全部原创微博，值为0代表爬取全部微博（原创+转发）。例如，如果要爬全部原创微博，请使用如下代码：

```
"only_crawl_original": 1,
```

**设置since_date**

since_date值可以是日期，也可以是整数。如果是日期，代表爬取该日期之后的微博，格式应为“yyyy-mm-dd”，如：

```
"since_date": "2018-01-01",
```

代表爬取从2018年1月1日到现在的微博。

如果是整数，代表爬取最近n天的微博，如:

```
"since_date": 10,
```

代表爬取最近10天的微博，这个说法不是特别准确，准确说是爬取发布时间从**10天前到本程序开始执行时**之间的微博。

**since_date是所有user的爬取起始时间，非常不灵活。如果你要爬多个用户，并且想单独为每个用户设置一个since_date，可以使用[定期自动爬取微博](#7定期自动爬取微博可选)方法二中的方法，该方法可以为多个用户设置不同的since_date，非常灵活**。

**设置end_date**

end_date为可选配置，表示爬取截止日期，格式与 since_date 相同（`YYYY-MM-DD` 或 `YYYY-MM-DDTHH:MM:SS`），默认为空字符串（不限制，抓到当前为止）。典型用途是补抓某个历史时间窗：

```
"end_date": "2023-12-31",
```

**设置markdown_split_by / markdown_sort_order / markdown_image_order**

这三个开关控制 Markdown 存档的组织方式，默认值见 `config.json.example`：
- `markdown_split_by`：`day_by_month`（默认，按天分文件并按月归档到 `YYYY-MM/` 子目录）、`day`（按天）、`month`（按月）、`year`（按年）、`all`（全部单文件）；
- `markdown_sort_order`：`new_to_old`（默认）或 `old_to_new`；
- `markdown_image_order`：`live_photo_first`（默认，Live Photo 在前）或 `image_first_interleaved`（图片优先，同名 Live Photo 紧跟图片）。

**设置markdown_preview**

markdown_preview 控制 md 内各类媒体的链接形式（0 = 普通链接，1 = 直接预览），默认：`link_preview=0`、`inline_image_preview=1`、`original_image_preview=1`、`retweet_image_preview=1`、`original_video_preview=0`、`retweet_video_preview=0`、`original_live_photo_preview=0`、`retweet_live_photo_preview=0`。

**设置markdown_show_location_link**

markdown_show_location_link 控制是否把「位置」类网页链接写进 md（这类链接指向微博 POI 页面，如 `http://weibo.com/p/100101B2094655D06CA0FB409F`）。默认 0 = 不写入（微博正文里的「示例城市·示例地点」这类位置文字保留，并显示在媒体之后、微博块最后，与正文空一行）；设为 1 时按 `markdown_preview.link_preview` 以普通链接或预览图片形式写入。含信息的普通网页链接（视频/音乐/链接卡片）会把正文最后一行（链接标题）与链接紧挨显示，并与前面的正文空一行隔开。

**设置markdown_edit_history**

编辑记录是自动维护的：检测到微博内容指纹变化时，程序先用「正文 + 图片/视频文件名 + 链接」的内容签名做二次确认（只是接口字段或媒体链接签名变化、正文语义没变，不算编辑），确认确实变化后才把上一版快照保存到 `<用户目录>/编辑历史/YYYY-MM/`，并在对应微博块正文写一行隐藏注释 `<!-- edited_at: … -->`（幂等，先备份原 md）。记录口径：正文、图片、视频、Live Photo、头条文章、话题与编辑次数；点赞/评论/转发等计数变化不算编辑；新微博首次被抓只记快照、不算编辑。长文详情页取全文失败时跳过编辑写回（保留旧存档全文，不用截断正文覆盖），失败清单会写进变化报告「六、长文获取失败」。

**设置markdown_file_dir / download_folder_name**

媒体文件名的时间前缀由 `timestamp_prefix_format` 配置，默认 `"%Y-%m-%d_%H-%M-%S"`（例如 `2024-01-02_03-04-05`）；设为 `"%Y%m%d_%H%M%S"` 则生成 `20240102_030405`。支持这两种格式，图片、视频、Live Photo 及对应 Markdown 链接使用同一规则。此配置只影响后续生成的文件名，不会重命名已有文件。
`timestamp_prefix_include_id` 控制是否在媒体时间前缀后追加微博 ID，默认 `0`（不追加）。

`markdown_file_dir` 是 md 内媒体链接使用的相对目录名（`original_image_dir`、`retweet_image_dir`、`original_video_dir`、`retweet_video_dir`、`original_live_photo_dir`、`retweet_live_photo_dir`、`comment_image_dir`）；`download_folder_name` 是下载到本地的文件夹名（`original_image_folder`、`retweet_image_folder`、……）。**write_mode 含 markdown 时，媒体落盘目录统一使用 `markdown_file_dir` 的名字**，保证 md 链接与本地文件一致；非 markdown 模式使用 `download_folder_name`，未显式配置 `comment_image_dir` 时评论图片目录回退到 `comment_image_folder`。

**设置start_page**

start_page表示爬取的起始页，默认为1表示从第一页开始爬取

**设置page_weibo_count**

page_weibo_count用于设置爬取一页里的微博数量，一页的微博数量越大，爬取微博效率越高，默认值为10，最小值为1，最大值为100，经测试设置大于100的值后最多也只返回最多100条数据。

**设置query_list(可选)**

query_list是一个关键词字符串列表或以`,`分隔关键词的字符串，用于指定关键词搜索爬取，若为空`[]`或`""`则爬取全部微博。例如要爬取用户包含“梦想”和“希望”的微博，则设定如下：

```
"query_list": ["梦想","希望"],
"query_list": "梦想,希望",
```

请注意，关键词搜索必须设定`cookie`信息。
**query_list是所有user的爬取关键词，非常不灵活。如果你要爬多个用户，并且想单独为每个用户设置一个query_list，可以使用[定期自动爬取微博](#7定期自动爬取微博可选)方法二中的方法，该方法可以为多个用户设置不同的query_list，非常灵活**。

**设置remove_html_tag**

remove_html_tag控制是否移除抓取到的weibo正文和评论中的html tag，值为1代表移除，值为0代表不移除，如

```
"remove_html_tag": 1,
```

代表移除html tag。例如`专属新意，色彩启程~<a href='/n/路易威登'>@路易威登</a> CAPUCINES 手袋正合我意，打开灵感包袋的搭配新方式！`会被处理成`专属新意，色彩启程~@路易威登 CAPUCINES 手袋正合我意，打开灵感包袋的搭配新方式！`。

**设置write_mode**

write_mode控制结果文件格式，**当前支持**的取值是 csv、json、sqlite、markdown，分别代表将结果写入 csv、json 文件、SQLite 数据库、Markdown 存档。write_mode可以同时包含这些取值中的一个或几个，如：

```
"write_mode": ["csv", "json", "markdown"],
```

代表将结果信息写入csv文件、json文件与 Markdown 存档。`mysql` / `mongo` / `mongodb` / `post` 也支持（未在真实服务验证）；`sqlite` 需要 `append` 模式配合，见[设置数据库](#4设置数据库可选)。

**设置weibo_archive_check**

weibo_archive_check控制每次爬取结束后是否生成本轮存档核对报告，值为1代表生成，值为0代表不生成，默认值为0。该功能需要 `write_mode` 包含markdown；报告列出「本轮未抓到的已存档微博」与「已抓到但未保存到本地」两类名单，只做提示，不会自动补抓。该值为1时，只要本轮抓取顺利完成、`write_mode` 含 markdown、且未被 query 关键词/快照失败等情况跳过，就会生成报告（无差异时也生成，报告里标注「核对结果: 无差异」）；抓取提前结束（接口错误/防封禁暂停）、无 markdown 后端、query 关键词用户等情况下不会生成报告。配置键支持自动迁移：`missing_weibo_report`、`weibo_delete_save_check` 等旧键名启动时自动映射，可用 `python weibo.py --update-config` 写回配置文件。

```
"weibo_archive_check": 1,
```

**设置weibo_deleted_report**

weibo_deleted_report 控制抓取结束后是否在**用户存档目录**生成 `已删除微博.txt`，默认 1（生成）。口径：抓取前本地存档里有、本轮完整抓取没有返回、且落在本轮核对范围内的微博，逐条调用 `statuses/show` 核验，只有接口明确返回「该微博不存在/已删除」才写入文件；每行 `微博ID<TAB>发布日期`。受限可见（暂无查看权限）、状态接口异常（unknown）不会算作已删除。append 增量模式、query 关键词模式、抓取未顺利完成时跳过；固定文件名，每次覆盖为本轮结果。设为 0 可关闭。

**设置weibo_archive_check_dir**

weibo_archive_check_dir控制本轮存档核对报告的输出目录，默认值为 "report"。相对路径相对于 `output_directory`（默认即 `weibo_data/report`）；也可以填绝对路径。

```
"weibo_archive_check_dir": "report",
```

**设置weibo_crawl_summary**

weibo_crawl_summary控制每次爬取结束后是否为每个账号生成本轮抓取情况报告，值为1代表生成，值为0代表不生成，默认值为0。该功能需要 `write_mode` 包含markdown（报告用抓取前本地 markdown 存档判断哪些是「已抓取过」）；只有本轮抓取顺利完成时才生成。报告记录：本轮抓到多少条、其中已抓取过多少条、本轮新抓到多少条、新抓到微博的 ID/发布日期/链接、新微博按月分布，以及已确认不可见跳过、只抓原创过滤、媒体下载失败等计数。

```
"weibo_crawl_summary": 1,
```

**设置weibo_crawl_summary_dir**

weibo_crawl_summary_dir控制本轮抓取情况报告的输出目录，默认值为 "report"；相对路径相对于 `output_directory`，也可以填绝对路径。

```
"weibo_crawl_summary_dir": "report",
```

**设置weibo_media_check**

weibo_media_check控制每次爬取结束后是否生成媒体完整性报告，值为1代表生成，值为0代表不生成，默认值为0。该功能需要 `write_mode` 包含 markdown：扫描该用户 markdown 里引用的本地图片/视频/Live Photo，以及评论块中的评论图片，检查文件是否存在、是否为空、文件头/尾部是否明显损坏；只读，不下载、不修复。报告会单独标注评论图片来源，并在统计行给出评论图片问题数。

```
"weibo_media_check": 1,
```

**设置weibo_media_check_dir**

weibo_media_check_dir控制媒体完整性报告的输出目录，默认值为 "report"；相对路径相对于 `output_directory`，也可以填绝对路径。

```
"weibo_media_check_dir": "report",
```

**设置weibo_archive_check_full**

weibo_archive_check_full控制是否允许运行全量存档核对脚本 `scripts/weibo_archive_check_full.py`，值为1代表允许，值为0代表禁用（脚本会直接退出并提示），默认值为0。该开关只影响手动运行的全量核对脚本，不影响每次爬取结束后的本轮存档核对。

```
"weibo_archive_check_full": 1,
```

**设置weibo_archive_check_full_dir**

weibo_archive_check_full_dir控制全量存档核对报告的输出目录，默认值为 "report"；相对路径相对于 `output_directory`，也可以填绝对路径。

**全量核对的范围口径**：默认从该用户「本地存档最早日期」起枚举平台微博（只核对已存档，不把配置起点到存档起点之间的历史算成「未保存」）；如需检查更早历史，用 `--since <起始日期>` 显式指定。报告 header 会标注「检查起点来源」。注意每用户枚举页数默认上限 300 页（`--max-pages`），从很早的日期起枚举可能撞上限，报告会提示「枚举未顺利完成（不能作为删除依据）」；此时「未保存」名单不完整，属于正常限制。

```
"weibo_archive_check_full_dir": "report",
```

**设置invisible_weibo_dirs**

invisible_weibo_dirs是已确认不可见的微博所在的目录列表（如用户核实后被删除、隐藏、仅粉丝可见的微博），默认值为["已不可见"]。这些目录中的微博不参与存档核对，正常爬取和全量核对时也不会写回。用户目录下的ignore_ids.txt作用相同，适合无法移动markdown文件的情况。

```
"invisible_weibo_dirs": ["已不可见"],
```

**设置original_pic_download**

original_pic_download控制是否下载**原创**微博中的图片，值为1代表下载，值为0代表不下载，如

```
"original_pic_download": 1,
```

代表下载原创微博中的图片。

**设置retweet_pic_download**

retweet_pic_download控制是否下载**转发**微博中的图片，值为1代表下载，值为0代表不下载，如

```
"retweet_pic_download": 0,
```

代表不下载转发微博中的图片。特别注意，本设置只有在爬全部微博（原创+转发），即only_crawl_original值为0时生效，否则程序会跳过转发微博的图片下载。

**设置original_video_download**

original_video_download控制是否下载**原创**微博中的视频，值为1代表下载，值为0代表不下载，如

```
"original_video_download": 1,
```

代表下载原创微博中的视频。

**设置retweet_video_download**

retweet_video_download控制是否下载**转发**微博中的视频，值为1代表下载，值为0代表不下载，如

```
"retweet_video_download": 0,
```

代表不下载转发微博中的视频和。特别注意，本设置只有在爬全部微博（原创+转发），即only_crawl_original值为0时生效，否则程序会跳过转发微博的视频下载。

**设置original_live_photo_download**

original_live_photo_download控制是否下载**原创**微博**Live Photo**中的视频，值为1代表下载，值为0代表不下载，如

```
"original_live_photo_download": 1,
```

代表下载原创微博中Live Photo的视频。

**设置retweet_live_photo_download**

retweet_live_photo_download控制是否下载原创微博Live Photo中的视频，值为1代表下载，值为0代表不下载，如

```
"retweet_live_photo_download": 0,
```

代表不下载转发微博中Live Photo的视频。特别注意，本设置只有在爬全部微博（原创+转发），即only_crawl_original值为0时生效，否则程序会跳过转发微博的视频下载。

**设置not_downloaded_record**

not_downloaded_record控制媒体下载失败时是否记录失败信息，值为1代表记录，值为0代表不记录（也不清理旧记录），默认值为0。记录格式为“微博id:本地文件路径:下载URL”；重新下载成功、或文件已存在且通过轻量校验后，会自动清除对应记录。

```
"not_downloaded_record": 1,
```

**设置not_downloaded_record_dir**

not_downloaded_record_dir控制失败记录的位置，默认值为空字符串，表示跟随媒体文件所在目录；填 "report" 表示集中写到 `output_directory/report/<用户目录名>/not_downloaded.txt`；也可以填其他相对路径（相对 `output_directory`）或绝对路径。该配置只在 `not_downloaded_record=1` 时生效。

```
"not_downloaded_record_dir": "",
```

**媒体文件轻量校验**

- 下载前：本地已有且非空的文件会先做文件头/尾部校验，明显损坏则删除重下；0 字节文件删除后重下；校验通过的文件不会重复下载、不消耗流量。
- 下载后：比对 `Content-Length` 与实际字节数；JPEG/PNG/GIF 校验头尾，MP4/MOV/WebM 校验容器签名；HTML/XML 错误页、明显不完整的内容不会落盘；失败不会留下半成品文件。
- 失败时按 `not_downloaded_record` 决定是否写 `not_downloaded.txt`，并在日志与（开启时）本轮抓取情况报告的「媒体下载失败」计数里体现。
- 媒体完整性报告：见上面的 `weibo_media_check`，用于发现 markdown 引用的媒体缺失/为空/损坏。

**设置user_id_as_folder_name**

user_id_as_folder_name控制结果文件的目录名，可取值为0和1，默认为0：

```
"user_id_as_folder_name": 0,
```

值为0，表示将结果文件保存在以用户昵称为名的文件夹里，这样结果更清晰；值为1表示将结果文件保存在以用户id为名的文件夹里，这样能保证多次爬取的一致性，因为用户昵称可变，用户id不可变。

**设置download_comment**

download_comment控制是否下载每条微博下的评论（含一级评论与二级回复；微博评论实际只有两层，接口把二级回复平铺返回）。`write_mode` 含 `sqlite` 时写入 SQLite 与评论 CSV；含 `markdown` 时把评论写入对应微博块。可取值为0和1，默认为0：

```
"download_comment": 1,
```

值为1，表示下载微博评论；值为0，表示不下载微博评论。

**设置comment_max_download_count**

comment_max_download_count控制单条微博最多抓取多少条一级评论，仅当 `download_comment = 1` 时生效；取值为非负整数，0 表示不限制（翻到接口结束），默认为0。

```
"comment_max_download_count": 0,
```

注意：微博评论接口有上限，实测一条评论数 10344 的微博，热门流最多约 16 页/255 条，之后的评论接口不再返回。

**设置comment_reply_max_download_count**

comment_reply_max_download_count控制每条一级评论最多抓取多少条二级回复，0 表示不限制，默认为0。

```
"comment_reply_max_download_count": 0,
```

实测单条一级评论的回复约 80～85 条后接口提前结束（即使页面显示数百条回复）。

**设置comment_max_requests**

comment_max_requests 是单条微博评论抓取的请求预算上限（默认 300），用于防止异常数据导致无限翻页。正常抓取远低于该值；只有在接口返回异常（如 has_more 永远为真）时才会触发保护并停止该微博的评论抓取。

**设置comment_pic_download**

comment_pic_download控制是否下载评论图片，默认为0（不下载）。开启且 `write_mode` 含 markdown 时，图片下载到 md 同级的「评论图片」目录，文件名与正文图片/视频一致（`<评论时间>.jpg`），并以 `![img](评论图片/文件名)` 引用（与正文图片展示格式一致）。没有评论图片时不会创建「评论图片」空目录。

**设置weibo_emoji_dir**

weibo_emoji_dir 是评论表情图片的本地存储目录，默认 `weibo_data/emoji`（相对项目根；绝对路径也支持）。表情 CDN 路径带版本段（如 `.../t4/...`）时按版本分目录，文件名只用表情名（如 `[微笑]` → `t4/微笑.png`）；没有版本段时直接放根目录。`emoji_index.json` 记录 URL → 文件、文件 → 内容摘要：同一 URL 不重复下载，同名同内容复用，同名不同内容（平台升级换图）自动落成 `微笑-2.png`，不会串图。Markdown 评论块用相对内链引用（如 `![微笑](../../emoji/t4/微笑.png)`）；下载失败保留原外链。评论表情的显示形态由 `comment_emoji_replace` 控制（0 = 保留图片；1 = 只显示表情名/外链文本）。`comment_emoji_inline_size` 控制评论表情在 md 中的显示高度（像素，默认 0）：0 = 保持原图尺寸、使用 `![]()` 语法；>0 时改为行内 HTML `<img src="…" width="18" height="18" alt="…">`，显示大小接近正文文字（如设为 18）。

**设置comment_show_like_count / comment_show_reply_target / comment_emoji_replace**

三个展示开关，只在写 Markdown 评论块时生效：comment_show_like_count 控制是否显示点赞数（默认0，不显示）；comment_show_reply_target 控制二级回复是否显示「回复 某人」（默认1，显示）；comment_emoji_replace 控制评论里的表情图片怎么输出（默认1）：0 = 保留外链图片，输出 `![表情名](外链)`（如 `![微笑](https://…)`）；1 = 不显示图片，优先输出表情名（如 `[微笑]`），没有表情名时回退为可点击外链 `[表情](外链)`。

**设置comment_include_latest_stream**

comment_include_latest_stream控制是否用旧接口 `api/comments/show` 补充「最新流」一级评论，默认0（不补充）。开启后能多拿到一批最新评论（实测约 360 条，与热门流基本不重合），但字段较少（无图片/表情/楼层）、请求更多（最多约 50 页）。

**设置comment_request_delay_min / comment_request_delay_max**

评论请求间隔（秒），逐页随机取值，默认1～2秒。该间隔与微博抓取的 `anti_ban_config` 分开设置，避免把 8～15 秒的微博延迟套到评论请求上。

**设置weibo_comment_summary / weibo_comment_summary_dir**

weibo_comment_summary控制每次爬取结束后是否为每个账号生成**评论下载情况报告**，默认0（不生成）；需要 `download_comment = 1` 且 `write_mode` 含 markdown。weibo_comment_summary_dir为报告目录（默认 "report"，相对 output_directory，也可填绝对路径）。

**设置weibo_comment_backfill / weibo_comment_backfill_dir**

weibo_comment_backfill控制是否允许运行**存量评论回填脚本** `scripts/weibo_comment_backfill.py`，默认0（禁用）；weibo_comment_backfill_dir为回填报告与进度文件目录（默认 "report"，相对 output_directory）。

存量评论回填用法（在项目根目录执行）：

```
python scripts/weibo_comment_backfill.py --dry-run                 # 只看将处理多少条微博
python scripts/weibo_comment_backfill.py --max-weibo 20            # 先小批试跑
python scripts/weibo_comment_backfill.py                           # 全量回填（默认全量）
python scripts/weibo_comment_backfill.py --users 用户A --since <起始日期>
```

脚本分批执行（默认每批 50 条微博、批间休息 60 秒），进度写入 `weibo_comment_backfill_dir` 下的 `comment_backfill_progress_<用户>.json`；中断后直接重跑即可续跑（加 `--force` 可重抓已处理项，`--no-resume` 从头开始）。回填会原地修改 md 文件；默认不生成 `.bak` 备份，需要回滚保护时把 `comment_markdown_backup` 设为 1。

**并发与中断**：回填与主爬（`weibo.py`）共用 `<output_directory>/.weibo_crawler.lock` 互斥——主爬运行期间启动回填会被拒绝（退出码 2），反之主爬也会跳过本轮，避免两者同时改写同一批 markdown（评论块丢失 / 新微博丢失）。回填过程中按 Ctrl-C 会保存进度并生成「已中断」报告（退出码 2），下次直接重跑即可续跑；账号级失败（HTTP/网络/风控）不写断点，下次运行自动重试。

**设置comment_markdown_backup**

comment_markdown_backup控制写评论块前是否把原 md 备份为 `<文件名>.bak.<时间戳>`，默认0（不备份）。需要回滚保护时设为1；同一次运行内同一个文件只备份一次。回填脚本单次也可用 `--no-backup` 关闭。已生成的备份可用 `python scripts/weibo_comment_backfill.py --clean-backups` 清理（加 `--dry-run` 先预览），会删除两类备份命名：主爬/编辑更新的 `*.md.bak.YYYYMMDD[_N]` 与评论写入的 `*.md.bak.YYYYMMDD_HHMMSS[_N]`。

**设置comment_user_filter_file / comment_user_filter**

按微博账号配置评论者白名单：只保留指定评论者发的评论/回复（主流程与回填脚本都生效，过滤在抓取完成后进行，请求量不变）。

推荐放在 `config/` 目录下的外部 txt 文件里（`comment_user_filter_file` 指定，默认 `config/comment_user_filter.txt`，格式同 `user_id_list.txt` 风格）：

```
# 每行：<微博账号ID或昵称> <评论者1> [评论者2] ...
# 空格或制表符分隔；# 开头为注释；空行忽略
1234567890 1111111111 2222222222
1234567891 3333333333
```

规则：账号未出现在文件里 = 不过滤；评论作者在白名单内的一级评论/二级回复保留；一级评论作者不在白名单但其下有白名单回复时，该一级评论作为上下文保留；过滤后整条微博没有符合内容时不写评论块。文件不存在或解析为空时，回退使用 config 里的内联 `comment_user_filter`（`{"微博账号": ["评论者", ...]}`）。示例文件见 `config/comment_user_filter.txt.example`。

**设置download_repost**

download_repost控制是否下载每条微博下的转发，仅当write_mode中有sqlite时有效，可取值为0和1，默认为1：

```
"download_repost": 1,
```

值为1，表示下载微博转发；值为0，表示不下载微博转发。

**设置repost_max_download_count**

repost_max_download_count控制下载转发的最大数量，仅当write_mode中有sqlite时有效，默认为1000：

```
"repost_max_download_count": 1000,
```

值为1000，表示最多下载每条微博下的1000条转发。

**设置cookie（可选）**

cookie为可选参数，即可填可不填，具体区别见[添加cookie与不添加cookie的区别](#添加cookie与不添加cookie的区别可选)。cookie默认配置如下：

```
"cookie": "your cookie",
```

如果想要设置cookie，可以按照[如何获取cookie](#如何获取cookie可选)中的方法，获取cookie，并将上面的"your cookie"替换成真实的cookie即可。

**设置store_binary_in_sqlite（可选）**

store_binary_in_sqlite控制是否往数据库中存储图片或视频的二进制数据。0为关闭，1为开启。

**可选配置键：mysql_config / mongodb_URI / post_config**

三者对应 MySQL / MongoDB / POST 写入后端（实现见 `util/legacy/backends.py`），**未在真实服务上验证**；
`write_mode` 含 `mysql` / `mongo` / `post` 时才会生效，依赖缺失或连接失败只记 warning 并跳过，
不阻断其他写入后端。不用这些后端时留着这些键没有影响。


**设置start_page（可选）**

start_page为爬取微博的初始页数，默认参数为1，即从所爬取用户的当前第一页微博内容开始爬取。
若在大批量爬取微博时出现中途被限制中断的情况，可通过查看csv文件内目前已爬取到的微博数除以10，向下取整后的值即为中断页数，手动设置start_page参数为中断页数，重新运行即可从被中断的节点继续爬取剩余微博内容。

**设置write_time_in_exif**

write_time_in_exif控制是否启用将created_time写入图片exif信息中，包括jpg和jpeg文件，可取值为0和1，默认为1：

```
"write_time_in_exif": 1,
```

值为1，表示启用；值为0，表示不启用。

### 4.设置数据库（可选）

本部分是可选部分。当前支持 **SQLite**（无需服务端）、**MySQL**、**MongoDB** 与 **POST 推送**；
后三者**未在真实服务上验证**（依赖缺失或连接失败只记 warning 并跳过，不阻断其他写入后端）。

**SQLite 数据库写入**

SQLite 无需安装服务端，`write_mode` 含 `"sqlite"` 即可；数据库文件路径由 `sqlite_db_path` 指定（默认 `weibodata.db`，相对项目根或绝对路径均可）。注意：`append` 模式必须包含 sqlite，否则程序会拒绝启动。0 字节或缺表的库文件会在启动时自动补建缺失表（comments/reposts 等）。

**MySQL数据库写入（保留，未验证）**

要想将爬取信息写入MySQL，请根据自己的系统环境安装MySQL，然后命令行执行：

```bash
pip install pymysql
```

**MongoDB数据库写入（保留，未验证）**

要想将爬取信息写入MongoDB，请根据自己的系统环境安装MongoDB，然后命令行执行：

```
pip install pymongo
```

**POST 推送（保留，未验证）**

`write_mode` 含 `"post"` 时，程序把本轮微博以 `{"user": ..., "weibo": [...]}` 结构
POST 到 `post_config.api_url`（令牌放 `post_config.api_token`），重试 3 次、退避 2/4 秒。
接口地址与令牌需自行准备；未在真实接口上验证。

MySQL和MongDB数据库的写入内容一样。程序首先会创建一个名为"weibo"的数据库，然后再创建"user"表和"weibo"表，包含爬取的所有内容。爬取到的微博**用户信息**或插入或更新，都会存储到user表里；爬取到的**微博信息**或插入或更新，都会存储到weibo表里，两个表通过user_id关联。如果想了解两个表的具体字段，请点击"详情"。
<details>

<summary>详情</summary>

**user**表

**id**：微博用户id，如"1000000005"；

**screen_name**：微博用户昵称，如"示例用户"；

**gender**：微博用户性别，取值为f或m，分别代表女和男；

**birthday**：生日；

**location**：所在地；

**education**：教育经历；

**company**：公司；

**sunshine**：阳光信用；

**registration_time**：注册时间；

**statuses_count**：微博数；

**followers_count**：粉丝数；

**follow_count**：关注数；

**description**：微博简介；

**profile_url**：微博主页，如<https://m.weibo.cn/u/1000000005?uid=1000000005&luicode=10000011&lfid=1005051000000005>;

**profile_image_url**：微博头像url；

**avatar_hd**：微博高清头像url；

**urank**：微博等级；

**mbrank**：微博会员等级，普通用户会员等级为0；

**verified**：微博是否认证，取值为true和false；

**verified_type**：微博认证类型，没有认证值为-1，个人认证值为0，企业认证值为2，政府认证值为3，这些类型仅是个人猜测，应该不全，大家可以根据实际情况判断；

**verified_reason**：微博认证信息，只有认证用户拥有此属性。

***
**weibo**表

**user_id**：存储微博用户id，如"1000000005"；

**screen_name**：存储微博昵称，如"示例用户"；

**id**：存储微博id；

**text**：存储微博正文；

**article_url**：存储微博中头条文章的url，如果微博中存在头条文章，就获取该头条文章的url，否则该值为''；

**pics**：存储原创微博的原始图片url。若某条微博有多张图片，则存储多个url，以英文逗号分割；若该微博没有图片，则值为''；

**video_url**：存储原创微博的视频url和Live Photo中的视频url。若某条微博有多个视频，则存储多个url，以英文分号分割；若该微博没有视频，则值为''；

**location**：存储微博的发布位置。若某条微博没有位置信息，则值为''；

**created_at**：存储微博的发布时间；

**source**：存储微博的发布工具；

**attitudes_count**：存储微博获得的点赞数；

**comments_count**：存储微博获得的评论数；

**reposts_count**：存储微博获得的转发数；

**topics**：存储微博话题，即两个#中的内容。若某条微博没有话题信息，则值为''；

**at_users**：存储微博@的用户。若某条微博没有@的用户，则值为''；

**retweet_id**：存储转发微博中原始微博的微博id。若某条微博为原创微博，则值为''。

</details>
**SQLite数据库写入**

脚本会自动建立并配置数据库文件`weibodata.db`。

### 5.运行脚本

大家可以根据自己的运行环境选择运行方式，Linux可以通过

```bash
python weibo.py
```

运行;

配置写回（可选，程序不会自动修改 `config.json`）：

1. 先预览差异（不写文件）：

```bash
python weibo.py --check-config
```

2. 确认后写回（会先备份 `config.json`；尽量保留原有注释与排版，无法安全保留时会回退为规范化 JSON 写入并提示）：

```bash
python weibo.py --update-config
```

不写回也能正常使用（程序兼容旧键名运行），只是配置文件里保留旧键名。

需要全量核对存档（只读，枚举平台完整时间线）时，运行独立脚本：

```bash
python scripts/weibo_archive_check_full.py
```

运行 `python scripts/weibo_archive_check_full.py --help` 可查看参数（`--users`、`--since`、`--max-pages`）；该脚本受配置项 `weibo_archive_check_full` 控制（默认 0；设为 1 才能运行）。报告输出目录由 `weibo_archive_check_full_dir` 控制（默认 `output_directory/report`），实际写入 `<该目录>/<用户目录名>/`。

### 6.按需求修改脚本（可选）

本部分为可选内容：如果你只需要抓取微博并保存，不需要自己改代码或添加新功能，可以忽略此部分。

程序入口是 `weibo.py` 的 `main()`，默认调用流程如下：

```python
from weibo import Weibo, get_config

config = get_config()
wb = Weibo(config)
wb.start()  # 抓取、写存档、生成报告
```

代码组织：`weibo.py` 是主入口与 `Weibo` 门面；`util/` 按功能分组（`core`/`config`/`fetch`/`http`/`markdown`/`edit`/`media`/`comment`/`report`/`storage`/`misc`/`legacy`）；`scripts/` 是运维脚本。配置统一在 `config/config.json`。

也可以在自己的脚本中调用 `Weibo` 类：

```python
from weibo import Weibo, get_config

wb = Weibo(get_config())
wb.start()  # 与命令行运行等价；每处理一个用户，wb.user / wb.weibo 为该用户的结果

print(wb.user)   # 用户信息（dict）
print(wb.weibo)  # 该用户本轮抓取到的微博（list）
```

爬取结果：

* `wb.user`：目标用户信息（dict），字段见[输出](#输出)章节「用户信息」；
* `wb.weibo`：该用户本轮抓取到的微博列表（list），字段见[输出](#输出)章节「微博信息」。

按需求修改时建议：

1. **优先改配置**：绝大多数需求（抓取范围、存档格式、报告开关、媒体下载、评论回填）都能通过 `config/config.json` 完成，不需要改代码；
2. **新增输出后端或行为**：在 `util/` 对应分组内实现，再由 `weibo.py` 的薄封装接入；
3. **验证**：`python3 -m unittest discover -s tests`（测试不联网，第三方依赖为替身）；真实接口与数据只能现场验证，见 [docs/部署与验证.md](./docs/部署与验证.md) 与 [docs/未验证功能.md](./docs/未验证功能.md)。

### 7.定期自动爬取微博（可选）

我们爬取了微博以后，很多微博账号又可能发了一些新微博，定期自动爬取微博就是每隔一段时间自动运行程序，自动爬取这段时间产生的新微博（忽略以前爬过的旧微博）。本部分为可选部分，如果不需要可以忽略。

思路是**利用第三方软件，如crontab，让程序每隔一段时间运行一次**。因为是要跳过以前爬过的旧微博，只爬新微博。所以需要**设置一个动态的since_date**。很多时候我们使用的since_date是固定的，比如since_date="2018-01-01"，程序就会按照这个设置从最新的微博一直爬到发布时间为2018-01-01的微博（包括这个时间）。因为我们想追加新微博，跳过旧微博。第二次爬取时since_date值就应该是当前时间到上次爬取的时间。 如果我们使用最原始的方式实现追加爬取，应该是这样：

```
假如程序第一次执行时间是2019-06-06，since_date假如为2018-01-01，那这一次就是爬取从2018-01-01到2019-06-06这段时间用户所发的微博；
第二次爬取，我们想要接着上次的爬，需要手动将since_date值设置为上次程序执行的日期，即2019-06-06
```

上面的方法太麻烦，因为每次都要手动设置since_date。因此我们需要动态设置since_date，即程序根据实际情况，自动生成since_date。
有两种方法实现动态更新since_date：

**方法一：将since_date设置成整数**

将config.json文件中的since_date设置成整数，如：

```
"since_date": 10,
```

这个配置告诉程序爬取最近10天的微博，更准确说是爬取发布时间从10天前到本程序开始执行时之间的微博。这样since_date就是一个动态的变量，每次程序执行时，它的值就是当前日期减10。配合crontab每9天或10天执行一次，就实现了定期追加爬取。

**方法二：将上次执行程序的时间写入文件（推荐）**

这个方法很简单，就是用户把要爬的用户id写入txt文件，然后再把文件路径赋值给config.json中的user_id_list参数。

txt文件名格式可以参考[程序设置](#3程序设置)中的设置user_id_list部分，这样设置就全部结束了。

说下这个方法的原理和好处，假如你的txt文件内容为：

```
1000000005
1000000004 示例用户2
1000000006 示例用户3 2019-01-01
```

第一次执行时，因为第一行和第二行都没有写时间，程序会按照config.json文件中since_date的值爬取，第三行有时间“2019-01-01”，程序就会把这个时间当作since_date。每个用户爬取结束程序都会自动更新txt文件，每一行第一部分是user_id，第二部分是用户昵称，第三部分是程序准备爬取该用户第一条微博（最新微博）时的日期。爬完三个用户后，txt文件的内容自动更新为：

```
1000000005 示例用户 2020-01-18
1000000004 示例用户2 2020-01-18
1000000006 示例用户3 2020-01-18
```

下次再爬取微博的时候，程序会把每行的时间数据作为since_date。这样的好处一是不用修改since_date，程序自动更新；二是每一个用户都可以单独拥有只属于自己的since_date，每个用户的since_date相互独立，互不干扰，格式为`yyyy-mm-dd`或整数。比如，现在又添加了一个新用户，以杨紫的微博为例，你想获取她2018-01-23到现在的全部微博，可以这样修改txt文件：

```
1000000005 示例用户 2020-01-18
1000000004 示例用户2 2020-01-18
1000000006 示例用户3 2020-01-18
1227368500 杨紫 3 梦想,希望
```

注意每一行的用户配置参数以空格分隔，如果第一个参数全部由数字组成，程序就认为此行为一个用户的配置，否则程序会认为该行只是注释，跳过该行；第二个参数可以为任意格式，建议写用户昵称；第三个如果是日期格式（yyyy-mm-dd），程序就将该日期设置为用户自己的since_date，否则使用config.json中的since_date爬取该用户的微博，第二个参数和第三个参数也可以不填。
也可以设置第四个参数，将被读取为query_list。

**方法三：将`const.py`文件中的运行模式改为`append`**

以追加模式运行程序，每次运行，每个id只获取最新的微博，而不是全部，避免频繁备份微博导致过多的请求次数。

注意：

* 该模式会跳过置顶微博。
* 若采集信息后用户又编辑微博，则不会记录编辑内容。

### 8.使用docker

> **状态：构建链已配置，未实测构建。** 根目录 `.dockerignore` 已排除 `weibo_data/`、`Output/`、
> `config/config.json`、`tests/`、`docs/`；`deploy/docker-compose.yml` 的 build context 为仓库根、
> volume 指向 `../config/config.json` 与 `../weibo_data`；`docker compose config` 解析通过。
> 首次构建会真实执行 `pip install -r requirements.txt`，需要网络。日常部署仍建议 rsync + cron。

推荐用法（在 `deploy/` 目录下）：

```bash
# 先在宿主机准备配置（不要把 config.json 打进镜像）
cp ../config/config.json.example ../config/config.json   # 填写 cookie / user_id_list

docker compose up -d --build      # 构建并后台启动（循环间隔由 schedule_interval 控制）
docker compose logs -f            # 观察抓取日志
```

等价的手动命令（从仓库根构建）：

```bash
docker build -f deploy/Dockerfile -t weibo-crawler .
docker run -it -d \
  --name weibo-crawler \
  -v "$(pwd)/config/config.json:/app/config/config.json:ro" \
  -v "$(pwd)/weibo_data:/app/weibo_data" \
  -e schedule_interval=1 \
  weibo-crawler
```

注意事项：

- 时区：`deploy/Dockerfile` 已安装 tzdata 并固定 `Asia/Shanghai`，无需再设 `TZ`；
- cookie：优先用环境变量 `WEIBO_COOKIE`（优先级最高）；也可把 `config/cookie.txt` 挂载进容器并在
  `config.json` 里写相对路径（相对项目根解析，不是容器当前目录）；
- 数据卷：必须挂载 `output_directory`（默认 `weibo_data`），否则容器重建后数据丢失。

## 如何获取user_id

1.打开网址<https://weibo.cn>，搜索我们要找的人，如"示例用户"，进入她的主页；

![](https://github.com/dataabc/media/blob/master/weiboSpider/images/user_home.png)
2.按照上图箭头所指，点击"资料"链接，跳转到用户资料页面；

![](https://github.com/dataabc/media/blob/master/weiboSpider/images/user_info.png)
如上图所示，示例用户微博资料页的地址为"<https://weibo.cn/1000000005/info>"，其中的"1000000005"即为此微博的user_id。

事实上，此微博的user_id也包含在用户主页(<https://weibo.cn/u/1000000005?f=search_0>)中，之所以我们还要点击主页中的"资料"来获取user_id，是因为很多用户的主页不是"<https://weibo.cn/user_id?f=search_0>"的形式，而是"<https://weibo.cn/个性域名?f=search_0>"或"<https://weibo.cn/微号?f=search_0>"的形式。其中"微号"和user_id都是一串数字，如果仅仅通过主页地址提取user_id，很容易将"微号"误认为user_id。

## 添加cookie与不添加cookie的区别（可选）

对于微博数2000条及以下的微博用户，不添加cookie可以获取其用户信息和大部分微博；对于微博数2000条以上的微博用户，不添加cookie可以获取其用户信息和最近2000条微博中的大部分，添加cookie可以获取其全部微博。以2020年1月2日示例用户的微博为例，此时她共有1085条微博，在不添加cookie的情况下，可以获取到1026条微博，大约占全部微博的94.56%，而在添加cookie后，可以获取全部微博。其他用户类似，大部分都可以在不添加cookie的情况下获取到90%以上的微博，在添加cookie后可以获取全部微博。具体原因是，大部分微博内容都可以在[移动版](https://m.weibo.cn/)匿名获取，少量微博需要用户登录才可以获取，所以这部分微博在不添加cookie时是无法获取的。

有少部分微博用户，不添加cookie可以获取其微博，无法获取其用户信息。对于这种情况，要想获取其用户信息，是需要cookie的。

如需抓取微博转发，请添加cookie。

## 如何获取cookie（可选）

1. 打开浏览器，访问 [https://m.weibo.cn](https://m.weibo.cn)或[https://weibo.cn](https://weibo.cn)并登录。
2. 按 `F12` 打开开发者工具，切换到 **Network** (网络) 标签页。
3. 将复制的 Cookie 填入 `config.json`。

---

## 如何检测cookie是否有效（可选）

本程序cookie检查的逻辑是：使用cookie来源账号发布**限定范围的**微博，若cookie可用，则可以读取到该微博，否则读取不到。

**操作方法**

1. 使用cookie的来源账号发布一条微博，该账号和微博需要满足以下条件：

   * 该微博必须是**非公开可见**的，后续需要根据可见性判断cookie是否有效；

   * 该微博需要是最近5条微博，不能在发布测试用微博内容后又发很多新微博；

   * 在`config.json`配置中的since_date之后，该账号必须有大于9条微博。

2. 打开`const.py`，把 `CHECK_COOKIE` 里的 `"CHECK": False` 改为 `"CHECK": True`，并把 `"HIDDEN_WEIBO": "微博内容"` 的值改为你发的那条限定范围微博的内容。

3. 将提供cookie的微博id放置在配置文件（`config/config.json`，旧部署仍兼容根目录 `config.json`）的`"user_id_list"`设置项数组中的第一个。例如提供cookie的微博id为`123456`，则`"user_id_list"`设置为`"user_id_list":["123456", "<其余id...>"]`。

注：本方法也将会抓取提供cookie账号的微博内容。

在间歇运行程序时，cookie无效会导致程序不能按照预设目标执行，因此可以打开cookie通知功能。本项目使用开源项目[pushdeer](https://github.com/easychen/pushdeer)进行通知，在使用前用户需要申请push_key，具体可查看官网了解。打开方法为：

1. 在`const.py`文件中，将 `NOTIFY` 里的 `"NOTIFY": False` 改为 `"NOTIFY": True`；
2. 将 `"PUSH_KEY": ""` 的 `""` 替换为 `"<你的push_key>"`

## API服务

> **状态：可启动，未验证**。REST API 示例在 `scripts/legacy/service.py`，主流程不依赖本服务。

启动方式：

```bash
pip install -r requirements.txt -r requirements-legacy.txt   # 需要 flask
python scripts/legacy/service.py                             # 默认 http://127.0.0.1:5000
```

API 用于触发抓取任务与查询任务状态/微博数据；抓取接口会真实启动抓取，查询接口依赖 SQLite
（`write_mode` 含 `"sqlite"` 才有数据）。**主流程不依赖本服务**，定时运行请直接用 cron 调
`weibo.py`。接口文档见 [API说明](./docs/API.md)（文件头已标注「可启动未验证」），
未验证清单见 [docs/未验证功能.md](./docs/未验证功能.md) A4。
