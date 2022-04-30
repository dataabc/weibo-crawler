import const

"""
运行模式
可以是追加模式append或覆盖模式overwrite
append模式：仅可在sqlite启用时使用。每次运行每个id只获取最新的微博，对于以往的即使是编辑过的微博，也不再获取。
overwrite模式：每次运行都会获取全量微博。
注意：overwrite模式下暂不能记录上次获取微博的id，因此从overwrite模式转为append模式时，仍需获取所有数据
"""
const.MODE = "overwrite"

"""
检查cookie是否有效
默认不需要检查cookie
如果检查cookie，需要参考以下链接设置
config中science_date一定要确保测试号获得的微博数在不含测试微博的情况下大于9
https://github.com/dataabc/weibo-crawler#%E5%A6%82%E4%BD%95%E6%A3%80%E6%B5%8Bcookie%E6%98%AF%E5%90%A6%E6%9C%89%E6%95%88%E5%8F%AF%E9%80%89
"""
const.CHECK_COOKIE = {
    "CHECK": False,  # 是否检查cookie
    "CHECKED": False,  # 这里不要动，判断已检查了cookie的标志位
    "EXIT_AFTER_CHECK": False,  # 这里不要动，append模式中已完成增量微博抓取，仅等待cookie检查的标志位
    "HIDDEN_WEIBO": "微博内容",  # 你可能发现平台会自动给你的微博自动加个空格，但这里你不用加空格
    "GUESS_PIN": False,  # 这里不要动，因为微博取消了“置顶”字样的显示，因此默认猜测所有人第一条都是置顶
}
const.NOTIFY = {
    "NOTIFY": False,  # 是否通知
    "PUSH_KEY": "",  # 这里使用push_deer做通知，填入pushdeer的pushkey
}
