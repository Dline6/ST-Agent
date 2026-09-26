"""Skill Studio（编辑面）错误类型（T-L1-003.4 / .5；03 §4）。

三个面各有失败口径，不可混：

- **画布编辑面**（``canvas``）**不抛异常**——用户级的编辑冲突（重名节点 /
  未知节点 / 成环 / 契约不匹配 …）一律以 ``EditResult.applied=False`` 表达；
  编辑器是全函数，调用方无需 try/except
- **草稿接收面**（``draft``）**显式失败**——``DraftShapeError``（草稿结构非法，
  画布不开）、``DraftSessionError``（会话状态非法：已接受 / 已拒绝的会话再操作）、
  ``DraftAcceptError``（base 无法派生 / base 已存在 / 落盘校验未过）
- **模板库面**（``templates`` / ``empty_state``）**显式失败**——``TemplateError``
  （模板标识不存在 / 种子非法 / 装载校验未过）。依赖缺失不在此列：它走
  §3.2 的 ``CompositeDependencyError``（与复合 Skill 依赖缺失同一判定件）
"""

__all__ = [
    "DraftAcceptError",
    "DraftSessionError",
    "DraftShapeError",
    "StudioError",
    "TemplateError",
]


class StudioError(Exception):
    """Skill Studio 子系统错误基类。"""


class DraftShapeError(StudioError):
    """草稿结构非法（无法构造 ``WorkflowDAG``）——画布不开，显式拒。"""


class DraftSessionError(StudioError):
    """草稿会话的非法操作（已接受 / 已拒绝的会话再接受、微调或拒绝）。"""


class DraftAcceptError(StudioError):
    """草稿「接受」失败（注册名无法派生 / base 已存在 / 工作流校验未过）。"""


class TemplateError(StudioError):
    """模板库失败（标识不存在 / 种子非法 / 装载校验未过 / 去重后注册名非法）。

    依赖缺失**不**用本型——那走 ``workflow.composite.CompositeDependencyError``，
    与复合 Skill 依赖缺失同一判定件（03 §3.2）。
    """
