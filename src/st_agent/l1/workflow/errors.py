"""工作流子系统错误类型（T-L1-003.1；03 §3；失败显式化，01 §5）。

- 配置面（fail-fast 异常）：``WorkflowNotFoundError`` / ``WorkflowExistsError`` /
  ``WorkflowValidationError``
- 校验面（不抛异常）：``WorkflowDAG.validate`` 返回 ``WorkflowValidation``——
  校验结论是「数据」不是「异常」，供 Studio 回显给用户（03 §4 校验步）
"""

__all__ = [
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
