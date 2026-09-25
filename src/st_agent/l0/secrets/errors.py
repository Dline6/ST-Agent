"""凭据子系统错误类型（02 §3；失败显式化，01 §5）。

- 凭据不存在 / 已吊销：``CredentialNotFoundError``
- 凭据标识重复：``CredentialExistsError``
- 标识 / 取值非法：``CredentialValidationError``
- 存储层损坏与打开失败沿用 ``st_agent.l0.storage`` 的
  ``StorageCorruptionError`` / ``StorageOpenError``，不另包一层。
"""

__all__ = [
    "CredentialError",
    "CredentialExistsError",
    "CredentialNotFoundError",
    "CredentialValidationError",
]


class CredentialError(Exception):
    """凭据子系统错误基类。"""


class CredentialNotFoundError(CredentialError, KeyError):
    """凭据不存在或已吊销（GWT-4 吊销后旧值不可恢复的显式载体）。"""


class CredentialExistsError(CredentialError):
    """同标识凭据已存在——更新走 ``rotate``，不得静默覆盖。"""


class CredentialValidationError(CredentialError, ValueError):
    """凭据种类 / 标识 / 取值 / 用途登记项非法。"""
