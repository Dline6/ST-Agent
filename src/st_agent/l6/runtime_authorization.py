"""L6 运行期授权档位与判据面（[08 §5](../../../docs/技术架构-v2/08-L6-反思演进.md) 共享口径 / [01 §7](../../../docs/技术架构-v2/01-平台共享契约.md)）。

自主查证循环（[05 §10](../../../docs/技术架构-v2/05-L3-对话主入口.md)）在每一步调用工具前须过一次
**授权闸门**（[D-090](../../../项目管理/决策日志.md) T-3）。本模块交付该闸门的**L6 侧**——档位与
判据的 owner；闸门本体（动作标识派生与交还）归 L3 的
[`ActionGate`](../l3/runtime/gate.py)。

**与演进授权的关系是「共享清单、分设档位」**（[01 §7](../../../docs/技术架构-v2/01-平台共享契约.md)）：

| 面 | 归属 | 本模块的角色 |
| --- | --- | --- |
| 三风险类与那张白名单清单（`evolution.risk-grading`） | [`EvolutionAuthorization`](authorization.py)（**单一事实源**） | **消费方**：`classify` 经注入的清单面转发，**不另登记第二张** |
| 演进档位（`evolution.authorization`） | [`EvolutionAuthorization`](authorization.py) | 无关（本模块**不读**它） |
| 运行期档位（`agent.authorization`） | **本模块** | owner：条目、判据、切换留痕 |

四条口径：

- **判据＝清单风险类 × 运行期档位**（[08 §5](../../../docs/技术架构-v2/08-L6-反思演进.md) 的审批门语义表同型）：
  `never-autonomous` ⇒ `denied`（红线，任何档位下都不自主）；`collaborative-required` ⇒
  `needs-confirmation`；`autonomous-ok` ⇒ 档为 `autonomous` 才 `autonomous-ok`，否则
  `needs-confirmation`。**判据住在清单与档位旁边**——[05 §10](../../../docs/技术架构-v2/05-L3-对话主入口.md)
  的闸门只做派生与适配（[D-096](../../../项目管理/决策日志.md) ②）。
- **清单是单一事实源**（[08 §7](../../../docs/技术架构-v2/08-L6-反思演进.md) 红线**单点生效**）：
  `classify` 一律转发注入的清单面；**未注入清单面即无清单可判**（fail-closed，不臆造一张）。
- **缺省与损坏都不放行**（[01 §7](../../../docs/技术架构-v2/01-平台共享契约.md)）：档位条目未落值 ⇒
  取 `collaborative`（本就不自动）；**条目损坏 / 清单读不出 ⇒ 一律 `denied` 并给出成因**，
  **不臆测为自主**。
- **运行期动作不进变更流**（[08 §5](../../../docs/技术架构-v2/08-L6-反思演进.md)）：本模块**只读**判据、
  **不写任何留痕**——工具调用不产生 `ChangeRecord`（每次调用都立变更记录会把变更历史淹掉）；
  档位**配置切换**照 01 §7 的登记项口径**留痕**，但落 `agent-change/` 而**不混进**
  `evolution-change/`（那会被 [08 §6](../../../docs/技术架构-v2/08-L6-反思演进.md) 的出厂重置
  「授权档复位」连带回放）。

**清单键空间含动作标识**（[01 §7](../../../docs/技术架构-v2/01-平台共享契约.md)）——除既有的 `config_id`
前缀外，判据也接受**运行期动作标识**：:func:`skill_action_id` 产 `skill.<base>`（工具调用），
:data:`MEMORY_WRITE_ACTION` / :data:`NET_EGRESS_ACTION` 是该键空间登记的另两类。**清单本体不变**：
`skill.` 前缀（须协作）与 `memory.` 前缀（红线）已在 [`:data:`DEFAULT_RISK_GRADING`](authorization.py) 内，
`net.egress` 未命中即保守（[01 §7](../../../docs/技术架构-v2/01-平台共享契约.md) 的白名单语义）。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from st_agent.contracts.registry_types import (
    ChangePolicy,
    ChangeRecord,
    ConfigEntry,
    PanelField,
)
from st_agent.l6.authorization import (
    DEFAULT_TIER,
    TIERS,
    RiskClass,
    Tier,
)
from st_agent.l6.authorization_store import EvolutionStore
from st_agent.l6.errors import AuthorizationError

__all__ = [
    "ACTION_VERDICTS",
    "AGENT_CHANGE_PREFIX",
    "AGENT_CONFIG_PREFIX",
    "AGENT_TIER_CONFIG_ID",
    "MEMORY_WRITE_ACTION",
    "NET_EGRESS_ACTION",
    "SKILL_ACTION_PREFIX",
    "ActionDecision",
    "ActionVerdict",
    "RuntimeAuthorization",
    "skill_action_id",
]

AGENT_CONFIG_PREFIX = "agent."
"""本模块条目的 ``config_id`` 前缀（[01 §7](../../../docs/技术架构-v2/01-平台共享契约.md) 点分语义）。"""

AGENT_TIER_CONFIG_ID = f"{AGENT_CONFIG_PREFIX}authorization"
"""运行期授权档位的条目 id（**与 `evolution.authorization` 并列不合并**，[D-090](../../../项目管理/决策日志.md) ①）。"""

AGENT_CHANGE_PREFIX = "agent-change/"
"""运行期档位变更留痕的目录前缀（`execution_log` 分区内；**不复用** `evolution-change/`）。"""

SKILL_ACTION_PREFIX = "skill."
"""工具调用类动作标识的前缀（[01 §7](../../../docs/技术架构-v2/01-平台共享契约.md) 的键空间）。"""

MEMORY_WRITE_ACTION = "memory.write"
"""记忆写入类动作标识（命既有 `memory.` 红线 ⇒ `never-autonomous`）。"""

NET_EGRESS_ACTION = "net.egress"
"""出网类动作标识（**清单未命中即保守**——须用户显式登记才可能自主）。"""

ActionVerdict = Literal["autonomous-ok", "needs-confirmation", "denied"]
"""运行期授权的三态结论（[05 §10](../../../docs/技术架构-v2/05-L3-对话主入口.md) 的闸门结论）。

