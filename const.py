"""全局常量：运行模式、Cookie 检查与通知开关。

历史写法是先 ``import const`` 再 ``const.MODE = ...``（模块自导入后给自己设属性），
效果等价但容易误导。2026-10-29 改为普通模块级常量；外部代码仍按
``const.MODE`` / ``const.CHECK_COOKIE`` / ``const.NOTIFY`` 读取，行为不变。
"""

# 运行模式：追加模式 append 或覆盖模式 overwrite。
# append 模式仅可在 sqlite 启用时使用：每次运行每个 id 只获取最新微博，
#   以往即使编辑过也不再获取。
# overwrite 模式每次运行都会获取全量微博。
# 注意：overwrite 模式下暂不能记录上次获取微博的 id，因此从 overwrite 转为
#   append 时仍需获取所有数据。
MODE = "overwrite"

# 检查 cookie 是否有效。默认不检查；启用时需参考 README 的说明，
# 并确保 config 中 since_date 让测试号获得的微博数（不含测试微博）大于 9。
CHECK_COOKIE = {
    "CHECK": False,  # 是否检查 cookie
    "CHECKED": False,  # 不要动：判断已检查了 cookie 的标志位
    "EXIT_AFTER_CHECK": False,  # 不要动：append 模式中已完成增量抓取、仅等待 cookie 检查的标志位
    "HIDDEN_WEIBO": "微博内容",  # 平台可能自动给微博加一个空格，这里不用加
}

NOTIFY = {
    "NOTIFY": False,  # 是否通知
    "PUSH_KEY": "",  # pushdeer 的 pushkey
}
