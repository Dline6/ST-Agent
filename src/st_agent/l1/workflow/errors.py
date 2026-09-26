"""工作流子系统错误类型（T-L1-003.1 / .3；03 §3–§4；失败显式化，01 §5）。

- 配置面（fail-fast 异常）：``WorkflowNotFoundError`` / ``WorkflowExistsError`` /
  ``WorkflowValidationError``
- 校验面（不抛异常）：``WorkflowDAG.validate`` 返回 ``WorkflowValidation``——
  校验结论是「数据」不是「异常」，供 Studio 回显给用户（03 §4 校验步）
- 试跑面（``.3``）：``TrialError`` 基类 + ``TrialStateError``（非法状态转移，
  如已结束的会话再 step / 注入到已执行节点）+ ``TrialNotFoundError``（记录不存在）
"""

__all__ = [
    "TrialError",
    "TrialNotFoundError",
    "TrialStateError",
    "WorkflowError",
    "WorkflowExistsError",
    "WorkflowNotFoundError",
    "WorkflowValidationError",
]


class WorkflowError(Exception):
    """工作流子系统错误基类。"""


class WorkflowNotFoundError(WorkflowError, KeyError):
    """工作流（或其某个版本）不存在。"""


class WorkflowExistsError(WorkflowError):
    """同 flow_id 已存在——更新走版本发布流程，不得静默覆盖。"""


class WorkflowValidationError(WorkflowError, ValueError):
    """flow_id / 节点 / 连线 / 分组 / 命名 / 参数绑定等配置项非法。"""


class TrialError(WorkflowError):
    """试跑子系统错误基类。"""


class TrialStateError(TrialError):
    """试跑会话的非法状态转移（已结束再推进 / 中止已完成会话 / 注入到已执行节点）。"""


class TrialNotFoundError(TrialError, KeyError):
    """试跑记录不存在（或记录损坏无法解析）。"""
