"""L0 存储子系统。

02-L0 §2 的实现：分区注册（partition）、加密原语（crypto）、分区清单
（manifest）、门面 Store（store）。对外统一从 ``st_agent.l0.storage``
import。
"""

from st_agent.l0.storage.errors import (
    CryptoError,
    StorageCorruptionError,
    StorageOpenError,
)
from st_agent.l0.storage.partition import (
    PARTITIONS,
    PARTITION_NAMES,
    Partition,
    PartitionName,
    validate_partition_name,
)
from st_agent.l0.storage.store import (
    CorruptedPartitionReport,
    StorageState,
    Store,
    StoreCorruptionReport,
)

__all__ = [
    "CryptoError",
    "CorruptedPartitionReport",
    "PARTITIONS",
    "PARTITION_NAMES",
    "Partition",
    "PartitionName",
    "StorageCorruptionError",
    "StorageOpenError",
    "StorageState",
    "Store",
    "StoreCorruptionReport",
    "validate_partition_name",
]
