"""L6 的 01 §7 门面适配器（``weekly-report.*`` 与 ``proposal.*`` 两族）。

**周报族**四个条目的**落值面**分两种（[01 §7](../../../docs/技术架构-v2/01-平台共享契约.md)
的「逐条目一次落值」裁定，见 [D-085](../../../项目管理/决策日志.md)）：

- ``weekly-report.day-of-week`` / ``.time`` / ``.min-feedback`` 是**标量**，正合统一标量
  落值面 ⇒ 本适配器把 ``apply`` 委托给 :class:`WeeklyReportBuilder` 的对应 ``set_*``
  （先例＝L5 ``daily-report.time`` / ``fatigue.ignore-threshold``）；
- ``weekly-report.template`` 的取值是**对象**（四段开关 + 订阅渠道链）⇒ 不在统一落值面内，
  ``apply`` 对它 **fail-closed 并点名**（先例＝L5 ``daily-report.template``）。

**提案族**（[08 §4](../../../docs/技术架构-v2/08-L6-反思演进.md)，[`T-L6-002.2`](../../../项目管理/tasks/T-L6-002.2-主动提案与周报候选生成.md)
交付）两个条目 ``proposal.repeat-threshold`` / ``proposal.rate-limit`` 都是标量，
``apply`` 一律委托 :class:`ProposalEngine` 的对应 ``set_*``。

L6 在 L1 之上（[铁律 7](../../../项目管理/工程宪法.md)「层间只向下依赖」），故适配器住在 L6 侧、
由组合根**注入** L1 的门面（``register_family``），L1 不 import L6。
"""

from __future__ import annotations

from typing import Any

from st_agent.contracts.registry_types import ChangeRecord, ConfigEntry, ConfigScope
from st_agent.l1.registry.errors import RegistryValidationError
from st_agent.l6.errors import ProposalError, WeeklyReportError
from st_agent.l6.proposal import ProposalEngine
from st_agent.l6.proposal_store import (
    CONFIG_PREFIX as PROPOSAL_CONFIG_PREFIX,
    RATE_LIMIT_CONFIG_ID,
    REPEAT_CONFIG_ID,
)
from st_agent.l6.weekly import WeeklyReportBuilder
from st_agent.l6.weekly_store import (
    CONFIG_PREFIX,
    DAY_CONFIG_ID,
    MIN_FEEDBACK_CONFIG_ID,
    TEMPLATE_CONFIG_ID,
    TIME_CONFIG_ID,
)

__all__ = [
    "TEMPLATE_WRITE_OWNER",
    "ProposalFamily",
    "WeeklyReportFamily",
    "proposal_family",
    "weekly_report_family",
]

TEMPLATE_WRITE_OWNER = "L6 WeeklyReportBuilder 的 set_template API"
"""对象类条目写面的归属（门面 ``apply`` 的 fail-closed 文案回指它）。"""

_WRITABLE = {
    DAY_CONFIG_ID: "set_day_of_week",
    TIME_CONFIG_ID: "set_time",
    MIN_FEEDBACK_CONFIG_ID: "set_min_feedback",
}


class WeeklyReportFamily:
    """L6 ``weekly-report.*`` 族的门面适配器（读面全接、写面按条目取值形态分流）。"""

    config_prefix = CONFIG_PREFIX
    scope: ConfigScope = "global"

    def __init__(self, reports: WeeklyReportBuilder) -> None:
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
        """落一次值：标量条目委托 owner；对象类条目 fail-closed 并点名 owner。"""
        if not config_id.startswith(self.config_prefix):
            raise RegistryValidationError(f"{config_id!r} 不属本族（{self.config_prefix}*）")
        if config_id == TEMPLATE_CONFIG_ID:
            raise RegistryValidationError(
                f"{config_id!r} 的取值为对象，不在统一标量落值面内"
                f"（01 §7 逐条目一次落值）——写面归 {TEMPLATE_WRITE_OWNER}"
            )
        setter = _WRITABLE.get(config_id)
        if setter is None:
            known = " / ".join(entry.config_id for entry in self.entries())
            raise RegistryValidationError(f"{config_id!r} 不是本族已知条目（已知：{known}）")
        try:
            return getattr(self._reports, setter)(value, trace_ref=trace_id)
        except WeeklyReportError as exc:
            # 门面的失败词汇统一为 RegistryValidationError（消费方按一种形态分流）
            raise RegistryValidationError(str(exc)) from exc


def weekly_report_family(reports: WeeklyReportBuilder) -> WeeklyReportFamily:
    """构造 L6 ``weekly-report`` 族的适配器（供组合根注入 L1 门面）。"""
    return WeeklyReportFamily(reports)


_PROPOSAL_WRITABLE = {
    REPEAT_CONFIG_ID: "set_repeat_threshold",
    RATE_LIMIT_CONFIG_ID: "set_rate_limit",
}


class ProposalFamily:
    """L6 ``proposal.*`` 族的门面适配器（读面全接、写面按条目取值形态分流）。

    两个条目都是**标量**（正整数 / 非负整数），正合统一标量落值面 ⇒ ``apply`` 委托
    :class:`ProposalEngine` 的对应 ``set_*``（形态同 :class:`WeeklyReportFamily`）。
    """

    config_prefix = PROPOSAL_CONFIG_PREFIX
    scope: ConfigScope = "global"

    def __init__(self, proposals: ProposalEngine) -> None:
        self._proposals = proposals

    def entries(self) -> tuple[ConfigEntry, ...]:
        return tuple(sorted(self._proposals.entries(), key=lambda e: e.config_id))

    def entry(self, config_id: str) -> ConfigEntry | None:
        if not config_id.startswith(self.config_prefix):
            return None
        return self._proposals.entry(config_id)

    def apply(
        self, config_id: str, value: object, *, trace_id: str | None = None
    ) -> ChangeRecord | None:
        """落一次值：标量条目委托 owner；未知条目即拒（不静默落一个半截值）。"""
        if not config_id.startswith(self.config_prefix):
            raise RegistryValidationError(f"{config_id!r} 不属本族（{self.config_prefix}*）")
        setter = _PROPOSAL_WRITABLE.get(config_id)
        if setter is None:
            known = " / ".join(entry.config_id for entry in self.entries())
            raise RegistryValidationError(f"{config_id!r} 不是本族已知条目（已知：{known}）")
        try:
            return getattr(self._proposals, setter)(value, trace_ref=trace_id)
        except ProposalError as exc:
            raise RegistryValidationError(str(exc)) from exc


def proposal_family(proposals: ProposalEngine) -> ProposalFamily:
    """构造 L6 ``proposal`` 族的适配器（供组合根注入 L1 门面）。"""
    return ProposalFamily(proposals)
