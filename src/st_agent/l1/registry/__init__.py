"""统一配置注册表门面（[01 §7](../../../docs/技术架构-v2/01-平台共享契约.md) 的消费面落地）。

- :mod:`~st_agent.l1.registry.naming` —— ``config_id`` 点分命名与落盘路径派生
- :mod:`~st_agent.l1.registry.family` —— 族协议与**作用域参数族**（物化 + override）
- :mod:`~st_agent.l1.registry.facade` —— :class:`ConfigRegistryFacade` 三语义
- :mod:`~st_agent.l1.registry.panel` —— 声明参数 → 面板字段（唯一实现；L3 再导出）

本包只做再导出；实现见各模块文档。
"""

from __future__ import annotations

from st_agent.l1.registry.errors import (
    ConfigRegistryError,
    RegistryFamilyConflict,
    RegistryValidationError,
)
from st_agent.l1.registry.facade import ConfigRegistryFacade
from st_agent.l1.registry.families import (
    McpHubPolicyFamily,
    ProviderHostFamily,
    RetentionFamily,
    SchedulerPolicyFamily,
)
from st_agent.l1.registry.family import (
    ConfigFamily,
    ParamFamily,
    change_prefix_for,
    declaration_for,
)
from st_agent.l1.registry.naming import (
    CHANGE_PARTITION,
    CONFIG_PARTITION,
    MAX_CONFIG_ID_LEN,
    WITHIN_SCOPE_FAMILIES,
    check_config_id,
    config_id_for_path,
    config_path,
    family_of,
    new_change_id,
    param_config_id,
    parse_param_config_id,
    target_parts,
)
from st_agent.l1.registry.panel import panel_field_for

__all__ = [
    "CHANGE_PARTITION",
    "CONFIG_PARTITION",
    "MAX_CONFIG_ID_LEN",
    "WITHIN_SCOPE_FAMILIES",
    "ConfigFamily",
    "ConfigRegistryError",
    "ConfigRegistryFacade",
    "McpHubPolicyFamily",
    "ParamFamily",
    "ProviderHostFamily",
    "RegistryFamilyConflict",
    "RegistryValidationError",
    "RetentionFamily",
    "SchedulerPolicyFamily",
    "change_prefix_for",
    "check_config_id",
    "config_id_for_path",
    "config_path",
    "declaration_for",
    "family_of",
    "new_change_id",
    "panel_field_for",
    "param_config_id",
    "parse_param_config_id",
    "target_parts",
]
