"""快捷指令种子的官方资源包接线（[01 §13](../../../docs/技术架构-v2/01-平台共享契约.md)）。

把 [05 §8](../../../docs/技术架构-v2/05-L3-对话主入口.md) 的快捷指令种子（:data:`DEFAULT_COMMANDS`）
包成官方资源条目，交给容器按 ``kind`` 分发；消费面是既有的
:meth:`CommandRegistry.register_source`（**按来源整组替换**，`T-L3-001.3` 交付的内侧）。

L3 在 L1 之上，向容器（`st_agent.l1.pack`）贡献条目与 loader 属**向下依赖**（铁律 7）。
"""

from __future__ import annotations

from st_agent.contracts.registry_types import SemVer
from st_agent.l1.pack import ResourceEntry
from st_agent.l3.commands.registry import DEFAULT_COMMANDS

__all__ = ["COMMAND_KIND", "official_command_entry"]

COMMAND_KIND = "command"
"""快捷指令种子的 kind（[01 §13](../../../docs/技术架构-v2/01-平台共享契约.md) kinds 表）。"""


def official_command_entry() -> ResourceEntry:
    """官方快捷指令种子条目（payload ＝ :data:`DEFAULT_COMMANDS`，引用非复制）。

    种子真相源仍是 :data:`DEFAULT_COMMANDS`（[`T-L3-001.3`](../../../项目管理/tasks/T-L3-001.3-快捷指令注册表.md)）；
    Pack 缺席时 `CommandRegistry()` 的构造缺省亦为它——两处同源。
    """
    return ResourceEntry(
        kind=COMMAND_KIND, version=SemVer(major=1, minor=0), payload=DEFAULT_COMMANDS,
    )
