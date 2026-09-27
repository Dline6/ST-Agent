"""记忆图谱错误类型（04-L2；失败显式化）。

分级与 L1 同构：``MemoryError`` 为基类；``MemoryValidationError`` 收非法入参
与损坏记录（fail-fast，不静默补默认）；``MemoryNotFoundError`` 收寻址失败
（按 id 取不到节点）；``MemoryConflictError`` 收「同一 id 已存在」这类冲突写入
——写入路径的失败一律显式抛出，不做静默覆盖（04 §4「不存在任何静默覆盖」）。
"""

__all__ = [
    "MemoryConflictError",
    "MemoryError",
    "MemoryNotFoundError",
    "MemoryValidationError",
]


class MemoryError(Exception):
    """记忆图谱子系统错误基类。"""


class MemoryValidationError(MemoryError, ValueError):
    """节点 / 边 / 查询入参非法，或 ``memory`` 分区内的记录损坏。"""


class MemoryNotFoundError(MemoryError, KeyError):
    """按 ``memory_node_id`` 取不到节点（寻址失败）。"""


class MemoryConflictError(MemoryError):
    """写入冲突：同一 ``memory_node_id`` 已存在（新增路径不得覆盖既有节点）。"""
