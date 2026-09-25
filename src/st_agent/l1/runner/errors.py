"""执行流水线错误类型（03 §1.2；失败显式化，01 §5）。

执行面失败一律走 ``ResultEnvelope``（``validation_failed`` /
``dependency_failed`` / ``failed``），不抛异常；本模块的异常只用于
调用方编程缺陷（非法 skill_id 形状、执行器签名错误）与内部控制流
（依赖环检测）。
"""

__all__ = [
    "RunnerError",
    "RunnerValidationError",
    "CycleDetectedError",
]


class RunnerError(Exception):
    """执行流水线错误基类。"""


class RunnerValidationError(RunnerError, ValueError):
    """调用方传参非法（skill_id 形状错误、trace_id 形状错误等）。"""


class CycleDetectedError(RunnerError):
    """依赖 DAG 成环（内部控制流；对外映射为 ``validation_failed`` 信封）。"""
