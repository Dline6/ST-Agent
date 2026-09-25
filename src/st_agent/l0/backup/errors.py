"""L0 备份 / 恢复 / 完全清空 / 留存错误类型（02 §8–§9）。

- 打开归档失败统一为 ``BackupError`` 子类（01 §5 失败显式化）
- 恢复前校验失败不写脏数据：校验在写入之前全部完成
"""

__all__ = [
    "BackupAuthError",
    "BackupCorruptedError",
    "BackupError",
    "BackupVersionError",
    "RetentionError",
    "WipeConfirmationError",
]


class BackupError(Exception):
    """备份子系统错误基类。"""


class BackupAuthError(BackupError):
    """归档密码错误（校验锚认证失败；现有数据不被破坏）。"""


class BackupCorruptedError(BackupError):
    """归档损坏（格式非法 / 解密失败 / 校验和不符；目标存储不写脏数据）。"""


class BackupVersionError(BackupError):
    """归档版本不兼容（主版本不一致，拒绝恢复）。"""


class RetentionError(BackupError):
    """留存策略错误（未知分区 / 非法天数 / 对禁清分区请求清理）。"""


class WipeConfirmationError(BackupError):
    """完全清空确认不足（未集齐三次确认，拒绝执行）。"""
