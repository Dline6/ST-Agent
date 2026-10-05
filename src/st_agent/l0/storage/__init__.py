"""L0 存储子系统。

02-L0 §2 的实现：分区注册（partition）、加密原语（crypto）、分区清单
（manifest）、格式标记（format）、门面 Store（store）。对外统一从
``st_agent.l0.storage`` import。
"""

from st_agent.l0.storage.errors import (
    CryptoError,
    StorageCorruptionError,
    StorageOpenError,
    StoragePassphraseRequired,
    StorageSecretsLockedError,
)
from st_agent.l0.storage.format import (
    MODE_ENCRYPTED,
    MODE_PLAIN,
    STORE_FORMAT,
    STORE_MARKER,
    StoreMode,
    read_mode,
    write_marker,
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
    "MODE_ENCRYPTED",
    "MODE_PLAIN",
    "STORE_FORMAT",
    "STORE_MARKER",
    "CryptoError",
    "CorruptedPartitionReport",
    "PARTITIONS",
    "PARTITION_NAMES",
    "Partition",
    "PartitionName",
    "StorageCorruptionError",
    "StorageOpenError",
    "StoragePassphraseRequired",
    "StorageSecretsLockedError",
    "StorageState",
    "Store",
    "StoreCorruptionReport",
    "StoreMode",
    "read_mode",
    "validate_partition_name",
    "write_marker",
]
