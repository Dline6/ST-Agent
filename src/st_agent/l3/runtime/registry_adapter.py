"""L3 的 01 §7 门面适配器（``investigate.*`` 族；[T-AGT-007](../../../项目管理/tasks/T-AGT-007-循环上界开放为配置项.md)）。

两条目 ``investigate.max_steps`` / ``investigate.max_llm_calls`` 都是**标量整数**，
正合统一标量落值面 ⇒ ``apply`` 委托 :class:`~st_agent.l3.runtime.bounds.LoopBounds`
的对应 ``set_*``（先例＝L6 :class:`~st_agent.l6.registry_adapter.AgentFamily` 委托
:meth:`~st_agent.l6.runtime_authorization.RuntimeAuthorization.set_tier`）。

owner 住 L3（[铁律 7](../../../项目管理/工程宪法.md)：L1 不得 import L3），故适配器住 L3 侧、
由**组合根注入** L1 的门面（``register_family``），L1 不 import L3。
"""

from __future__ import annotations

from st_agent.contracts.registry_types import ChangeRecord, ConfigEntry, ConfigScope
from st_agent.l1.registry.errors import RegistryValidationError
from st_agent.l3.errors import AgentRuntimeValidationError
from st_agent.l3.runtime.bounds import (
    INVESTIGATE_CONFIG_PREFIX,
    MAX_LLM_CALLS_CONFIG_ID,
    MAX_STEPS_CONFIG_ID,
    LoopBounds,
)

__all__ = ["InvestigateFamily", "investigate_family"]

_WRITABLE: dict[str, str] = {
    MAX_STEPS_CONFIG_ID: "set_steps",
    MAX_LLM_CALLS_CONFIG_ID: "set_llm_calls",
}


class InvestigateFamily:
    """L3 ``investigate.*`` 族的门面适配器（读面全接、写面委托 :class:`LoopBounds`）。"""

    config_prefix = INVESTIGATE_CONFIG_PREFIX
    scope: ConfigScope = "global"

    def __init__(self, bounds: LoopBounds) -> None:
        self._bounds = bounds

    def entries(self) -> tuple[ConfigEntry, ...]:
        return tuple(sorted(self._bounds.entries(), key=lambda e: e.config_id))

    def entry(self, config_id: str) -> ConfigEntry | None:
        if not config_id.startswith(self.config_prefix):
            return None
        return self._bounds.entry(config_id)

    def apply(
        self, config_id: str, value: object, *, trace_id: str | None = None
    ) -> ChangeRecord | None:
        """落一次值：委托 owner；未知条目即拒（不静默落一个半截值）。"""
        if not config_id.startswith(self.config_prefix):
            raise RegistryValidationError(f"{config_id!r} 不属本族（{self.config_prefix}*）")
        setter = _WRITABLE.get(config_id)
        if setter is None:
            known = " / ".join(entry.config_id for entry in self.entries())
            raise RegistryValidationError(f"{config_id!r} 不是本族已知条目（已知：{known}）")
        try:
            return getattr(self._bounds, setter)(value, trace_id=trace_id)
        except AgentRuntimeValidationError as exc:
            # 门面的失败词汇统一为 RegistryValidationError（消费方按一种形态分流）
            raise RegistryValidationError(str(exc)) from exc


def investigate_family(bounds: LoopBounds) -> InvestigateFamily:
    """构造 L3 ``investigate`` 族的适配器（供组合根注入 L1 门面）。"""
    return InvestigateFamily(bounds)