**与 [`GATE_VERDICTS`](../l3/runtime/loop.py) 同词表**——L6 在 L3 之上、**不得** import 它
（[铁律 7](../../../项目管理/工程宪法.md)），故此处是**镜像副本**，两处的相乘性由
`tests/l3/test_agent_gate.py` 的跨层用例钉住（同 `AGENT_TERMINATION_LABELS` 的既有做法）。
"""

ACTION_VERDICTS: tuple[ActionVerdict, ...] = (
    "autonomous-ok", "needs-confirmation", "denied",
)


def skill_action_id(base: str) -> str:
    """工具调用的动作标识（``skill.<base>``）。

    ``base`` 是 **`skill_id` 版本剥离后的能力身份**，即工具目录里
    [`ToolEntry.name`](../l1/skills/tool_catalog.py) 的取值——故「同一能力升版」沿用**同一**
    动作标识，清单不必按版本逐条登记（[01 §2](../../../docs/技术架构-v2/01-平台共享契约.md) 的版本折叠口径）。
    """
    text = (base or "").strip()
    if not text:
        raise AuthorizationError("动作标识需要非空的 skill base（01 §7 的键空间）")
    return f"{SKILL_ACTION_PREFIX}{text}"


class ActionDecision(BaseModel):
    """一次运行期授权的结论（[01 §7](../../../docs/技术架构-v2/01-平台共享契约.md) / [08 §5](../../../docs/技术架构-v2/08-L6-反思演进.md)）。

    **不自主也显式**——``reason`` 是中性说明，随行如实告知「这一步为什么不能自主」，
    供闸门在终止交还时转述。``risk_class`` / ``tier`` 是**判据的输入快照**：读得出即照实记，
    读不出（清单或条目损坏）为 ``None``——**不拿一个猜的值充数**。
    """

    model_config = ConfigDict(frozen=True)

    action_id: str = Field(min_length=1)
    """被判的动作标识（`skill.<base>` / `memory.write` / `net.egress`）。"""
    verdict: ActionVerdict
    reason: str = ""
    risk_class: RiskClass | None = None
    """清单给出的风险类（**读不出时为 `None`**，不臆造）。"""
    tier: Tier | None = None
    """生效的运行期档位（条目损坏时为 `None`）。"""

    @property
    def permitted(self) -> bool:
        """是否**可自主执行**（仅 `autonomous-ok`；`needs-confirmation` 与 `denied` 都不）。"""
        return self.verdict == "autonomous-ok"


def _system_now() -> datetime:
    """用户本地时区当前时刻（[01 §8](../../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    return datetime.now().astimezone()


class RuntimeAuthorization:
    """运行期授权档位与判据面（读面 + 切换留痕 + 判据）。

    :param store: ``Store`` 句柄；缺省 ``None`` ＝**纯内存态**
    :param registry: 01 §7 的 [`ConfigRegistryFacade`](../l1/registry/facade.py)
        （鸭子类型 ``register_family``）；缺省 ``None`` ⇒ 条目**不登记**（读面仍可用）
    :param grading: **风险分级清单面**（鸭子类型 ``classify(action_id) -> RiskClass``，
        如 [`EvolutionAuthorization`](authorization.py)）；缺省 ``None`` ⇒ **无清单可判**，
        `decide` 一律 fail-closed（不臆造一张清单——清单是单一事实源，[A2](../../项目管理/tasks/T-AGT-005.1-运行期授权档位与共享清单.md)）
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    def __init__(
        self,
        *,
        store: Any = None,
        registry: Any = None,
        grading: Any = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = (
            EvolutionStore(store, now=now, change_prefix=AGENT_CHANGE_PREFIX)
            if store is not None else None
        )
        self._registry = registry
        self._grading = grading
        self._now = _system_now if now is None else now
        self._default_tier: Tier = DEFAULT_TIER

    # ───────────────────────── 档位 ─────────────────────────

    def tier(self) -> Tier:
        """当前运行期档位（条目缺失 ⇒ 缺省 `collaborative`；**损坏 ⇒ 抛**，不静默回落）。"""
        if self._store is None:
            return self._default_tier
        return _require_tier(self._store.current(AGENT_TIER_CONFIG_ID, self._default_tier))

    def set_tier(self, value: Any, *, trace_ref: str | None = None) -> ChangeRecord | None:
        """切换运行期档位（**留痕**；取值未变 ⇒ 不落盘、不留痕，同 `evolution.authorization`）。

        :raises AuthorizationError: 不在 :data:`~st_agent.l6.authorization.TIERS` 内
        """
        target = _require_tier(value)
        self._default_tier = target
        if self._store is None:
            return None
        return self._store.set(
            self._entry(AGENT_TIER_CONFIG_ID, target),
            previous=self._store.current(AGENT_TIER_CONFIG_ID, DEFAULT_TIER),
            trace_ref=trace_ref,
        )

    # ───────────────────────── 判据 ─────────────────────────

    def classify(self, action_id: str) -> RiskClass:
        """**经注入的清单面**判一个动作标识的风险类（清单单一事实源，不另立规则表）。

        :raises AuthorizationError: 未注入清单面 / 清单读不出（**不臆造一张**）
        """
        text = (action_id or "").strip()
        if not text:
            raise AuthorizationError("判据需要非空的动作标识（01 §7 的键空间）")
        if self._grading is None:
            raise AuthorizationError(
                "未注入风险分级清单面（`evolution.risk-grading`）——无清单可判（fail-closed）"
            )
        return self._grading.classify(text)

    def decide(self, action_id: str) -> ActionDecision:
        """该动作在当前运行期档位下可否**自主**（[05 §10](../../../docs/技术架构-v2/05-L3-对话主入口.md) 的闸门判据）。

        结论映射穷尽三类风险类 × 档位（见模块文档）；**读不出清单或档位 ⇒ `denied` + 成因**
        （fail-closed，[01 §7](../../../docs/技术架构-v2/01-平台共享契约.md)）。

        :raises AuthorizationError: 动作标识为空（调用方缺陷，fail-fast——不静默判成保守）
        """
        text = (action_id or "").strip()
        if not text:
            raise AuthorizationError("判据需要非空的动作标识（01 §7 的键空间）")
        try:
            risk = self.classify(text)
            tier = self.tier()
        except AuthorizationError as exc:
            return ActionDecision(
                action_id=text, verdict="denied",
                reason=f"运行期授权读不出（{exc}）——按不放行处理（fail-closed，01 §7）",
            )
        return _decide(text, risk, tier)

    @property
    def grading_source(self) -> Any:
        """注入的清单面本体（**同一份**清单的证据：与 [`EvolutionAuthorization`](authorization.py) 同一实例）。"""
        return self._grading

    # ───────────────────────── 条目与留痕读面 ─────────────────────────

    def entries(self) -> tuple[ConfigEntry, ...]:
        """本面登记进 [01 §7](../../../docs/技术架构-v2/01-平台共享契约.md) 的**一条**条目（含当前取值）。

        只有运行期档位这一条——**风险分级清单不在此**（它归 `evolution.risk-grading`，
        本面只是消费方；GWT-1 的「不存在第二张清单」即此）。
        """
        return (self._entry(AGENT_TIER_CONFIG_ID, self.tier()),)

    def entry(self, config_id: str) -> ConfigEntry | None:
        """单取一个条目（不存在 → ``None``）。"""
        for candidate in self.entries():
            if candidate.config_id == config_id:
                return candidate
        return None

    def changes(self) -> tuple[ChangeRecord, ...]:
        """全部切换留痕（`agent-change/`；无 ``Store`` → 空集）。"""
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


def _decide(action_id: str, risk: RiskClass, tier: Tier) -> ActionDecision:
    """三风险类 × 档位 → 三态结论（**穷尽**，无「其余」兜底）。"""
    if risk == "never-autonomous":
        return ActionDecision(
            action_id=action_id, verdict="denied", risk_class=risk, tier=tier,
            reason=(
                f"动作 {action_id!r} 属红线（never-autonomous）——任何档位下都不自主执行"
                "（08 §7；该红线与演进授权共用同一张清单）"
            ),
        )
    if risk == "collaborative-required":
        return ActionDecision(
            action_id=action_id, verdict="needs-confirmation", risk_class=risk, tier=tier,
            reason=f"动作 {action_id!r} 须协作（collaborative-required，08 §5）——交还用户确认",
        )
    if tier == "autonomous":
        return ActionDecision(
            action_id=action_id, verdict="autonomous-ok", risk_class=risk, tier=tier,
            reason=f"动作 {action_id!r} 可自主（autonomous-ok）且运行期档为 autonomous，放行（08 §5）",
        )
    return ActionDecision(
        action_id=action_id, verdict="needs-confirmation", risk_class=risk, tier=tier,
        reason=(
            f"动作 {action_id!r} 虽为可自主类，但当前运行期档 {tier!r} 不自动放行"
            "（08 §5：手动只建议 / 协作需批准）——交还用户确认"
        ),
    )


def _require_tier(value: Any) -> Tier:
    raw = value.strip() if isinstance(value, str) else value
    if raw not in TIERS:
        raise AuthorizationError(
            f"未知的运行期授权档位 {value!r}（合法：{' / '.join(TIERS)}，08 §5）"
        )
    return raw  # type: ignore[return-value]


_AGENT_TIER_ENTRY = (
    "运行期授权档位",
    {"type": "string", "enum": list(TIERS)},
    "自主查证循环调用工具前的授权档位：manual（只建议，永不自主）/ collaborative（缺省，不自动调用"
    "任何工具）/ autonomous（清单标为「可自主」的动作自动调用，其余照旧交还确认）",
    PanelField(
        widget="select", label="运行期授权档位",
        help_text="三档之一；缺省 collaborative（与演进授权档位相互独立）",
        choices=TIERS,
    ),
)

_ENTRY_SPECS: dict[str, tuple[str, dict[str, Any], str, PanelField]] = {
    AGENT_TIER_CONFIG_ID: _AGENT_TIER_ENTRY,
}
