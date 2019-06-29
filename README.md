# 功能
爬取新浪微博信息，并写入csv/txt文件，文件名为目标用户id加".csv"和".txt"的形式，同时还会下载该微博原始图片(可选)。<br>
<br>
以爬取迪丽热巴的微博为例，她的微博昵称为"Dear-迪丽热巴"，id为1669879400(后面会讲如何获取用户id)。我们选择爬取她的原创微博。程序会自动生成一个weibo文件夹，我们以后爬取的所有微博都被存储在这里。然后程序在该文件夹下生成一个名为"Dear-迪丽热巴"的文件夹，迪丽热巴的所有微博爬取结果都在这里。"Dear-迪丽热巴"文件夹里包含一个csv文件和一个img文件夹，img文件夹用来存储下载到的图片。<br>
<br>
csv文件结果如下所示：
![](https://picture.cognize.me/cognize/github/weibo-crawler/weibo_csv.png)*1669879400.csv*<br>
本csv文件是爬取“全部微博”(原创微博+转发微博)的结果文件。因为迪丽热巴很多微博本身都没有图片、发布工具、位置、话题和@用户等信息，所以当这些内容没有时对应位置为空。"是否原创"列用来标记是否为原创微博，
当为转发微博时，文件中还包含**原始用户id**、**原始用户昵称**、**原始微博id**、**原始微博正文**、**原始微博原始图片url**、**原始微博位置**、**原始微博日期**、**原始微博工具**、**原始微博点赞数**、**原始微博评论数**、**原始微博转发数**、**原始微博话题**、**原始微博@用户**等信息。原创微博因为没有这些转发信息，所以对应位置为空。若爬取的是“全部原创微博”，则csv文件中
不会包含“是否原创”及其之后的转发属性列；<br>
下载的图片如下所示：
![](https://picture.cognize.me/cognize/github/weibo-crawler/picture.png)*img文件夹*<br>
本次下载了769张图片，大小一共1.16GB，包括她原创微博中的图片和转发微博转发理由中的图片。图片名为yyyymmdd+微博id的形式，若某条微博存在多张图片，则图片名中还会包括它在微博图片中的序号。若某图片下载失败，该图片url会被写到图片同目录下的not_downloaded_pictures.txt文件里；若图片全部下载成功则不会生成not_downloaded_pictures.txt。

# 输入
用户id，例如新浪微博昵称为"Dear-迪丽热巴"的id为"1669879400"

# 输出
用户信息<br>
- 用户id：微博用户id，为一串数字形式
- 用户昵称：微博用户昵称，如"Dear-迪丽热巴"
- 性别：微博用户性别
- 微博数：用户的全部微博数（转发微博+原创微博）
- 粉丝数：用户的粉丝数
- 关注数：用户关注的微博数量
- 简介：用户简介
- 主页地址：微博移动版主页url
- 头像url：用户头像url
- 高清头像url：用户高清头像url
- 微博等级：用户微博等级
- 会员等级：微博会员用户等级，普通用户该等级为0
- 是否认证：用户是否认证，如个人认证、企业认证等
- 认证类型：用户认证类型，如个人你、企业、政府等
- 认证信息：为认证用户特有，用户信息栏显示的认证信息
***
微博信息<br>
- 微博id：微博唯一标志
- 微博内容：微博正文
- 原始图片url：原创微博图片和转发微博转发理由中图片的url，若某条微博存在多张图片，每个url以英文逗号分隔，若没有图片则值为无
- 微博发布位置：位置微博中的发布位置
- 微博发布时间：微博发布时的时间，精确到分
- 点赞数：微博被赞的数量
- 转发数：微博被转发的数量
- 评论数：微博被评论的数量
- 微博发布工具：微博的发布工具，如iPhone客户端、HUAWEI Mate 20 Pro等
- 话题：微博话题，即两个#中的内容
- @用户：微博@的用户
- 结果文件：保存在当前目录weibo文件夹下以用户昵称为名的文件夹里，名字为"user_id.csv"和"user_id.txt"的形式
- 微博图片：原创微博中的图片和转发微博转发理由中的图片，保存在以用户昵称为名的文件夹下的img文件夹里
- 原始微博：为转发微博所特有，是转发微博中那条被转发的微博，存储为字典形式，包含了上述微博信息中的所有内容，如微博id、微博内容等等

# 运行环境
- 开发语言：python2/python3
- 系统： Windows/Linux/macOS

# 使用说明
## 1.下载脚本
```bash
$ git clone https://github.com/dataabc/weibo-crawler.git
```
运行上述命令，将本项目下载到当前目录，如果下载成功当前目录会出现一个名为"weibo-crawler"的文件夹；
## 2.设置user_id
打开weibospider文件夹下的"**weibo.py**"文件，将**user_id**替换成想要爬取的微博的user_id，后面会详细讲解如何获取user_id;
## 3.安装依赖
```
pip install -r requirements.txt
```
## 4.运行脚本
大家可以根据自己的运行环境选择运行方式，Linux可以通过
```bash
$ python weibo.py
```
运行;
## 5.按需求修改脚本（可选）
本脚本是一个Weibo类，用户可以按照自己的需求调用Weibo类。
例如用户可以直接在"weibo.py"文件中调用Weibo类，具体调用代码示例如下：
```python
user_id = 1669879400
filter = 1
pic_download = 1
wb = Weibo(user_id, filter, pic_download) #调用Weibo类，创建微博实例wb
wb.start()  #爬取微博信息
```
user_id可以改成任意合法的用户id（爬虫的微博id除外）；filter默认值为0，表示爬取所有微博信息（转发微博+原创微博），为1表示只爬取用户的所有原创微博；pic_download默认值为0，代表不下载微博原始图片，1代表下载；wb是Weibo类的一个实例，也可以是其它名字，只要符合python的命名规范即可；通过执行wb.start() 完成了微博的爬取工作。在上述代码执行后，我们可以得到很多信息：<br>
**wb.user**：存储目标微博用户信息；<br>
wb.user包含爬取到的微博用户信息，如**用户id**、**用户昵称**、**性别**、**微博数**、**粉丝数**、**关注数**、**简介**、**主页地址**、**头像url**、**高清头像url**、**微博等级**、**会员等级**、**是否认证**、**认证类型**、**认证信息**等，大家可以点击"详情"查看具体用法。

<details>
  
<summary>详情</summary>

**id**：微博用户id，取值方式为wb.user['id'],由一串数字组成；<br>
**screen_name**：微博用户昵称，取值方式为wb.user['screen_name']；<br>
**gender**：微博用户性别，取值方式为wb.user['gender']，取值为f或m，分别代表女和男;<br>
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
**verified_type**：微博认证类型，取值方式为wb.user['verified_type']，为认证值为-1，一般个人认证值为0，企业认证值为2，政府认证值为3，这些类型仅是个人才是，应该不全，大家可以根据实际情况判断；<br>
**verified_reason**：微博认证信息，取值方式为wb.user['verified_reason']，只有认证用户拥有此属性。<br>

</details>

**wb.weibo**：存储爬取到的所有微博信息；<br>
wb.weibo包含爬取到的所有微博信息，如**微博id**、**正文**、**原始图片url**、**位置**、**日期**、**发布工具**、**点赞数**、**转发数**、**评论数**、**话题**、**@用户**等。如果爬的是全部微博(原创+转发)，除上述信息之外，还包含**原始用户id**、**原始用户昵称**、**原始微博id**、**原始微博正文**、**原始微博原始图片url**、**原始微博位置**、**原始微博日期**、**原始微博工具**、**原始微博点赞数**、**原始微博评论数**、**原始微博转发数**、**原始微博话题**、**原始微博@用户**等信息。wb.weibo是一个列表，包含了爬取的所有微博信息。wb.weibo[0]为爬取的第一条微博，wb.weibo[1]为爬取的第二条微博，以此类推。当filter=1时，wb.weibo[0]为爬取的第一条**原创**微博，以此类推。wb.weibo[0]["id"]为第一条微博的id，wb.weibo[0]["text"]为第一条微博的正文，wb.weibo[0]["created_at"]为第一条微博的发布时间，还有其它很多信息不在赘述，大家可以点击下面的"详情"查看具体用法。
<details>
  
<summary>详情</summary>

**user_id**：存储微博用户id。如wb.weibo[0]['user_id']为最新一条微博的用户id；<br>
**screen_name**：存储微博昵称。如wb.weibo[0]['screen_name']为最新一条微博的昵称；<br>
**id**：存储微博id。如wb.weibo[0]['id']为最新一条微博的id；<br>
**text**：存储微博正文。如wb.weibo[0]['text']为最新一条微博的正文；<br>
**pics**：存储原创微博的原始图片url。如wb.weibo[0]['pics']为最新一条微博的原始图片url，若该条微博有多张图片，则存储多个url，以英文逗号分割；若该微博没有图片，则值为""；<br>
**location**：存储微博的发布位置。如wb.weibo[0]['location']为最新一条微博的发布位置，若该条微博没有位置信息，则值为""；<br>
**created_at**：存储微博的发布时间。如wb.weibo[0]['created_at']为最新一条微博的发布时间；<br>
**source**：存储微博的发布工具。如wb.weibo[0]['source']为最新一条微博的发布工具；<br>
**attitudes_count**：存储微博获得的点赞数。如wb.weibo[0]['attitudes_count']为最新一条微博获得的点赞数；<br>
**comments_count**：存储微博获得的评论数。如wb.weibo[0]['comments_count']为最新一条微博获得的评论数；<br>
**reposts_count**：存储微博获得的转发数。如wb.weibo[0]['reposts_count']为最新一条微博获得的转发数；<br>
**topics**：存储微博话题，即两个#中的内容。如wb.weibo[0]['topics']为最新一条微博的话题，若该条微博没有话题信息，则值为""；<br>
**at_users**：存储微博@的用户。如wb.weibo[0]['at_users']为最新一条微博@的用户，若该条微博没有@的用户，则值为""；<br>
**retweet**：存储转发微博中原始微博的全部。假如wb.weibo[0]为转发微博，则wb.weibo[0]['retweet']为该转发微博的原始微博，它存储的内容与wb.weibo[0]一样，只是没有retweet属性;若该条微博为原创微博，则wb[0]没有"retweet"属性，大家可以点击"详情"查看具体用法。<br>
<details>
  
<summary>详情</summary>

假设爬取到的第i条微博为转发微博，则它存在以下信息：<br>
**user_id**：存储原始微博用户id。wb.weibo[i-1]['retweet']['user_id']为该原始微博的用户id；<br>
**screen_name**：存储原始微博昵称。wb.weibo[i-1]['retweet']['screen_name']为该原始微博的昵称；<br>
**id**：存储原始微博id。wb.weibo[i-1]['retweet']['id']为该原始微博的id；<br>
**text**：存储原始微博正文。wb.weibo[i-1]['retweet']['text']为该原始微博的正文；<br>
**pics**：存储原始微博的原始图片url。wb.weibo[i-1]['retweet']['pics']为该原始微博的原始图片url，若该原始微博有多张图片，则存储多个url，以英文逗号分割；若该原始微博没有图片，则值为""；<br>
**location**：存储原始微博的发布位置。wb.weibo[i-1]['retweet']['location']为该原始微博的发布位置，若该原始微博没有位置信息，则值为""；<br>
**created_at**：存储原始微博的发布时间。wb.weibo[i-1]['retweet']['created_at']为该原始微博的发布时间；<br>
**source**：存储原始微博的发布工具。wb.weibo[i-1]['retweet']['source']为该原始微博的发布工具；<br>
**attitudes_count**：存储原始微博获得的点赞数。wb.weibo[i-1]['retweet']['attitudes_count']为该原始微博获得的点赞数；<br>
**comments_count**：存储原始微博获得的评论数。wb.weibo[i-1]['retweet']['comments_count']为该原始微博获得的评论数；<br>
**reposts_count**：存储原始微博获得的转发数。wb.weibo[i-1]['retweet']['reposts_count']为该原始微博获得的转发数；<br>
**topics**：存储原始微博话题，即两个#中的内容。wb.weibo[i-1]['retweet']['topics']为该原始微博的话题，若该原始微博没有话题信息，则值为""；<br>
**at_users**：存储原始微博@的用户。wb.weibo[i-1]['retweet']['at_users']为该原始微博@的用户，若该原始微博没有@的用户，则值为""；<br>

</details>

</details>

# 如何获取user_id
1.打开网址<https://weibo.cn>，搜索我们要找的人，如”迪丽热巴“，进入她的主页；<br>
![](https://picture.cognize.me/cognize/github/weibospider/user_home.png)
2.按照上图箭头所指，点击"资料"链接，跳转到用户资料页面；<br>
![](https://picture.cognize.me/cognize/github/weibospider/user_info.png)
如上图所示，迪丽热巴微博资料页的地址为"<https://weibo.cn/1669879400/info>"，其中的"1669879400"即为此微博的user_id。<br>
事实上，此微博的user_id也包含在用户主页(<https://weibo.cn/u/1669879400?f=search_0>)中，之所以我们还要点击主页中的"资料"来获取user_id，是因为很多用户的主页不是"<https://weibo.cn/user_id?f=search_0>"的形式，而是"<https://weibo.cn/个性域名?f=search_0>"或"<https://weibo.cn/微号?f=search_0>"的形式。其中"微号"和user_id都是一串数字，如果仅仅通过主页地址提取user_id，很容易将"微号"误认为user_id。
