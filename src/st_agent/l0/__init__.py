"""L0 存储子系统（02-L0 §2）。

职责（§2.1–§2.3）：
- **存储分区**：七个逻辑分区（memory / config / chat_history / execution_log /
  reflection / data_cache / secrets），物理组织为「每分区一个目录」，分区边界
  可独立导出、独立清空
- **落盘加密**：全部分区内容 AES-256-GCM 加密；主密钥由用户主密码经
  PBKDF2-HMAC-SHA256 派生（§2.2「业界验证的标准构造」；迭代次数 600_000，
  OWSASP 2023 推荐，实现阶段安全评审可调）；``secrets`` 分区单独隔离
  （独立派生密钥），密钥材料不出现在其他分区
- **损坏恢复**：每分区维护加密清单（文件列表 + 校验和），打开时逐分区校验，
  损坏定位到分区级 → 从最近备份恢复 / 无备份进入冷启动（§2.3）

边界：
- 产品方零接触：不设任何由产品方持有的恢复通道；主密码丢失 = 数据不可恢复
- 备份归档格式与「完全清空」在 T-L0-006；data_cache 的库表内容在 T-L0-005
  （本模块只交付分区容器——SQLite 单文件作为 opaque blob 加密存取）
"""

from st_agent.l0.storage import (
    CorruptedPartitionReport,
    CryptoError,
    PARTITIONS,
    Partition,
    PartitionName,
    StorageCorruptionError,
    StorageOpenError,
    StorageState,
    Store,
    StoreCorruptionReport,
)
from st_agent.l0.storage.crypto import (
    MASTER_KDF_ITERATIONS,
    derive_master_key,
    derive_partition_key,
    seal_bytes,
    open_bytes,
)
from st_agent.l0.storage.manifest import ManifestEntry, PartitionManifest

__all__ = [
    "CorruptedPartitionReport",
    "CryptoError",
    "MASTER_KDF_ITERATIONS",
    "ManifestEntry",
    "PARTITIONS",
    "Partition",
    "PartitionManifest",
    "PartitionName",
    "StorageCorruptionError",
    "StorageOpenError",
    "StorageState",
    "Store",
    "StoreCorruptionReport",
    "derive_master_key",
    "derive_partition_key",
    "open_bytes",
    "seal_bytes",
]
