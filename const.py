import const

"""
运行模式
可以是追加模式append或覆盖模式overwrite
append模式：仅可在sqlite启用时使用。每次运行每个id只获取最新的微博，对于以往的即使是编辑过的微博，也不再获取。
overwrite模式：每次运行都会获取全量微博。
注意：overwrite模式下暂不能记录上次获取微博的id，因此从overwrite模式转为append模式时，仍需获取所有数据
"""
const.MODE = 'overwrite'
