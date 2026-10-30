"""本地命令执行面（T-AGT-016）。

- :mod:`command`——一次性 shell 执行、超时终止、输出上限与执行留痕。
- :mod:`exec_config`——``local-exec/enabled`` 条目（缺省关）与读写面。

口径见 [01 §10](../../../docs/技术架构-v2/01-平台共享契约.md) 与
[02 §7](../../../docs/技术架构-v2/02-L0-本地优先基座.md)。
"""

from __future__ import annotations

from st_agent.l0.exec.command import (
    COMMAND_LOG_PREFIX,
    DEFAULT_MAX_OUTPUT_CHARS,
    DEFAULT_TIMEOUT_S,
    MAX_OUTPUT_CHARS,
    MAX_TIMEOUT_S,
    CommandResult,
    CommandRunner,
    spawn_shell,
)
from st_agent.l0.exec.errors import CommandConfigError, CommandError
from st_agent.l0.exec.exec_config import (
    DEFAULT_LOCAL_EXEC_ENABLED,
    LOCAL_EXEC_DISCLOSURE,
    LOCAL_EXEC_ENABLED_CONFIG_ID,
    LocalExecSettings,
    exec_config_entries,
    get_local_exec_enabled,
    load_local_exec_settings,
    set_local_exec_enabled,
)

__all__ = [
    "COMMAND_LOG_PREFIX",
    "DEFAULT_LOCAL_EXEC_ENABLED",
    "DEFAULT_MAX_OUTPUT_CHARS",
    "DEFAULT_TIMEOUT_S",
    "LOCAL_EXEC_DISCLOSURE",
    "LOCAL_EXEC_ENABLED_CONFIG_ID",
    "MAX_OUTPUT_CHARS",
    "MAX_TIMEOUT_S",
    "CommandConfigError",
    "CommandError",
    "CommandResult",
    "CommandRunner",
    "LocalExecSettings",
    "exec_config_entries",
    "get_local_exec_enabled",
    "load_local_exec_settings",
    "set_local_exec_enabled",
    "spawn_shell",
]
