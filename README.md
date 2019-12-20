* [功能](#功能)
* [输出](#输出)
* [实例](#实例)
* [运行环境](#运行环境)
* [使用说明](#使用说明)
  * [下载脚本](#1下载脚本)
  * [安装依赖](#2安装依赖)
  * [程序设置](#3程序设置)
  * [设置数据库（可选）](#4设置数据库可选)
  * [运行脚本](#5运行脚本)
  * [按需求修改脚本（可选）](#6按需求修改脚本可选)
* [如何获取user_id](#如何获取user_id)

## 功能
连续爬取**一个**或**多个**新浪微博用户（如[Dear-迪丽热巴](https://weibo.cn/u/1669879400)、[郭碧婷](https://weibo.cn/u/1729370543)）的数据，并将结果信息写入文件。写入信息几乎包括了用户微博的所有数据，主要有**用户信息**和**微博信息**两大类，前者包含用户昵称、关注数、粉丝数、微博数等等；后者包含微博正文、发布时间、发布工具、评论数等等，因为内容太多，这里不再赘述，详细内容见[输出](#输出)部分。具体的写入文件类型如下：
- 写入**csv文件**（默认）
- 写入**MySQL数据库**（可选）
- 写入**MongoDB数据库**（可选）
- 下载用户**原创**微博中的原始**图片**（可选）
- 下载用户**转发**微博中的原始**图片**（可选）
- 下载用户**原创**微博中的**视频**（可选）
- 下载用户**转发**微博中的**视频**（可选）<br>
## 输出
**用户信息**<br>
- 用户id：微博用户id，如"1669879400"
- 用户昵称：微博用户昵称，如"Dear-迪丽热巴"
- 性别：微博用户性别
- 微博数：用户的全部微博数（转发微博+原创微博）
- 粉丝数：用户的粉丝数
- 关注数：用户关注的微博数量
- 简介：用户简介
- 主页地址：微博移动版主页url，如<https://m.weibo.cn/u/1669879400?uid=1669879400&luicode=10000011&lfid=1005051669879400>
- 头像url：用户头像url
- 高清头像url：用户高清头像url
- 微博等级：用户微博等级
- 会员等级：微博会员用户等级，普通用户该等级为0
- 是否认证：用户是否认证，为布尔类型
- 认证类型：用户认证类型，如个人认证、企业认证、政府认证等
- 认证信息：为认证用户特有，用户信息栏显示的认证信息
***
**微博信息**<br>
- 微博id：微博的id，为一串数字形式
- 微博bid：微博的bid，与[cookie版](https://github.com/dataabc/weiboSpider)中的微博id是同一个值
- 微博内容：微博正文
- 原始图片url：原创微博图片和转发微博转发理由中图片的url，若某条微博存在多张图片，每个url以英文逗号分隔，若没有图片则值为''
- 视频url: 微博中的视频url，若微博中没有视频，则值为''
- 微博发布位置：位置微博中的发布位置
- 微博发布时间：微博发布时的时间，精确到天
- 点赞数：微博被赞的数量
- 转发数：微博被转发的数量
- 评论数：微博被评论的数量
- 微博发布工具：微博的发布工具，如iPhone客户端、HUAWEI Mate 20 Pro等，若没有则值为''
- 话题：微博话题，即两个#中的内容，若存在多个话题，每个url以英文逗号分隔，若没有则值为''
- @用户：微博@的用户，若存在多个@用户，每个url以英文逗号分隔，若没有则值为''
- 原始微博：为转发微博所特有，是转发微博中那条被转发的微博，存储为字典形式，包含了上述微博信息中的所有内容，如微博id、微博内容等等
- 结果文件：保存在当前目录weibo文件夹下以用户昵称为名的文件夹里，名字为"user_id.csv"形式
- 微博图片：微博中的图片，保存在以用户昵称为名的文件夹下的img文件夹里
- 微博视频：微博中的视频，保存在以用户昵称为名的文件夹下的video文件夹里
## 实例
以爬取迪丽热巴的微博为例，我们需要修改**config.json**文件，文件内容如下：
```
{
    "user_id_list": ["1669879400"],
    "filter": 1,
    "since_date": "1900-01-01",
    "write_mode": ["csv"],
    "original_pic_download": 1,
    "retweet_pic_download": 0,
    "original_video_download": 1,
    "retweet_video_download": 0
}
```

对于上述参数的含义以及取值范围，这里仅作简单介绍，详细信息见[程序设置](#3程序设置)。
>**user_id_list**代表我们要爬取的微博用户的user_id，可以是一个或多个，也可以是文件路径，微博用户Dear-迪丽热巴的user_id为1669879400，具体如何获取user_id见[如何获取user_id](#如何获取user_id)；<br>**filter**的值为1代表爬取全部原创微博，值为0代表爬取全部微博（原创+转发）；<br>**since_date**代表我们要爬取since_date日期之后发布的微博，因为我要爬迪丽热巴的全部原创微博，所以since_date设置了一个非常早的值；<br>**write_mode**代表结果文件的保存类型，我想要把结果写入txt文件和csv文件，所以它的值为["csv", "txt"]，如果你想写入数据库，具体设置见[设置数据库](#4设置数据库可选)；<br>**original_pic_download**值为1代表下载原创微博中的图片，值为0代表不下载；<br>**retweet_pic_download**值为1代表下载转发微博中的图片，值为0代表不下载；<br>**original_video_download**值为1代表下载原创微博中的视频，值为0代表不下载；<br>**retweet_video_download**值为1代表下载转发微博中的视频，值为0代表不下载。<br>

配置完成后运行程序：
```bash
$ python weibo.py
```
程序会自动生成一个weibo文件夹，我们以后爬取的所有微博都被存储在weibo文件夹里。然后程序在该文件夹下生成一个名为"Dear-迪丽热巴"的文件夹，迪丽热巴的所有微博爬取结果都在这里。"Dear-迪丽热巴"文件夹里包含一个csv文件、一个img文件夹和一个video文件夹，img文件夹用来存储下载到的图片，video文件夹用来存储下载到的视频。如果你设置了保存数据库功能，这些信息也会保存在数据库里，数据库设置见[设置数据库](#4设置数据库可选)部分。<br>
csv文件结果如下所示：
![](https://picture.cognize.me/cognize/github/weibo-crawler/weibo_csv.png)*1669879400.csv*<br>
本csv文件是爬取“全部微博”(原创微博+转发微博)的结果文件。因为迪丽热巴很多微博本身都没有图片、发布工具、位置、话题和@用户等信息，所以当这些内容没有时对应位置为空。"是否原创"列用来标记是否为原创微博，
当为转发微博时，文件中还包含转发微博的信息。为了简便起见，姑且将转发微博中被转发的原始微博称为**源微博**，它的用户id、昵称、微博id等都在名称前加上源字，以便与目标用户自己发的微博区分。对于转发微博，程序除了获取用户原创部分的信息，还会获取**源用户id**、**源用户昵称**、**源微博id**、**源微博正文**、**源微博原始图片url**、**源微博位置**、**源微博日期**、**源微博工具**、**源微博点赞数**、**源微博评论数**、**源微博转发数**、**源微博话题**、**源微博@用户**等信息。原创微博因为没有这些转发信息，所以对应位置为空。若爬取的是"全部**原创**微博"，则csv文件中不会包含"是否原创"及其之后的转发属性列；<br>
<br>
下载的图片如下所示：
![](https://picture.cognize.me/cognize/github/weibo-crawler/img.png)*img文件夹*<br>
本次下载了788张图片，大小一共1.21GB，包括她原创微博中的所有图片。图片名为yyyymmdd+微博id的形式，若某条微博存在多张图片，则图片名中还会包括它在微博图片中的序号。若某图片下载失败，程序则会以“weibo_id:pic_url”的形式将出错微博id和图片url写入同文件夹下的not_downloaded.txt里；若图片全部下载成功则不会生成not_downloaded.txt；<br>
<br>
下载的视频如下所示：
![](https://picture.cognize.me/cognize/github/weibo-crawler/video.png)*video文件夹*<br>
本次下载了66个视频，是她原创微博中的视频，视频名为yyyymmdd+微博id的形式。有三个视频因为网络原因下载失败，程序将它们的微博id和视频url分别以“weibo_id:video_url”的形式写到了同文件夹下的not_downloaded.txt里。<br>
因为我本地没有安装MySQL数据库和MongoDB数据库，所以暂时设置成不写入数据库。如果你想要将爬取结果写入数据库，只需要先安装数据库（MySQL或MongoDB），再安装对应包（pymysql或pymongo），然后将mysql_write或mongodb_write值设置为1即可。写入MySQL需要用户名、密码等配置信息，这些配置如何设置见[设置数据库](#4设置数据库可选)部分。
## 运行环境
- 开发语言：python2/python3
- 系统： Windows/Linux/macOS

## 使用说明
### 1.下载脚本
```bash
$ git clone https://github.com/dataabc/weibo-crawler.git
```
运行上述命令，将本项目下载到当前目录，如果下载成功当前目录会出现一个名为"weibo-crawler"的文件夹；
### 2.安装依赖
```bash
$ pip install -r requirements.txt
```
### 3.程序设置
打开**config.json**文件，你会看到如下内容：
```
{
    "user_id_list": ["1669879400"],
    "filter": 1,
    "since_date": "2018-01-01",
    "write_mode": ["csv"],
    "original_pic_download": 1,
    "retweet_pic_download": 0,
    "original_video_download": 1,
    "retweet_video_download": 0,
    "mysql_config": {
        "host": "localhost",
        "port": 3306,
        "user": "root",
        "password": "123456",
        "charset": "utf8mb4"
    }
}
```
下面讲解每个参数的含义与设置方法。<br>
**设置user_id_list**<br>
user_id_list是我们要爬取的微博的id，可以是一个，也可以是多个，例如：
```
"user_id_list": ["1223178222", "1669879400", "1729370543"],
```
上述代码代表我们要连续爬取user_id分别为“1223178222”、 “1669879400”、 “1729370543”的三个用户的微博，具体如何获取user_id见[如何获取user_id](#如何获取user_id)。<br>
user_id_list的值也可以是文件路径，我们可以把要爬的所有微博用户的user_id都写到txt文件里，然后把文件的位置路径赋值给user_id_list。<br>
在txt文件中，每个user_id占一行，也可以在user_id后面加注释（可选），如用户昵称等信息，user_id和注释之间必需要有空格，文件名任意，类型为txt，位置位于本程序的同目录下，文件内容示例如下：
```
1223178222 胡歌
1669879400 迪丽热巴
1729370543 郭碧婷
```
假如文件叫user_id_list.txt，则user_id_list设置代码为：
```
"user_id_list": "user_id_list.txt",
```
**设置filter**<br>
filter控制爬取范围，值为1代表爬取全部原创微博，值为0代表爬取全部微博（原创+转发）。例如，如果要爬全部原创微博，请使用如下代码：
```
"filter": 1,
```
**设置since_date**<br>
since_date值可以是日期，也可以是整数。如果是日期，代表爬取该日期之后的微博，格式应为“yyyy-mm-dd”，如：
```
"since_date": "2018-01-01",
```
代表爬取从2018年1月1日到现在的微博。<br>
如果是整数，代表爬取最近n天的微博，如:
```
"since_date": 10,
```
代表爬取最近10天的微博，这个说法不是特别准确，准确说是爬取发布时间从**10天前到本程序开始执行时**之间的微博。<br>
**设置write_mode**<br>
write_mode控制结果文件格式，取值范围是csv、mongo和mysql，分别代表将结果文件写入csv、txt、MongoDB和MySQL数据库。write_mode可以同时包含这些取值中的一个或几个，如：
```
"write_mode": ["csv"],
```
代表将结果信息写入csv文件。特别注意，如果你想写入数据库，除了在write_mode添加对应数据库的名字外，还应该安装相关数据库和对应python模块，具体操作见[设置数据库](#4设置数据库可选)部分。<br>
**设置original_pic_download**<br>
original_pic_download控制是否下载**原创**微博中的图片，值为1代表下载，值为0代表不下载，如
```
"original_pic_download": 1,
```
代表下载原创微博中的图片。<br>
**设置retweet_pic_download**<br>
retweet_pic_download控制是否下载**转发**微博中的图片，值为1代表下载，值为0代表不下载，如
```
"retweet_pic_download": 0,
```
代表不下载转发微博中的图片。特别注意，本设置只有在爬全部微博（原创+转发），即filter值为0时生效，否则程序会跳过转发微博的图片下载。<br>
**设置original_video_download**<br>
original_video_download控制是否下载**原创**微博中的视频，值为1代表下载，值为0代表不下载，如
```
"original_video_download": 1,
```
代表下载原创微博中的视频。<br>
**设置retweet_video_download**<br>
retweet_video_download控制是否下载**转发**微博中的视频，值为1代表下载，值为0代表不下载，如
```
"retweet_video_download": 0,
```
代表不下载转发微博中的视频。特别注意，本设置只有在爬全部微博（原创+转发），即filter值为0时生效，否则程序会跳过转发微博的视频下载。<br>
**设置mysql_config（可选）**<br>
mysql_config控制mysql参数配置。如果你不需要将结果信息写入mysql，这个参数可以忽略，即删除或保留都无所谓；如果你需要写入mysql且config.json文件中mysql_config的配置与你的mysql配置不一样，请将该值改成你自己mysql中的参数配置。
### 4.设置数据库（可选）
本部分是可选部分，如果不需要将爬取信息写入数据库，可跳过这一步。本程序目前支持MySQL数据库和MongoDB数据库，如果你需要写入其它数据库，可以参考这两个数据库的写法自己编写。<br>
**MySQL数据库写入**<br>
要想将爬取信息写入MySQL，请根据自己的系统环境安装MySQL，然后命令行执行：
```bash
$ pip install pymysql
```
**MongoDB数据库写入**<br>
要想将爬取信息写入MongoDB，请根据自己的系统环境安装MongoDB，然后命令行执行：
```
$ pip install pymongo
```
MySQL和MongDB数据库的写入内容一样。程序首先会创建一个名为"weibo"的数据库，然后再创建"user"表和"weibo"表，包含爬取的所有内容。爬取到的微博**用户信息**或插入或更新，都会存储到user表里；爬取到的**微博信息**或插入或更新，都会存储到weibo表里，两个表通过user_id关联。如果想了解两个表的具体字段，请点击"详情"。
<details>
  
<summary>详情</summary>

**user**表<br>
**id**：微博用户id，如"1669879400"；<br>
**screen_name**：微博用户昵称，如"Dear-迪丽热巴"；<br>
**gender**：微博用户性别，取值为f或m，分别代表女和男；<br>
**statuses_count**：微博数；<br>
**followers_count**：粉丝数；<br>
**follow_count**：关注数；<br>
**description**：微博简介；<br>
**profile_url**：微博主页，如<https://m.weibo.cn/u/1669879400?uid=1669879400&luicode=10000011&lfid=1005051669879400>; <br>
**profile_image_url**：微博头像url；<br>
**avatar_hd**：微博高清头像url；<br>
**urank**：微博等级；<br>
**mbrank**：微博会员等级，普通用户会员等级为0；<br>
**verified**：微博是否认证，取值为true和false；<br>
**verified_type**：微博认证类型，没有认证值为-1，个人认证值为0，企业认证值为2，政府认证值为3，这些类型仅是个人猜测，应该不全，大家可以根据实际情况判断；<br>
**verified_reason**：微博认证信息，只有认证用户拥有此属性。<br>
***
**weibo**表<br>
**user_id**：存储微博用户id，如"1669879400"；<br>
**screen_name**：存储微博昵称，如"Dear-迪丽热巴"；<br>
**id**：存储微博id；<br>
**text**：存储微博正文；<br>
**pics**：存储原创微博的原始图片url。若某条微博有多张图片，则存储多个url，以英文逗号分割；若该微博没有图片，则值为''；<br>
**video_url**：存储原创微博的视频url。若某条微博没有视频，则值为''；<br>
**location**：存储微博的发布位置。若某条微博没有位置信息，则值为''；<br>
**created_at**：存储微博的发布时间；<br>
**source**：存储微博的发布工具；<br>
**attitudes_count**：存储微博获得的点赞数；<br>
**comments_count**：存储微博获得的评论数；<br>
**reposts_count**：存储微博获得的转发数；<br>
**topics**：存储微博话题，即两个#中的内容。若某条微博没有话题信息，则值为''；<br>
**at_users**：存储微博@的用户。若某条微博没有@的用户，则值为''；<br>
**retweet_id**：存储转发微博中原始微博的微博id。若某条微博为原创微博，则值为''。<br>
</details>

### 5.运行脚本
大家可以根据自己的运行环境选择运行方式，Linux可以通过
```bash
$ python weibo.py
```
运行;
### 6.按需求修改脚本（可选）
本部分为可选部分，如果你不需要自己修改代码或添加新功能，可以忽略此部分。<br>
本程序所有代码都位于weibo.py文件，程序主体是一个Weibo类，上述所有功能都是通过在main函数调用Weibo类实现的，默认的调用代码如下：
```python
        if not os.path.isfile('./config.json'):
            sys.exit(u'当前路径：%s 不存在配置文件config.json' %
                     (os.path.split(os.path.realpath(__file__))[0] + os.sep))
        with open('./config.json') as f:
            config = json.loads(f.read())
        wb = Weibo(config)
        wb.start()  # 爬取微博信息
```
用户可以按照自己的需求调用或修改Weibo类。<br>
通过执行本程序，我们可以得到很多信息：<br>
**wb.user**：存储目标微博用户信息；<br>
wb.user包含爬取到的微博用户信息，如**用户id**、**用户昵称**、**性别**、**微博数**、**粉丝数**、**关注数**、**简介**、**主页地址**、**头像url**、**高清头像url**、**微博等级**、**会员等级**、**是否认证**、**认证类型**、**认证信息**等，大家可以点击"详情"查看具体用法。

<details>
  
<summary>详情</summary>

**id**：微博用户id，取值方式为wb.user['id'],由一串数字组成；<br>
**screen_name**：微博用户昵称，取值方式为wb.user['screen_name']；<br>
**gender**：微博用户性别，取值方式为wb.user['gender']，取值为f或m，分别代表女和男；<br>
**statuses_count**：微博数，取值方式为wb.user['statuses_count']；<br>
**followers_count**：微博粉丝数，取值方式为wb.user['followers_count']；<br>
**follow_count**：微博关注数，取值方式为wb.user['follow_count']；<br>
**description**：微博简介，取值方式为wb.user['description']；<br>
**profile_url**：微博主页，取值方式为wb.user['profile_url']; <br>
**profile_image_url**：微博头像url，取值方式为wb.user['profile_image_url']；<br>
**avatar_hd**：微博高清头像url，取值方式为wb.user['avatar_hd']；<br>
**urank**：微博等级，取值方式为wb.user['urank']；<br>
**mbrank**：微博会员等级，取值方式为wb.user['mbrank']，普通用户会员等级为0；<br>
**verified**：微博是否认证，取值方式为wb.user['verified']，取值为true和false；<br>
**verified_type**：微博认证类型，取值方式为wb.user['verified_type']，没有认证值为-1，个人认证值为0，企业认证值为2，政府认证值为3，这些类型仅是个人猜测，应该不全，大家可以根据实际情况判断；<br>
**verified_reason**：微博认证信息，取值方式为wb.user['verified_reason']，只有认证用户拥有此属性。<br>

</details>

**wb.weibo**：存储爬取到的所有微博信息；<br>
wb.weibo包含爬取到的所有微博信息，如**微博id**、**正文**、**原始图片url**、**视频url**、**位置**、**日期**、**发布工具**、**点赞数**、**转发数**、**评论数**、**话题**、**@用户**等。如果爬的是全部微博(原创+转发)，除上述信息之外，还包含**原始用户id**、**原始用户昵称**、**原始微博id**、**原始微博正文**、**原始微博原始图片url**、**原始微博位置**、**原始微博日期**、**原始微博工具**、**原始微博点赞数**、**原始微博评论数**、**原始微博转发数**、**原始微博话题**、**原始微博@用户**等信息。wb.weibo是一个列表，包含了爬取的所有微博信息。wb.weibo[0]为爬取的第一条微博，wb.weibo[1]为爬取的第二条微博，以此类推。当filter=1时，wb.weibo[0]为爬取的第一条**原创**微博，以此类推。wb.weibo[0]['id']为第一条微博的id，wb.weibo[0]['text']为第一条微博的正文，wb.weibo[0]['created_at']为第一条微博的发布时间，还有其它很多信息不在赘述，大家可以点击下面的"详情"查看具体用法。
<details>
  
<summary>详情</summary>

**user_id**：存储微博用户id。如wb.weibo[0]['user_id']为最新一条微博的用户id；<br>
**screen_name**：存储微博昵称。如wb.weibo[0]['screen_name']为最新一条微博的昵称；<br>
**id**：存储微博id。如wb.weibo[0]['id']为最新一条微博的id；<br>
**text**：存储微博正文。如wb.weibo[0]['text']为最新一条微博的正文；<br>
**pics**：存储原创微博的原始图片url。如wb.weibo[0]['pics']为最新一条微博的原始图片url，若该条微博有多张图片，则存储多个url，以英文逗号分割；若该微博没有图片，则值为''；<br>
**video_url**：存储原创微博的视频url。如wb.weibo[0]['video_url']为最新一条微博的视频url；若该微博没有视频，则值为''；<br>
**location**：存储微博的发布位置。如wb.weibo[0]['location']为最新一条微博的发布位置，若该条微博没有位置信息，则值为''；<br>
**created_at**：存储微博的发布时间。如wb.weibo[0]['created_at']为最新一条微博的发布时间；<br>
**source**：存储微博的发布工具。如wb.weibo[0]['source']为最新一条微博的发布工具；<br>
**attitudes_count**：存储微博获得的点赞数。如wb.weibo[0]['attitudes_count']为最新一条微博获得的点赞数；<br>
**comments_count**：存储微博获得的评论数。如wb.weibo[0]['comments_count']为最新一条微博获得的评论数；<br>
**reposts_count**：存储微博获得的转发数。如wb.weibo[0]['reposts_count']为最新一条微博获得的转发数；<br>
**topics**：存储微博话题，即两个#中的内容。如wb.weibo[0]['topics']为最新一条微博的话题，若该条微博没有话题信息，则值为''；<br>
**at_users**：存储微博@的用户。如wb.weibo[0]['at_users']为最新一条微博@的用户，若该条微博没有@的用户，则值为''；<br>
**retweet**：存储转发微博中原始微博的全部信息。假如wb.weibo[0]为转发微博，则wb.weibo[0]['retweet']为该转发微博的原始微博，它存储的属性与wb.weibo[0]一样，只是没有retweet属性;若该条微博为原创微博，则wb[0]没有"retweet"属性，大家可以点击"详情"查看具体用法。<br>
<details>
  
<summary>详情</summary>

假设爬取到的第i条微博为转发微博，则它存在以下信息：<br>
**user_id**：存储原始微博用户id。wb.weibo[i-1]['retweet']['user_id']为该原始微博的用户id；<br>
**screen_name**：存储原始微博昵称。wb.weibo[i-1]['retweet']['screen_name']为该原始微博的昵称；<br>
**id**：存储原始微博id。wb.weibo[i-1]['retweet']['id']为该原始微博的id；<br>
**text**：存储原始微博正文。wb.weibo[i-1]['retweet']['text']为该原始微博的正文；<br>
**pics**：存储原始微博的原始图片url。wb.weibo[i-1]['retweet']['pics']为该原始微博的原始图片url，若该原始微博有多张图片，则存储多个url，以英文逗号分割；若该原始微博没有图片，则值为''；<br>
**video_url**：存储原始微博的视频url。如wb.weibo[i-1]['retweet']['video_url']为该原始微博的视频url；若该微博没有视频，则值为''；<br>
**location**：存储原始微博的发布位置。wb.weibo[i-1]['retweet']['location']为该原始微博的发布位置，若该原始微博没有位置信息，则值为''；<br>
**created_at**：存储原始微博的发布时间。wb.weibo[i-1]['retweet']['created_at']为该原始微博的发布时间；<br>
**source**：存储原始微博的发布工具。wb.weibo[i-1]['retweet']['source']为该原始微博的发布工具；<br>
**attitudes_count**：存储原始微博获得的点赞数。wb.weibo[i-1]['retweet']['attitudes_count']为该原始微博获得的点赞数；<br>
**comments_count**：存储原始微博获得的评论数。wb.weibo[i-1]['retweet']['comments_count']为该原始微博获得的评论数；<br>
**reposts_count**：存储原始微博获得的转发数。wb.weibo[i-1]['retweet']['reposts_count']为该原始微博获得的转发数；<br>
**topics**：存储原始微博话题，即两个#中的内容。wb.weibo[i-1]['retweet']['topics']为该原始微博的话题，若该原始微博没有话题信息，则值为''；<br>
**at_users**：存储原始微博@的用户。wb.weibo[i-1]['retweet']['at_users']为该原始微博@的用户，若该原始微博没有@的用户，则值为''。<br>

</details>

</details>

## 如何获取user_id
1.打开网址<https://weibo.cn>，搜索我们要找的人，如"迪丽热巴"，进入她的主页；<br>
![](https://picture.cognize.me/cognize/github/weibospider/user_home.png)
2.按照上图箭头所指，点击"资料"链接，跳转到用户资料页面；<br>
![](https://picture.cognize.me/cognize/github/weibospider/user_info.png)
如上图所示，迪丽热巴微博资料页的地址为"<https://weibo.cn/1669879400/info>"，其中的"1669879400"即为此微博的user_id。<br>
事实上，此微博的user_id也包含在用户主页(<https://weibo.cn/u/1669879400?f=search_0>)中，之所以我们还要点击主页中的"资料"来获取user_id，是因为很多用户的主页不是"<https://weibo.cn/user_id?f=search_0>"的形式，而是"<https://weibo.cn/个性域名?f=search_0>"或"<https://weibo.cn/微号?f=search_0>"的形式。其中"微号"和user_id都是一串数字，如果仅仅通过主页地址提取user_id，很容易将"微号"误认为user_id。
