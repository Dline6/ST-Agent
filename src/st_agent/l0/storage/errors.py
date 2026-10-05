"""L0 存储子系统错误类型。

- 打开存储的统一失败：``StorageOpenError``（密码错 / 结构坏）
- 「给口令就能过」的显式载体：``StoragePassphraseRequired``（打开 / 解锁需要主密码或凭据口令）
- 损坏检测的显式载体：``StorageCorruptionError``（分区级定位，02 §2.3）
- 加密原语失败：``CryptoError``（GCM 认证失败等）
- 惰性解锁未就绪：``StorageSecretsLockedError``（明文模式下 `secrets` 未解锁，02 §2.2）
"""

from __future__ import annotations

__all__ = [
    "CryptoError",
    "StorageCorruptionError",
    "StorageOpenError",
    "StoragePassphraseRequired",
    "StorageSecretsLockedError",
]


class StorageError(Exception):
    """L0 存储子系统错误基类。"""


class CryptoError(StorageError):
    """加密原语失败（错密钥 / GCM 认证失败 / 密文格式非法）。"""


class StorageOpenError(StorageError):
    """打开存储失败（主密码错误 / 存储结构损坏）。"""


class StoragePassphraseRequired(StorageOpenError):
    """打开 / 解锁需要用户口令（[02 §2.2 / §3]）——「**给口令就能过**」的显式载体。

    与其余 :class:`StorageOpenError`（keyfile 损坏 / 格式标记非法 / 凭据分区结构异常）分开
    的意义即**用途**：调用方**只对**本类做「索取口令 → 重试」，其余一律 fail fast——把
    「重试必然失败」的打开失败也重试三遍只会拖延报错。

    ``kind`` 是机器可读的口令种类（人可读原因在消息里，两者不混）：

    - ``"main_passphrase"``：**加密根的主密码**（02 §2.2）
    - ``"credentials"``：**明文根的凭据口令**（凭据恒加密、惰性解锁 / TOFU，02 §2.2 / §3）

    子类关系保证既有 ``except StorageOpenError`` 的调用方行为不变。
    """

    def __init__(self, message: str, *, kind: str) -> None:
        self.kind = kind
        super().__init__(message)


class StorageSecretsLockedError(StorageError):
    """`secrets` 分区未解锁（明文模式的惰性解锁口径，02 §2.2）。

    凭据恒加密：明文根对其余分区免口令，访问 `secrets` 时才要口令。本异常是
    「尚未给出凭据口令」的**显式**载体——调用方须 `Store.unlock_secrets(口令)`，
    不得把未解锁当作空分区继续。
    """


class StorageCorruptionError(StorageError):
    """分区损坏（02 §2.3）：损坏范围定位到分区级。

    ``report`` 携带逐分区损坏明细，其余分区不受影响——调用方决定
    从备份恢复（T-L0-006）或冷启动重建。
    """

    def __init__(self, report) -> None:
        self.report = report
        parts = ", ".join(c.partition for c in report.corrupted)
        super().__init__(
            f"存储损坏：分区 [{parts}] 校验失败（{len(report.corrupted)} 个），"
            "其余分区可用；请从最近备份恢复或对损坏分区执行冷启动重建"
        )
