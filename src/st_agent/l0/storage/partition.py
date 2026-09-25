"""存储分区模型（02 §2.1 存储分区）。

七个逻辑分区，物理组织为「存储根下每分区一个目录」（④ 对齐确认；
§2.1 允许实现自定物理组织，约束是**可独立导出、独立清空**）。
"""

from __future__ import annotations

from typing import NamedTuple

__all__ = [
    "PARTITIONS",
    "PARTITION_NAMES",
    "Partition",
    "PartitionName",
    "validate_partition_name",
]

PartitionName = str
"""逻辑分区名（02 §2.1 表的 ``memory`` 等七个）。"""


class Partition(NamedTuple):
    """一个逻辑分区的注册信息（02 §2.1 表的机器可读副本）。"""

    name: PartitionName
    """分区名（= 目录名）。"""
    consumers: str
    """消费方（02 §2.1 表第 3 列，文档性字段）。"""
    secrets_isolated: bool = False
    """是否按 §2.2 单独隔离（仅 ``secrets`` 分区为 True——独立派生密钥）。"""


PARTITIONS: tuple[Partition, ...] = (
    Partition("memory", "L2"),
    Partition("config", "L1 / 各层"),
    Partition("chat_history", "L3"),
    Partition("execution_log", "L1 / L5 / L6"),
    Partition("reflection", "L6"),
    Partition("data_cache", "全部 Skill"),
    Partition("secrets", "L0 内部", secrets_isolated=True),
)

PARTITION_NAMES: tuple[str, ...] = tuple(p.name for p in PARTITIONS)


def validate_partition_name(name: str) -> PartitionName:
    """分区名合法性（防路径注入——只允许注册过的七个）。"""
    if name not in PARTITION_NAMES:
        raise ValueError(
            f"未知分区 {name!r}；合法分区 = {list(PARTITION_NAMES)}（02 §2.1）"
        )
    return name
