"""官方资源包的条目模型与 L1 自有的官方资源（01 §13）。

[01 §13](../../../docs/技术架构-v2/01-平台共享契约.md) 定义：**官方 Pack＝官方资源的
类型化容器**，各类资源以 :class:`ResourceEntry`（``kind`` / ``version`` / ``payload``）
承载。容器只定「装载 / 分发」面，**不解读** ``payload`` 的形态——形态归该 kind 的
既有出处（``skill`` → [01 §2](../../../docs/技术架构-v2/01-平台共享契约.md)，
``rulepack`` → [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md)，
``onboarding_questions`` → [01 §7](../../../docs/技术架构-v2/01-平台共享契约.md)，
``command`` → [05 §8](../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

**kinds 走开放注册**（父任务假设 `A1`）：本模块只提供 L1 **自有**的官方资源
（``skill``——引用 :data:`OFFICIAL_PACK`，不搬运、不复制）；其余 kind 的条目由
其**归属层**在装配期贡献（L2 / L3 向下 import 本模块属向下依赖，铁律 7）。
"""

from __future__ import annotations

from dataclasses import dataclass

from st_agent.contracts.registry_types import SemVer
from st_agent.l1.pack.errors import PackResourceError
from st_agent.l1.skills.pack import OFFICIAL_PACK

__all__ = [
    "OFFICIAL_RESOURCES",
    "SKILL_KIND",
    "ResourceEntry",
]

SKILL_KIND = "skill"
"""Skill 描述体的 kind（[01 §13](../../../docs/技术架构-v2/01-平台共享契约.md) kinds 表）。"""


@dataclass(frozen=True)
class ResourceEntry:
    """一条类型化官方资源条目（[01 §13](../../../docs/技术架构-v2/01-平台共享契约.md)）。

    ``payload`` 原样承载——**容器不解读**其形态（形态归该 kind 的既有出处）。

    :attr:`key` ＝ ``(kind, version)``：容器的幂等面按它判「同一条目」（[01 §13]：
    同 kind 同 version 重复装载**不重复、不静默覆盖**）。
    """

    kind: str
    version: SemVer
    payload: object

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not self.kind:
            raise PackResourceError(f"资源条目的 kind 须为非空字符串，得到 {self.kind!r}")
        if not isinstance(self.version, SemVer):
            raise PackResourceError(
                f"资源条目的 version 须为 SemVer（01 §9 语义），得到 {type(self.version).__name__}"
            )

    @property
    def key(self) -> tuple[str, SemVer]:
        """幂等判据：``(kind, version)``。"""
        return (self.kind, self.version)


OFFICIAL_RESOURCES: tuple[ResourceEntry, ...] = (
    ResourceEntry(kind=SKILL_KIND, version=SemVer(major=1, minor=0), payload=OFFICIAL_PACK),
)
"""L1 **自有**的官方资源条目。

现只有 ``skill`` 一类（引用 [03 §2](../../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)
的 :data:`OFFICIAL_PACK`，不复制）。``rulepack`` / ``onboarding_questions`` / ``command``
三类由各自归属层在装配期贡献——L1 不 import L2 / L3（铁律 7）。
"""
