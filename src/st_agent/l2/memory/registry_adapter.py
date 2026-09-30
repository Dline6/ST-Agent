"""L2 ``memory-policy/*`` 的**只读**门面适配器（[01 §7](../../../docs/技术架构-v2/01-平台共享契约.md)）。

L2 记忆策略条目（[04 §4–§5](../../../docs/技术架构-v2/04-L2-记忆图谱.md)）的
**取值为对象 / 列表**（置信度基线是三元数值对象、写入白名单与 Onboarding 题集是
列表），而统一门面的落值语义是「**逐条目一次落值**」（01 §7）——把复合取值塞进
统一标量落值面只会造出一条与 owner 校验并行的第二写路径。故本适配器**只接读面**
（``entries`` / ``entry``），写面归各 owner 的 ``set_*`` API；门面 ``apply``
对该族一律 fail-closed 并点名 owner。

L2 在 L1 之上（[铁律 7](../../../项目管理/工程宪法.md)「层间只向下依赖」），故适配器
住在 L2 侧、由组合根**注入** L1 的门面（``register_family``），L1 不 import L2。

:param store: ``Store`` 句柄
:param declarations: ``() -> Sequence[ConfigEntry]``——各 owner 的**当前**声明形态
    （如 ``ConfidenceModel.baseline_entry`` / ``WritePolicy.entry``）；本适配器
    在其上叠加 ``MemoryPolicyStore`` 的当前取值。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from st_agent.contracts.registry_types import ChangeRecord, ConfigEntry, ConfigScope
from st_agent.l1.registry.errors import RegistryValidationError
from st_agent.l2.memory.config_store import MemoryPolicyStore

__all__ = ["WRITE_OWNER", "MemoryPolicyFamily", "memory_policy_family"]

WRITE_OWNER = "L2 各 owner 的 set_* API（ConfidenceModel / WritePolicy / Onboarding）"
"""本族条目写面的归属（门面 ``apply`` 的 fail-closed 文案回指它）。"""


class MemoryPolicyFamily:
    """L2 ``memory-policy/*`` 族的**只读**适配器。"""

    config_prefix = "memory-policy/"
    scope: ConfigScope = "global"

    def __init__(
        self,
        store,
        *,
        declarations: Callable[[], Sequence[ConfigEntry]],
    ) -> None:
        self._book = MemoryPolicyStore(store)
        self._declarations = declarations

    def entries(self) -> tuple[ConfigEntry, ...]:
        return tuple(
            sorted((self._overlay(e) for e in self._declarations()),
                   key=lambda e: e.config_id)
        )

    def entry(self, config_id: str) -> ConfigEntry | None:
        if not config_id.startswith(self.config_prefix):
            return None
        for entry in self._declarations():
            if entry.config_id == config_id:
                return self._overlay(entry)
        return None

    def apply(
        self, config_id: str, value: object, *, trace_id: str | None = None
    ) -> ChangeRecord | None:
        if not config_id.startswith(self.config_prefix):
            raise RegistryValidationError(f"{config_id!r} 不属本族（{self.config_prefix}*）")
        raise RegistryValidationError(
            f"{config_id!r} 的取值为对象 / 列表，不在统一落值面内"
            f"（01 §7 逐条目一次落值）——写面归 {WRITE_OWNER}"
        )

    def _overlay(self, entry: ConfigEntry) -> ConfigEntry:
        current = self._book.current(entry.config_id, entry.default)
        return entry.model_copy(update={"default": current})


def memory_policy_family(
    store, *, declarations: Callable[[], Sequence[ConfigEntry]]
) -> MemoryPolicyFamily:
    """构造 L2 ``memory-policy`` 族的只读适配器（供组合根注入 L1 门面）。"""
    return MemoryPolicyFamily(store, declarations=declarations)
