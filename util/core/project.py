"""项目根目录定位（单一来源）。

代码分布在 ``util/`` 子包中；子模块若用 ``os.path.split(os.path.realpath(__file__))[0]``
当「项目根」，基准目录会变成模块所在子包（如 ``config/user_id_list.txt`` 被解析成
``<repo>/util/config/config/user_id_list.txt``）。

本模块按包结构固定上溯：``util/core/project.py`` → ``util/core`` → ``util``
→ 项目根。所有需要「项目根」的模块统一调用 :func:`project_root`，不要在子包里
再用 ``__file__`` 自行计算。
"""

from __future__ import annotations

import os


def project_root() -> str:
    """返回项目根目录（即含 ``weibo.py`` 的那一层）。"""
    here = os.path.dirname(os.path.realpath(__file__))  # <repo>/util/core
    return os.path.dirname(os.path.dirname(here))  # <repo>
