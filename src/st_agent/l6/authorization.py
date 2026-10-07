"""L6 演进授权档位与风险分级清单（[08 §5](../../../docs/技术架构-v2/08-L6-反思演进.md) 前半）。

[08 §5](../../../docs/技术架构-v2/08-L6-反思演进.md) 规定三档授权（`manual` / `collaborative`
/ `autonomous`，**配置注册表项、切换留痕**）与「小步优化」的边界（**风险分级清单**：哪些
参数可自主改、哪些必须协作、哪些永不可自主）。本模块把两者落成 [01 §7](../../../docs/技术架构-v2/01-平台共享契约.md)
的**登记条目**，并把它们的**判据**以鸭子面 ``permits(scope) -> bool`` 对外——[08 §4](../../../docs/技术架构-v2/08-L6-反思演进.md)
的 A/B 实验启用门（[`ExperimentConsole`](experiment.py)）正是该面的消费方。

四条口径：

- **档位与清单都是 01 §7 条目**——档位 `evolution.authorization` 是标量枚举，走统一标量落值面；
  风险分级清单 `evolution.risk-grading` 取值是**对象** ⇒ **不在统一标量落值面内**，写面即
  :meth:`EvolutionAuthorization.set_grading`（先例＝`weekly-report.template`）。
- **清单是白名单而非黑名单**——**未分类的条目按保守处理**（视为必须协作），
  **不静默放行**；[08 §5](../../../docs/技术架构-v2/08-L6-反思演进.md) 的三类语义逐条落为
  :data:`RiskClass`。
- **`never-autonomous` 恒不妨行**——Memory 内容与策略在任何档位下都**不自动变更**（[08 §7](../../../docs/技术架构-v2/08-L6-反思演进.md) 红线）。
- **读不到即 fail-closed**——档位条目缺失 ⇒ 缺省档 `collaborative`（本就不自动）；
  **损坏 ⇒ 一律不放行**并给出原因，**不臆测为 `autonomous`**。

本模块**不做**变更流（提案 → 生效 → 回滚）——那是 [`change.py`](change.py)；也不做实验的
启停结算（那是 [`experiment.py`](experiment.py)，本模块只回答「该范围在当前档位下可否**自动**启用」）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from st_agent.contracts.registry_types import (
    ChangePolicy,
    ChangeRecord,
    ConfigEntry,
    PanelField,
)
from st_agent.l6.authorization_store import (
    GRADING_CONFIG_ID,
    TIER_CONFIG_ID,
    EvolutionStore,
)
from st_agent.l6.errors import AuthorizationError

__all__ = [
    "DEFAULT_RISK_GRADING",
    "DEFAULT_SCOPES",
    "DEFAULT_TIER",
    "GRADING_CONFIG_ID",
    "RISK_CLASSES",
    "TIER_CONFIG_ID",
    "TIERS",
    "EvolutionAuthorization",
    "EvolutionRiskRule",
    "PermitDecision",
    "RiskClass",
    "Tier",
]

Tier = Literal["manual", "collaborative", "autonomous"]
TIERS: tuple[Tier, ...] = ("manual", "collaborative", "autonomous")

DEFAULT_TIER: Tier = "collaborative"
"""缺省档位（[08 §5](../../../docs/技术架构-v2/08-L6-反思演进.md) 原文「协作模式（默认档）」）。"""

RiskClass = Literal["autonomous-ok", "collaborative-required", "never-autonomous"]
RISK_CLASSES: tuple[RiskClass, ...] = (
    "autonomous-ok", "collaborative-required", "never-autonomous",
)

DEFAULT_SCOPES: tuple[str, ...] = ("delivery-strategy", "lens-composition")
"""实验的低风险范围（与 [`LOW_RISK_SCOPES`](experiment.py) 同集；构造口可覆写以便测试隔离）。"""


class EvolutionRiskRule(BaseModel):
    """风险分级清单的一条**声明式规则**（`config_id` 前缀 → 风险类）。"""

    model_config = ConfigDict(frozen=True)

    prefix: str = Field(min_length=1)
    """`config_id` 前缀（点分族前缀或 `族/` 前缀，如 `skill.` / `memory-policy/`）。"""
    risk_class: RiskClass


DEFAULT_RISK_GRADING: tuple[EvolutionRiskRule, ...] = (
    # 永不可自主——Memory 内容与策略（[08 §7](../../../docs/技术架构-v2/08-L6-反思演进.md) 红线），任何档位下都不自动变更
    EvolutionRiskRule(prefix="memory-policy/", risk_class="never-autonomous"),
    EvolutionRiskRule(prefix="memory.", risk_class="never-autonomous"),
    # 可自主——表现层参数：[08 §5](../../../docs/技术架构-v2/08-L6-反思演进.md) 原文「仅推送文案风格、报告结构等表现层参数可自主」
    EvolutionRiskRule(prefix="daily-report.", risk_class="autonomous-ok"),
    EvolutionRiskRule(prefix="weekly-report.", risk_class="autonomous-ok"),
    EvolutionRiskRule(prefix="channel-delivery.", risk_class="autonomous-ok"),
    # 必须协作——[08 §5](../../../docs/技术架构-v2/08-L6-反思演进.md) 原文「Skill 参数、注意力预算、视角阵容必须协作」
    EvolutionRiskRule(prefix="skill.", risk_class="collaborative-required"),
    EvolutionRiskRule(prefix="workflow.", risk_class="collaborative-required"),
    EvolutionRiskRule(prefix="attention-budget.", risk_class="collaborative-required"),
    EvolutionRiskRule(prefix="lens.", risk_class="collaborative-required"),
)


class PermitDecision(BaseModel):
    """一次「可否**自动**放行」的结论（**不自动也显式**——原因随行，不静默）。"""

    model_config = ConfigDict(frozen=True)

    permitted: bool
    reason: str = ""
    tier: Tier = DEFAULT_TIER


def _system_now() -> datetime:
    """用户本地时区当前时刻（[01 §8](../../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    return datetime.now().astimezone()


class EvolutionAuthorization:
    """演进授权档位与风险分级清单面（读面 + 切换留痕 + 判据）。

    :param store: ``Store`` 句柄；缺省 ``None`` ＝**纯内存态**
    :param registry: 01 §7 的 [`ConfigRegistryFacade`](../l1/registry/facade.py)
        （鸭子类型 ``register_family``）；缺省 ``None`` ⇒ 条目**不登记**（读面仍可用）
    :param scopes: 实验的低风险范围（缺省 :data:`DEFAULT_SCOPES`）
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    def __init__(
        self,
        *,
        store: Any = None,
        registry: Any = None,
        grading: Sequence[EvolutionRiskRule] | None = None,
        scopes: Sequence[str] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = EvolutionStore(store, now=now) if store is not None else None
        self._registry = registry
        self._now = _system_now if now is None else now
        self._default_tier: Tier = DEFAULT_TIER
        self._grading: tuple[EvolutionRiskRule, ...] = tuple(
            _require_rules(DEFAULT_RISK_GRADING if grading is None else grading)
        )
        self._scopes: tuple[str, ...] = tuple(
            DEFAULT_SCOPES if scopes is None else scopes
        )
        self._memory: dict[str, Any] = {}     # 纯内存态（无 Store 时）

    # ───────────────────────── 档位 ─────────────────────────

    def tier(self) -> Tier:
        """当前授权档位（条目缺失 ⇒ 缺省 `collaborative`；**损坏 ⇒ 抛**，不静默回落）。"""
        if self._store is None:
            return self._default_tier
        raw = self._store.current(TIER_CONFIG_ID, self._default_tier)
        return _require_tier(raw)

    def set_tier(self, value: Any, *, trace_ref: str | None = None) -> ChangeRecord | None:
        """切换档位（**留痕**；取值未变 ⇒ 不落盘、不留痕）。

        旧值取该条目的**生效取值**（未落过值时为缺省档 `collaborative`）——故「切到缺省档」
        在从未落值时**不留痕**（[01 §7](../../../docs/技术架构-v2/01-平台共享契约.md) 的「取值不变不留痕」）。

        :raises AuthorizationError: 不在 :data:`TIERS` 内
        """
        target = _require_tier(value)
        self._default_tier = target
        if self._store is None:
            return None
        return self._store.set(
            self._entry(TIER_CONFIG_ID, target),
            previous=self._store.current(TIER_CONFIG_ID, DEFAULT_TIER), trace_ref=trace_ref,
        )

    # ───────────────────────── 风险分级清单 ─────────────────────────

    def grading(self) -> tuple[EvolutionRiskRule, ...]:
        """当前风险分级清单（**按声明顺序**，首个命中的前缀即该条目的风险类）。"""
        if self._store is None:
            return self._grading
        raw = self._store.current(GRADING_CONFIG_ID, None)
        if raw is None:
            return self._grading
        return _rules_from_value(raw)

    def set_grading(
        self, rules: Sequence[EvolutionRiskRule | Mapping[str, Any]], *,
        trace_ref: str | None = None,
    ) -> ChangeRecord | None:
        """替换风险分级清单（取值是对象 ⇒ **写面即本方法**；越界即拒、不落盘不留痕）。"""
        target = _require_rules(rules)
        if self._store is None:
            self._grading = target
            return None
        # 旧值须在更新内存态**之前**取（否则「未变」判定会拿新值比自己，静默不落盘）
        previous = self._store.current(GRADING_CONFIG_ID, _rules_payload(self._grading))
        self._grading = target
        return self._store.set(
            self._entry(GRADING_CONFIG_ID, _rules_payload(target)),
            previous=previous, trace_ref=trace_ref,
        )

    def classify(self, config_id: str) -> RiskClass:
        """一个 `config_id` 的风险类（**未命中任何规则 ⇒ 保守**：必须协作）。"""
        text = (config_id or "").strip()
        for rule in self.grading():
            if text.startswith(rule.prefix):
                return rule.risk_class
        return "collaborative-required"

    # ───────────────────────── 判据（实验授权面） ─────────────────────────

    def permit_decision(self, scope: str) -> PermitDecision:
        """「该范围的演进动作在当前档位下可否**自动**放行」——**不自动也显式**。

        `autonomous` 且范围在低风险清单内 ⇒ 放行；其余档位 ⇒ 不放行（走协作路径）。
        档位条目**损坏** ⇒ 一律不放行并给出原因（fail-closed，[08 §5](../../../docs/技术架构-v2/08-L6-反思演进.md)）。
        """
        try:
            current = self.tier()
        except AuthorizationError as exc:
            return PermitDecision(
                permitted=False, tier=DEFAULT_TIER,
                reason=f"授权档位读不出（{exc}）——按不放行处理（fail-closed）",
            )
        if current != "autonomous":
            return PermitDecision(
                permitted=False, tier=current,
                reason=f"当前档位 {current!r} 不自动放行（08 §5：手动只建议 / 协作需批准）",
            )
        if scope not in self._scopes:
            return PermitDecision(
                permitted=False, tier=current,
                reason=f"范围 {scope!r} 不在低风险清单内（08 §5：仅表现层参数可自主）",
            )
        return PermitDecision(
            permitted=True, tier=current,
            reason=f"自主档 + 低风险范围 {scope!r}，自动放行（08 §5）",
        )

    def permits(self, scope: str) -> bool:
        """鸭子面（[08 §4](../../../docs/技术架构-v2/08-L6-反思演进.md) 的 A/B 启用门消费它）：
        该范围可否**自动**启用。**缺省不注入即是本面。**"""
        return self.permit_decision(scope).permitted

    # ───────────────────────── 条目与留痕读面 ─────────────────────────

    def entries(self) -> tuple[ConfigEntry, ...]:
        """本面登记进 [01 §7](../../../docs/技术架构-v2/01-平台共享契约.md) 的两个条目（含当前取值）。"""
        return (
            self._entry(TIER_CONFIG_ID, self.tier()),
            self._entry(GRADING_CONFIG_ID, _rules_payload(self.grading())),
        )

    def entry(self, config_id: str) -> ConfigEntry | None:
        """单取一个条目（不存在 → ``None``）。"""
        for candidate in self.entries():
            if candidate.config_id == config_id:
                return candidate
        return None

    def changes(self) -> tuple[ChangeRecord, ...]:
        """全部切换留痕（无 ``Store`` → 空集）。"""
        return () if self._store is None else self._store.changes()

    # ───────────────────────── 内部 ─────────────────────────

    def _entry(self, config_id: str, value: Any) -> ConfigEntry:
        display, schema, description, panel = _ENTRY_SPECS[config_id]
        return ConfigEntry(
            config_id=config_id, display_name=display, value_schema=schema,
            default=value, description_for_chat=description, panel_form_spec=panel,
            scope="global",
            change_policy=ChangePolicy(requires_confirmation=True),
        )


def _require_tier(value: Any) -> Tier:
    raw = value.strip() if isinstance(value, str) else value
    if raw not in TIERS:
        raise AuthorizationError(
            f"未知的演进授权档位 {value!r}（合法：{' / '.join(TIERS)}，08 §5）"
        )
    return raw  # type: ignore[return-value]


def _require_rules(
    rules: Sequence[EvolutionRiskRule | Mapping[str, Any]],
) -> tuple[EvolutionRiskRule, ...]:
    """校验风险分级清单（前缀非空、风险类合法、**不得重复前缀**——不静默后者覆盖前者）。"""
    out: list[EvolutionRiskRule] = []
    seen: set[str] = set()
    for item in rules:
        if isinstance(item, EvolutionRiskRule):
            rule = item
        else:
            try:
                rule = EvolutionRiskRule(**dict(item))
            except (ValidationError, TypeError) as exc:
                raise AuthorizationError(f"风险分级规则形态非法：{exc}") from exc
        if rule.prefix in seen:
            raise AuthorizationError(f"风险分级清单前缀重复：{rule.prefix!r}")
        seen.add(rule.prefix)
        out.append(rule)
    if not out:
        raise AuthorizationError("风险分级清单不得为空（08 §5 要求以清单形式注册）")
    return tuple(out)


def _rules_payload(rules: Sequence[EvolutionRiskRule]) -> dict[str, Any]:
    """清单 → 条目取值（对象：`{"rules": [...]}`）。"""
    return {"rules": [rule.model_dump() for rule in rules]}


def _rules_from_value(value: Any) -> tuple[EvolutionRiskRule, ...]:
    """条目取值 → 清单（形态非法 ⇒ 抛，不静默回落缺省——那会静默放宽/收紧风险判定）。"""
    if isinstance(value, Mapping) and "rules" in value:
        raw = value["rules"]
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        raw = value
    else:
        raise AuthorizationError(f"风险分级清单取值形态非法：{value!r}")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise AuthorizationError(f"风险分级清单的 rules 须为序列：{raw!r}")
    return _require_rules(raw)


_TIER_ENTRY = (
    "演进授权档位",
    {"type": "string", "enum": list(TIERS)},
    "演进动作的授权档位：manual（只建议，永不自动）/ collaborative（提案经批准后生效，缺省）"
    " / autonomous（低风险参数可小步自动优化，每次变更显式告知且可回滚）",
    PanelField(
        widget="select", label="授权档位", help_text="三档之一；缺省 collaborative",
        choices=TIERS,
    ),
)

_GRADING_ENTRY = (
    "演进风险分级清单",
    {"type": "object"},
    "逐配置前缀标注风险类：可自主 / 必须协作 / 永不可自主；未列出的前缀按必须协作处理",
    PanelField(
        widget="matrix", label="风险分级清单",
        help_text="逐前缀的风险类；写入走授权面自有 API",
    ),
)

_ENTRY_SPECS: dict[str, tuple[str, dict[str, Any], str, PanelField]] = {
    TIER_CONFIG_ID: _TIER_ENTRY,
    GRADING_CONFIG_ID: _GRADING_ENTRY,
}
