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

from typing import Any

from st_agent.contracts.registry_types import ChangeRecord, ConfigEntry, ConfigScope
from st_agent.l1.registry.errors import RegistryValidationError
from st_agent.l5.budget import MATRIX_CONFIG_ID, MODE_CONFIG_ID, SLOTS_CONFIG_ID, AttentionBudget
from st_agent.l5.budget_store import CONFIG_PREFIX
from st_agent.l5.daily_report_store import (
    CONFIG_PREFIX as REPORT_PREFIX,
)
from st_agent.l5.daily_report_store import TIME_CONFIG_ID, TEMPLATE_CONFIG_ID
from st_agent.l5.errors import (
    BudgetValidationError,
    DailyReportValidationError,
    FatigueValidationError,
    FrequencyValidationError,
)
from st_agent.l5.fatigue_store import CONFIG_PREFIX as FATIGUE_PREFIX
from st_agent.l5.fatigue_store import THRESHOLD_CONFIG_ID
from st_agent.l5.frequency_store import CONFIG_PREFIX as FREQUENCY_PREFIX
from st_agent.l5.frequency_store import WINDOW_CONFIG_ID

__all__ = [
    "WRITE_OWNER",
    "AttentionBudgetFamily",
    "DailyReportFamily",
    "FatigueFamily",
    "FrequencyFamily",
    "attention_budget_family",
    "daily_report_family",
    "fatigue_family",
    "frequency_family",
]

WRITE_OWNER = "L5 AttentionBudget 的 set_cell / set_slots API"
"""对象类条目写面的归属（门面 ``apply`` 的 fail-closed 文案回指它）。"""

_TEMPLATE_WRITE_OWNER = "L5 DailyReportBuilder 的 set_template API"
"""日报模板条目的写面归属（取值为对象 ⇒ 不在统一标量落值面内）。"""

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


class FrequencyFamily:
    """L5 ``frequency.*`` 族：唯一条目 ``dedup-window-minutes`` 是**标量** ⇒ 写面可接。"""

    config_prefix = FREQUENCY_PREFIX
    scope: ConfigScope = "global"

    def __init__(self, frequency: Any) -> None:
        self._frequency = frequency

    def entries(self) -> tuple[ConfigEntry, ...]:
        return tuple(sorted(self._frequency.entries(), key=lambda e: e.config_id))

    def entry(self, config_id: str) -> ConfigEntry | None:
        if not config_id.startswith(self.config_prefix):
            return None
        return self._frequency.entry(config_id)

    def apply(
        self, config_id: str, value: object, *, trace_id: str | None = None
    ) -> ChangeRecord | None:
        if config_id != WINDOW_CONFIG_ID:
            raise RegistryValidationError(
                f"{config_id!r} 不是本族已知条目"
                f"（已知：{' / '.join(e.config_id for e in self.entries())}）"
                if config_id.startswith(self.config_prefix)
                else f"{config_id!r} 不属本族（{self.config_prefix}*）"
            )
        try:
            return self._frequency.set_window_minutes(value, trace_ref=trace_id)
        except FrequencyValidationError as exc:
            raise RegistryValidationError(str(exc)) from exc


class DailyReportFamily:
    """L5 ``daily-report.*`` 族：时刻是标量（写面可接）、模板是对象（fail-closed 并点名）。"""

    config_prefix = REPORT_PREFIX
    scope: ConfigScope = "global"

    def __init__(self, reports: Any) -> None:
        self._reports = reports

    def entries(self) -> tuple[ConfigEntry, ...]:
        return tuple(sorted(self._reports.entries(), key=lambda e: e.config_id))

    def entry(self, config_id: str) -> ConfigEntry | None:
        if not config_id.startswith(self.config_prefix):
            return None
        return self._reports.entry(config_id)

    def apply(
        self, config_id: str, value: object, *, trace_id: str | None = None
    ) -> ChangeRecord | None:
        if config_id == TEMPLATE_CONFIG_ID:
            raise RegistryValidationError(
                f"{config_id!r} 的取值为对象，不在统一落值面内"
                f"（01 §7 逐条目一次落值）——写面归 {_TEMPLATE_WRITE_OWNER}"
            )
        if config_id != TIME_CONFIG_ID:
            raise RegistryValidationError(
                f"{config_id!r} 不是本族已知条目"
                f"（已知：{' / '.join(e.config_id for e in self.entries())}）"
                if config_id.startswith(self.config_prefix)
                else f"{config_id!r} 不属本族（{self.config_prefix}*）"
            )
        try:
            return self._reports.set_time(value, trace_ref=trace_id)
        except DailyReportValidationError as exc:
            raise RegistryValidationError(str(exc)) from exc


class FatigueFamily:
    """L5 ``fatigue.*`` 族：唯一条目 ``ignore-threshold`` 是**标量** ⇒ 写面可接。"""

    config_prefix = FATIGUE_PREFIX
    scope: ConfigScope = "global"

    def __init__(self, fatigue: Any) -> None:
        self._fatigue = fatigue

    def entries(self) -> tuple[ConfigEntry, ...]:
        return tuple(sorted(self._fatigue.entries(), key=lambda e: e.config_id))

    def entry(self, config_id: str) -> ConfigEntry | None:
        if not config_id.startswith(self.config_prefix):
            return None
        return self._fatigue.entry(config_id)

    def apply(
        self, config_id: str, value: object, *, trace_id: str | None = None
    ) -> ChangeRecord | None:
        if config_id != THRESHOLD_CONFIG_ID:
            raise RegistryValidationError(
                f"{config_id!r} 不是本族已知条目"
                f"（已知：{' / '.join(e.config_id for e in self.entries())}）"
                if config_id.startswith(self.config_prefix)
                else f"{config_id!r} 不属本族（{self.config_prefix}*）"
            )
        try:
            return self._fatigue.set_threshold(value, trace_ref=trace_id)
        except FatigueValidationError as exc:
            raise RegistryValidationError(str(exc)) from exc


def frequency_family(frequency: Any) -> FrequencyFamily:
    """构造 L5 ``frequency`` 族的适配器（供组合根注入 L1 门面）。"""
    return FrequencyFamily(frequency)


def daily_report_family(reports: Any) -> DailyReportFamily:
    """构造 L5 ``daily-report`` 族的适配器（供组合根注入 L1 门面）。"""
    return DailyReportFamily(reports)


def fatigue_family(fatigue: Any) -> FatigueFamily:
    """构造 L5 ``fatigue`` 族的适配器（供组合根注入 L1 门面）。"""
    return FatigueFamily(fatigue)
