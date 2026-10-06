"""L5 ``attention-budget.*`` 的 01 §7 门面适配器。

三个条目的**落值面**分两种（[01 §7](../../../docs/技术架构-v2/01-平台共享契约.md) 的
「逐条目一次落值」裁定，见 [D-081](../../../项目管理/决策日志.md)）：

- ``attention-budget.current-mode`` 是**单值枚举**，正合统一标量落值面 ⇒ 本适配器把
  ``apply`` 委托给 :meth:`AttentionBudget.switch_mode`（先例＝L1 ``scheduler-policy``）；
- ``attention-budget.slots`` 与 ``attention-budget.matrix`` 的取值是**对象** ⇒
  不在统一落值面内（把复合取值塞进去只会造出一条与 owner 校验并行的第二写路径），
  写面归 :data:`WRITE_OWNER`，``apply`` 对它们 **fail-closed 并点名**（先例＝L2 ``memory-policy``）。

L5 在 L1 之上（[铁律 7](../../../项目管理/工程宪法.md)「层间只向下依赖」），故适配器住在 L5 侧、
由组合根**注入** L1 的门面（``register_family``），L1 不 import L5。
"""

from __future__ import annotations

from st_agent.contracts.registry_types import ChangeRecord, ConfigEntry, ConfigScope
from st_agent.l1.registry.errors import RegistryValidationError
from st_agent.l5.budget import MATRIX_CONFIG_ID, MODE_CONFIG_ID, SLOTS_CONFIG_ID, AttentionBudget
from st_agent.l5.budget_store import CONFIG_PREFIX
from st_agent.l5.errors import BudgetValidationError

__all__ = ["WRITE_OWNER", "AttentionBudgetFamily", "attention_budget_family"]

WRITE_OWNER = "L5 AttentionBudget 的 set_cell / set_slots API"
"""对象类条目写面的归属（门面 ``apply`` 的 fail-closed 文案回指它）。"""

_OBJECT_CONFIG_IDS = (SLOTS_CONFIG_ID, MATRIX_CONFIG_ID)


class AttentionBudgetFamily:
    """L5 ``attention-budget.*`` 族的门面适配器（读面全接、写面按条目取值形态分流）。"""

    config_prefix = CONFIG_PREFIX
    scope: ConfigScope = "global"

    def __init__(self, budget: AttentionBudget) -> None:
        self._budget = budget

    def entries(self) -> tuple[ConfigEntry, ...]:
        return tuple(sorted(self._budget.entries(), key=lambda e: e.config_id))

    def entry(self, config_id: str) -> ConfigEntry | None:
        if not config_id.startswith(self.config_prefix):
            return None
        return self._budget.entry(config_id)

    def apply(
        self, config_id: str, value: object, *, trace_id: str | None = None
    ) -> ChangeRecord | None:
        """落一次值：情境模式委托 owner；对象类条目 fail-closed 并点名 owner。"""
        if not config_id.startswith(self.config_prefix):
            raise RegistryValidationError(
                f"{config_id!r} 不属本族（{self.config_prefix}*）"
            )
        if config_id in _OBJECT_CONFIG_IDS:
            raise RegistryValidationError(
                f"{config_id!r} 的取值为对象 / 列表，不在统一落值面内"
                f"（01 §7 逐条目一次落值）——写面归 {WRITE_OWNER}"
            )
        if config_id != MODE_CONFIG_ID:
            raise RegistryValidationError(
                f"{config_id!r} 不是本族的已知条目"
                f"（已知：{' / '.join(entry.config_id for entry in self.entries())}）"
            )
        try:
            return self._budget.switch_mode(value, trace_ref=trace_id)  # type: ignore[arg-type]
        except BudgetValidationError as exc:
            # 门面的失败词汇统一为 RegistryValidationError（消费方按一种形态分流）
            raise RegistryValidationError(str(exc)) from exc


def attention_budget_family(budget: AttentionBudget) -> AttentionBudgetFamily:
    """构造 L5 ``attention-budget`` 族的适配器（供组合根注入 L1 门面）。"""
    return AttentionBudgetFamily(budget)
