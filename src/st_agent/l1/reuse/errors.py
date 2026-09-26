"""输出复用错误类型（03 §1.3；失败显式化，01 §5）。

``register`` 属配置/登记面，非法入参 fail-fast；``reuse`` 查询面按需
异常（未登记即 ``OutputNotRegisteredError``）。执行面的失败一律由
``SkillRunner`` 包 ``ResultEnvelope``，本模块不抛运行时业务异常。
"""

__all__ = [
    "OutputNotRegisteredError",
    "ReuseError",
    "ReuseValidationError",
]


class ReuseError(Exception):
    """输出复用子系统错误基类。"""


class ReuseValidationError(ReuseError, ValueError):
    """登记 / 查询入参非法（空标识、非可复用状态、oracle 不合口径）。"""


class OutputNotRegisteredError(ReuseError, KeyError):
    """引用的 ``skill_run_id`` 未登记为可复用结果（03 §1.3）。"""
