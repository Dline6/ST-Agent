"""定时调度错误类型（03 §6；失败显式化，01 §5）。

判定与登记面（`targets` / `record_run`）非法入参 fail-fast；查询面按需异常
（未注册目标即 ``SchedulerTargetNotFoundError``）。执行面的失败一律由
``SkillRunner`` 包 ``ResultEnvelope`` 并落到运行状态，本模块不抛运行时业务异常。
"""

__all__ = [
    "CronSyntaxError",
    "SchedulerError",
    "SchedulerTargetNotFoundError",
    "SchedulerValidationError",
]


class SchedulerError(Exception):
    """定时调度子系统错误基类。"""


class SchedulerValidationError(SchedulerError, ValueError):
    """判定 / 登记入参非法（时钟不带时区、目标标识不合形态、状态枚举越界）。"""


class CronSyntaxError(SchedulerValidationError):
    """cron 表达式非法（段数不符 / 取值越界 / 原子语法不识别）。"""


class SchedulerTargetNotFoundError(SchedulerError, KeyError):
    """查询的调度目标未在运行环境中注册（03 §6 的目标来自工作流与 Skill 注册面）。"""
