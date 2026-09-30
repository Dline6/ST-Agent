"""配置注册表门面的错误类型（01 §7）。

与各层既有取向一致：**显式失败、不静默降级**。门面的调用方（L3 的
:class:`~st_agent.l3.config.registry.ConfigRegistryPort` 消费端）按鸭子类型
注入，端口实现的契约错误一律在此类型下收口。
"""

from __future__ import annotations

__all__ = [
    "ConfigRegistryError",
    "RegistryFamilyConflict",
    "RegistryValidationError",
]


class ConfigRegistryError(Exception):
    """配置注册表门面的基类错误。"""


class RegistryValidationError(ConfigRegistryError):
    """命名 / 取值 / 目标不合约（01 §7）——如 ``config_id`` 形态非法、
    取值不满足参数的 ``value_schema``、或目标不在任何已注册族内。"""


class RegistryFamilyConflict(ConfigRegistryError):
    """同一 ``config_id`` 前缀被两族声明（门面按前缀唯一分派，不静默后者覆盖前者）。"""
