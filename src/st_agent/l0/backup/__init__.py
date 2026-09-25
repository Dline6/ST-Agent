"""L0 备份 / 恢复 / 完全清空 / 留存（02 §8–§9）。

职责：
- **备份归档**：一键导出四分区（+可选 data_cache）为单个加密归档
- **恢复**：任意设备导入 + 密码 → 校验后覆盖还原
- **完全清空**：三次确认后删除存储根整目录
- **留存策略**：chat_history / execution_log 清理周期可配（01 §7 注册表）

边界：
- ``secrets`` / ``execution_log`` 永不进归档（02 §8.1 分区表）
- 全程禁网络（GWT-5 零接触）；凭证明文仅驻内存
"""

from st_agent.l0.backup.backup import (
    BACKUP_AAD,
    BACKUP_FORMAT_TAG,
    RETENTION_SWEEP_PARTITIONS,
    WIPE_CONFIRM_TOKENS,
    RetentionSweepReport,
    apply_retention,
    create_backup,
    get_policy,
    restore_backup,
    set_policy,
    wipe_all,
)
from st_agent.l0.backup.errors import (
    BackupAuthError,
    BackupCorruptedError,
    BackupError,
    BackupVersionError,
    RetentionError,
    WipeConfirmationError,
)
from st_agent.l0.backup.models import (
    BACKUP_FORMAT_VERSION,
    BACKUP_PARTITIONS,
    DEFAULT_CHAT_HISTORY_DAYS,
    DEFAULT_EXECUTION_LOG_DAYS,
    POLICY_PATH,
    RETAINABLE_PARTITIONS,
    BackupFileEntry,
    BackupManifest,
    RestoreReport,
    RetentionPolicy,
    WipeReport,
    retention_config_entries,
)

__all__ = [
    "BACKUP_AAD",
    "BACKUP_FORMAT_TAG",
    "BACKUP_FORMAT_VERSION",
    "BACKUP_PARTITIONS",
    "DEFAULT_CHAT_HISTORY_DAYS",
    "DEFAULT_EXECUTION_LOG_DAYS",
    "POLICY_PATH",
    "RETAINABLE_PARTITIONS",
    "RETENTION_SWEEP_PARTITIONS",
    "WIPE_CONFIRM_TOKENS",
    "BackupAuthError",
    "BackupCorruptedError",
    "BackupError",
    "BackupFileEntry",
    "BackupManifest",
    "BackupVersionError",
    "RestoreReport",
    "RetentionError",
    "RetentionPolicy",
    "RetentionSweepReport",
    "WipeConfirmationError",
    "WipeReport",
    "apply_retention",
    "create_backup",
    "get_policy",
    "restore_backup",
    "retention_config_entries",
    "set_policy",
    "wipe_all",
]
