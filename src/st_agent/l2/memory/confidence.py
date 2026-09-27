"""置信度模型（04 §5）。

§5 三条：

- **基线**——``user_stated`` > ``inferred``（推断类必带 ``trace_id`` 已在 §1 由模型层强制）
- **时间衰减**——thesis 类节点信心度随时间自动降低（衰减曲线参数可配）
- **被新事件引用时重估**——事件触发重估，与衰减结合（策略实现阶段定并注册为可配置项）

**衰减与重估算出来、不回写盘**（见任务 `A1`）：§5 说信心度「自动降低」，若落成
**改写节点**，即属「副驾自主修改既有 Memory 节点内容」，与
[§3.2 红线](../../../docs/技术架构-v2/04-L2-记忆图谱.md) 直接相抵，且每次读都改盘。
故存量 ``confidence`` 是写入时刻的记录，**有效置信度**由本模块按需计算——切片
同时给出两者（``MemorySlice.confidence`` / ``.base_confidence``），使「这是用户说的
还是推断的」「衰减了多少」都可追溯。

基线的落地形态是**上限**：某来源的节点有效置信度不超过该来源的基线，故 §5 的
「``user_stated`` > ``inferred``」在**读面**成立（而不只是写入时的缺省值）。

消费方（视角、推送文案）按置信度调整表述强度——本模块的有效值即接在
:class:`MemoryReader` 的切片上（见任务 `A5`）。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from st_agent.contracts.registry_types import ChangePolicy, ChangeRecord, ConfigEntry, PanelField
from st_agent.l2.memory.config_store import MemoryPolicyStore
from st_agent.l2.memory.errors import MemoryValidationError
from st_agent.l2.memory.graph import MemoryGraph
from st_agent.l2.memory.models import NODE_TYPES, SOURCES, MemoryNode

__all__ = [
    "BASELINE_CONFIG_ID",
    "DEFAULT_BASELINE",
    "DEFAULT_DYNAMICS",
    "DYNAMICS_CONFIG_ID",
    "ConfidenceBaseline",
    "ConfidenceDynamics",
    "ConfidenceModel",
    "checked_baseline",
    "checked_dynamics",
]

BASELINE_CONFIG_ID = "memory-policy/confidence-baseline"
"""置信度基线条目的 ``config_id``（01 §7）。"""

DYNAMICS_CONFIG_ID = "memory-policy/confidence-dynamics"
"""衰减 / 重估参数条目的 ``config_id``（01 §7）。"""

Unit = Annotated[float, Field(ge=0.0, le=1.0)]


def _now() -> datetime:
    """用户本地时区当前时刻（01 §8）。"""
    return datetime.now().astimezone()


class ConfidenceBaseline(BaseModel):
    """§5 基线：按来源给出的置信度**上限**（``user_stated`` 须严格高于 ``inferred``）。"""

    model_config = ConfigDict(frozen=True)

    user_stated: Unit = 0.9
    inferred: Unit = 0.5

    @model_validator(mode="after")
    def _declared_order(self) -> "ConfidenceBaseline":
        if not self.user_stated > self.inferred:
            raise MemoryValidationError(
                "置信度基线须满足 user_stated > inferred（04 §5：用户明确表达高于副驾推断）"
            )
        return self


class ConfidenceDynamics(BaseModel):
    """§5 衰减与重估参数（哪些维度衰减、降多快、被引用后回升多少）。"""

    model_config = ConfigDict(frozen=True)

    decayed_types: tuple[str, ...] = ("thesis",)
    """参与时间衰减的节点类型（§5 只把衰减写在 thesis 上；见任务 `A2`）。"""
    half_life_days: Annotated[float, Field(gt=0.0)] = 180.0
    """半衰期（天）——``effective = base × 0.5 ** (龄期 / 半衰期)``。"""
    reference_recovery: Unit = 0.25
    """被新事件引用时的回升量（不超过该来源基线）。"""

    @field_validator("decayed_types")
    @classmethod
    def _known_types(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        unknown = sorted(set(v) - set(NODE_TYPES))
        if unknown:
            raise MemoryValidationError(
                f"未知的记忆维度 {unknown}（合法维度：{'/'.join(NODE_TYPES)}）"
            )
        return v


DEFAULT_BASELINE: dict[str, float] = ConfidenceBaseline().model_dump(mode="json")
"""缺省基线（§5 未给具体数值，此处定初版；可配）。"""

DEFAULT_DYNAMICS: dict[str, Any] = ConfidenceDynamics().model_dump(mode="json")
"""缺省衰减 / 重估参数（§5 只说「可配」，数值为初版；可配）。"""


def checked_baseline(**fields: Any) -> ConfidenceBaseline:
    """构造一个基线取值（非法 → ``MemoryValidationError``，不抛裸 pydantic 异常）。

    直接 ``ConfidenceBaseline(...)`` 时 pydantic 会把校验期抛出的本层错误再包成
    ``ValidationError``；本工厂把它还原为本层错误类型（与 ``checked_node`` 同款）。
    """
    try:
        return ConfidenceBaseline(**fields)
    except ValidationError as exc:
        raise MemoryValidationError(f"置信度基线非法：{exc}") from exc


def checked_dynamics(**fields: Any) -> ConfidenceDynamics:
    """构造一组衰减 / 重估参数（非法 → ``MemoryValidationError``，同上）。"""
    try:
        return ConfidenceDynamics(**fields)
    except ValidationError as exc:
        raise MemoryValidationError(f"置信度衰减参数非法：{exc}") from exc


def _baseline_entry(values: ConfidenceBaseline) -> ConfigEntry:
    """按 01 §7 七字段组装基线条目（``default`` 槽＝当前取值）。"""
    return ConfigEntry(
        config_id=BASELINE_CONFIG_ID,
        display_name="记忆置信度基线",
        value_schema={
            "type": "object",
            "properties": {s: {"type": "number"} for s in SOURCES},
        },
        default=values.model_dump(mode="json"),
        description_for_chat=(
            "用户明确说过的记忆、与副驾推断出的记忆，各自可信到什么程度；"
            "用户明确表达的可信度必须更高"
        ),
        panel_form_spec=PanelField(
            widget="matrix",
            label="置信度基线",
            help_text="取值 0–1；用户明确表达须高于副驾推断",
            choices=SOURCES,
        ),
        scope="global",
        change_policy=ChangePolicy(requires_confirmation=True),
    )


def _dynamics_entry(values: ConfidenceDynamics) -> ConfigEntry:
    """按 01 §7 七字段组装衰减 / 重估条目（``default`` 槽＝当前取值）。"""
    return ConfigEntry(
        config_id=DYNAMICS_CONFIG_ID,
        display_name="记忆置信度的时间衰减与重估",
        value_schema={
            "type": "object",
            "properties": {
                "decayed_types": {"type": "array",
                                  "items": {"type": "string", "enum": list(NODE_TYPES)}},
                "half_life_days": {"type": "number"},
                "reference_recovery": {"type": "number"},
            },
        },
        default=values.model_dump(mode="json"),
        description_for_chat=(
            "哪些维度的记忆会随时间降低可信度、降多快（半衰期天数），"
            "以及被新信息引用后回升多少"
        ),
        panel_form_spec=PanelField(
            widget="matrix",
            label="衰减与重估",
            help_text="半衰期以天计；回升量取值 0–1，不超过该来源的基线",
            choices=NODE_TYPES,
        ),
        scope="global",
        change_policy=ChangePolicy(requires_confirmation=True),
    )


class ConfidenceModel:
    """置信度模型门面（04 §5；条目落 ``config``，留痕落 ``execution_log``）。

    :param graph: :class:`MemoryGraph`（读配置与「被引用」判据同源）
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    def __init__(self, graph: MemoryGraph, *, now: Callable[[], datetime] | None = None) -> None:
        self._graph = graph
        self._config = MemoryPolicyStore(graph.store, now=now)
        self._now = _now if now is None else now

    # ───────────────────────── 读 ─────────────────────────

    def baseline_entry(self) -> ConfigEntry:
        """基线条目的登记形态（01 §7 七字段；供配置注册表面浏览）。"""
        return _baseline_entry(self.baseline_config())

    def dynamics_entry(self) -> ConfigEntry:
        """衰减 / 重估条目的登记形态（01 §7 七字段）。"""
        return _dynamics_entry(self.dynamics())

    def baseline_config(self) -> ConfidenceBaseline:
        """当前基线（条目缺失或损坏 → 缺省值，不因一条配置读不动就停摆）。"""
        raw = self._config.current(BASELINE_CONFIG_ID, DEFAULT_BASELINE)
        try:
            return checked_baseline(**raw)
        except (MemoryValidationError, TypeError):
            return ConfidenceBaseline()

    def dynamics(self) -> ConfidenceDynamics:
        """当前衰减 / 重估参数（同上回落口径）。"""
        raw = self._config.current(DYNAMICS_CONFIG_ID, DEFAULT_DYNAMICS)
        try:
            return checked_dynamics(**raw)
        except (MemoryValidationError, TypeError):
            return ConfidenceDynamics()

    def baseline(self, source: str) -> float:
        """某来源的置信度基线（§5：``user_stated`` > ``inferred``）。"""
        baseline = self.baseline_config()
        if source not in SOURCES:
            raise MemoryValidationError(
                f"未知的记忆来源 {source!r}（合法来源：{'/'.join(SOURCES)}）"
            )
        return baseline.user_stated if source == "user_stated" else baseline.inferred

    # ───────────────────────── 算 ─────────────────────────

    def effective(self, node: MemoryNode, *, now: datetime | None = None) -> float:
        """节点的**有效**置信度（§5：基线封顶 → 时间衰减 → 被引用时重估）。"""
        moment = now if now is not None else self._now()
        ceiling = self.baseline(node.source)
        value = min(float(node.confidence), ceiling)
        dynamics = self.dynamics()
        if node.type in dynamics.decayed_types:
            age_days = max(0.0, (moment - node.updated_at).total_seconds() / 86400.0)
            value *= 0.5 ** (age_days / dynamics.half_life_days)
            if self._referenced_since(node):
                value = min(ceiling, value + dynamics.reference_recovery)
        return max(0.0, min(1.0, value))

    # ───────────────────────── 写 ─────────────────────────

    def set_baseline(
        self, values: ConfidenceBaseline, *, trace_ref: str | None = None
    ) -> ChangeRecord | None:
        """改一版基线并留痕（**取值未变则不留痕**，返回 ``None``）。"""
        return self._config.set(_baseline_entry(values),
                                previous=self.baseline_config().model_dump(mode="json"),
                                trace_ref=trace_ref)

    def set_dynamics(
        self, values: ConfidenceDynamics, *, trace_ref: str | None = None
    ) -> ChangeRecord | None:
        """改一版衰减 / 重估参数并留痕（**取值未变则不留痕**，返回 ``None``）。"""
        return self._config.set(_dynamics_entry(values),
                                previous=self.dynamics().model_dump(mode="json"),
                                trace_ref=trace_ref)

    def changes(self) -> tuple[ChangeRecord, ...]:
        """全部条目变更留痕（按 ``change_id`` 升序）。"""
        return self._config.changes()

    # ───────────────────────── 内部工具 ─────────────────────────

    def _referenced_since(self, node: MemoryNode) -> bool:
        """该节点是否在 ``updated_at`` 之后获得过新边（§5「被新事件引用」，见任务 `A3`）。"""
        return any(e.created_at > node.updated_at
                   for e in self._graph.edges_of(node.memory_node_id))
