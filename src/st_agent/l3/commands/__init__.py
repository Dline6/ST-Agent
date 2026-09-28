"""L3 快捷指令包（[05 §8](../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

对外只经本模块取用。
"""

from __future__ import annotations

from st_agent.l3.commands.registry import (
    COMMAND_PREFIX,
    DEFAULT_COMMANDS,
    INTENT_KINDS,
    SOURCES,
    CommandRegistry,
    CommandResolution,
    IntentKind,
    ParamValue,
    QuickCommand,
)

__all__ = [
    "COMMAND_PREFIX",
    "DEFAULT_COMMANDS",
    "INTENT_KINDS",
    "SOURCES",
    "CommandRegistry",
    "CommandResolution",
    "IntentKind",
    "ParamValue",
    "QuickCommand",
]
