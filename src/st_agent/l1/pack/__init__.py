"""官方资源包（Official Resource Pack，[01 §13](../../../docs/技术架构-v2/01-平台共享契约.md)）。

**官方 Pack＝官方资源的类型化容器**：各类官方资源以 :class:`ResourceEntry`
（``kind`` / ``version`` / ``payload``）承载，容器按 ``kind`` 分派给消费方的 loader
——使 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md) 的中性化规则库、
[04 §7](../../../docs/技术架构-v2/04-L2-记忆图谱.md) 的 Onboarding 问题清单、
[05 §8](../../../docs/技术架构-v2/05-L3-对话主入口.md) 的快捷指令种子各自
「可随官方 Pack 更新」的声明有其载体。

容器归 L1（[03 §2](../../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)）；
各消费方（L2 / L3）向容器注册 loader 属**向下依赖**（铁律 7）。`kinds` 走**开放注册**
——本包只自带 L1 自有的 ``skill`` 条目，其余 kind 由归属层在装配期贡献。
"""

from st_agent.l1.pack.container import Loader, OfficialPack, load_official_pack
from st_agent.l1.pack.errors import (
    PackError,
    PackLoaderMissingError,
    PackResourceError,
)
from st_agent.l1.pack.resources import OFFICIAL_RESOURCES, SKILL_KIND, ResourceEntry

__all__ = [
    "OFFICIAL_RESOURCES",
    "SKILL_KIND",
    "Loader",
    "OfficialPack",
    "PackError",
    "PackLoaderMissingError",
    "PackResourceError",
    "ResourceEntry",
    "load_official_pack",
]
