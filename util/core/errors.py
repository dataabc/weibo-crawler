"""异常分类辅助。

``NameError`` / ``AttributeError`` 等属于**代码缺陷**；网络超时、接口字段缺失、
文件写失败属于**业务失败**。两者常被 ``except Exception`` 兜底，日志与报告里
混在一起，难以判断「该重试、该等平台恢复，还是该报 bug」。

本模块提供最小分类工具，供顶层入口、报告与测试使用；不改变任何捕获行为。
"""

from __future__ import annotations

#: 一旦出现就说明「代码本身有问题」的异常类型（而不是环境/数据问题）。
PROGRAMMING_ERROR_TYPES = (
    NameError,
    UnboundLocalError,
    AttributeError,
    TypeError,
    ImportError,
    SyntaxError,
    RecursionError,
)


def is_programming_error(exc: BaseException) -> bool:
    """判断异常是否属于代码缺陷类（而非业务/环境失败）。"""
    return isinstance(exc, PROGRAMMING_ERROR_TYPES)


def describe_exception(exc: BaseException) -> str:
    """把异常转成「类型: 消息」的短描述（用于报告字段）。"""
    return f"{type(exc).__name__}: {exc}"
