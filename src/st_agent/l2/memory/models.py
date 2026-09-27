"""L2 记忆图谱本体（04 §1 节点模型 / §2 边模型）。

**节点**：六类（``identity`` / ``attention`` / ``thesis`` / ``history`` /
``pattern`` / ``evolution``），type 枚举固定；每类除 §1 的通用字段外，各带
自己的专属字段。判别载体是 ``type``，六类构成一个可往返的判别联合
（:data:`MEMORY_NODE_ADAPTER`）。

**边**：四类（``related_to`` / ``evolves_from`` / ``derived_from`` /
``refers_to``）。前三类的两端都是 ``memory_node_id``；``refers_to`` 的**外端**
是外部 ID（``stock_id`` / ``announcement_id``），本节**不**为外部实体建影子节点
（L2 不复制 L0 数据）。边一律**按写入方向**存储（有向）——``related_to`` 的对称性
属消费方语义，不做「写一条自动补反条」的隐式行为。

**通用字段不变量**（§1；构造期强制，不静默补默认）：

- ``memory_node_id`` 形态 ``mn_<20 位十六进制>``（01 §1，经 ``MemoryNodeId``）
- ``source=inferred`` 必带 ``provenance.trace_id``（推断依据可追溯）；
  ``source=user_stated`` 不得带 ``provenance``（推断依据只属于推断类节点）
- ``privacy_level ∈ {public, private, sensitive}``
- 时间字段带时区（01 §8），且 ``updated_at >= created_at``
- 每个节点**至少一个非空专属字段**——空节点（只有通用字段）无意义，
  §7 的「某维度无记忆」表达为**没有该节点**，而不是建一个空节点

``confidence`` 取 ``[0, 1]`` 浮点：§5 的基线（``user_stated`` > ``inferred``）与
时间衰减（「信心度随时间自动降低」，衰减曲线参数可配）都是数值语义；展示层的
「高/中/低」分档属渲染面。**基线取值与衰减模型归 `T-L2-002`（§5）**，本节只定字段形态。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, ClassVar, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from st_agent.contracts.identifiers import (
    AnnouncementId,
    FeedbackId,
    MemoryNodeId,
    StockId,
    TraceId,
)
from st_agent.l2.memory.errors import MemoryValidationError

__all__ = [
    "EDGE_PREFIX",
    "EDGE_TYPES",
    "INTERNAL_EDGE_TYPES",
    "MEMORY_NODE_ADAPTER",
    "NODE_PREFIX",
    "NODE_TYPES",
    "PRIVACY_LEVELS",
    "SOURCES",
    "AnyNode",
    "AttentionNode",
    "EdgeKey",
    "EdgeTypeName",
    "EvolutionNode",
    "EvolutionPoint",
    "HistoryNode",
    "IdentityNode",
    "MemoryEdge",
    "MemoryNode",
    "PatternNode",
    "PrivacyLevel",
    "Provenance",
    "RevisionEntry",
    "Source",
    "ThesisNode",
    "TypeName",
    "check_node_id",
    "checked_edge",
    "checked_node",
    "edge_key",
    "new_node_id",
    "node_text",
    "parse_node",
]

NODE_PREFIX = "node/"
"""``memory`` 分区内节点文件的目录前缀（一节点一文件）。"""
EDGE_PREFIX = "edge/"
"""``memory`` 分区内边文件的目录前缀（一边一文件）。"""

NODE_TYPES: tuple[str, ...] = (
    "identity", "attention", "thesis", "history", "pattern", "evolution",
)
"""§1 六类节点的 type 枚举（机器可读副本）。"""

EDGE_TYPES: tuple[str, ...] = (
    "related_to", "evolves_from", "derived_from", "refers_to",
)
"""§2 四类边的 edge_type 枚举（机器可读副本）。"""

INTERNAL_EDGE_TYPES: tuple[str, ...] = ("related_to", "evolves_from", "derived_from")
"""两端均为 ``memory_node_id`` 的边类型；其余（``refers_to``）外端为外部 ID。"""

SOURCES: tuple[str, ...] = ("user_stated", "inferred")
"""§1 ``source`` 枚举：用户明确表达 / 副驾推断。"""

PRIVACY_LEVELS: tuple[str, ...] = ("public", "private", "sensitive")
"""§1 ``privacy_level`` 枚举（导出过滤依据，见 09-生态 §2）。"""

TypeName = Literal["identity", "attention", "thesis", "history", "pattern", "evolution"]
Source = Literal["user_stated", "inferred"]
PrivacyLevel = Literal["public", "private", "sensitive"]
EdgeTypeName = Literal["related_to", "evolves_from", "derived_from", "refers_to"]


def new_node_id() -> str:
    """生成一个新的 ``memory_node_id``（01 §1：本机生成的 20 位十六进制串）。"""
    return MemoryNodeId.generate().value


def _require_tz(value: datetime, field: str) -> datetime:
    if value.tzinfo is None:
        raise MemoryValidationError(f"{field} 必须带时区语义（01 §8：内部传输带时区）")
    return value


def check_node_id(raw: str) -> str:
    """校验 ``memory_node_id`` 形态（非法 → ``MemoryValidationError``）。"""
    try:
        MemoryNodeId.of(raw)
    except ValueError as exc:
        raise MemoryValidationError(f"memory_node_id 非法 {raw!r}：{exc}") from exc
    return raw


def _check_ref_target(raw: str) -> str:
    """校验 ``refers_to`` 外端：``memory_node_id`` 或外部 ID（``stock_id`` / ``announcement_id``）。"""
    for check in (MemoryNodeId.of, StockId.of, AnnouncementId.of):
        try:
            check(raw)
            return raw
        except ValueError:
            continue
    raise MemoryValidationError(
        f"refers_to 目标非法 {raw!r}（须为 memory_node_id / stock_id / announcement_id）"
    )


def _nonempty(value: Any) -> bool:
    """专属字段的「非空」判据：None / 空白串 / 空序列均算空。"""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (tuple, list, dict, set)):
        return len(value) > 0
    return True


def _string_leaves(payload: Any) -> list[str]:
    """递归收集 JSON 可序列化载荷里的全部字符串（相关性判据的取材面）。"""
    if isinstance(payload, str):
        return [payload]
    if isinstance(payload, dict):
        return [s for v in payload.values() for s in _string_leaves(v)]
    if isinstance(payload, (list, tuple)):
        return [s for v in payload for s in _string_leaves(v)]
    return []


class Provenance(BaseModel):
    """推断依据（§1：``inferred`` 类节点必带 ``trace_id`` 供下钻）。"""

    model_config = ConfigDict(frozen=True)

    trace_id: Annotated[str, Field(min_length=3, max_length=128)]
    """产生该推断的推理链锚点（01 §4）。"""

    @field_validator("trace_id")
    @classmethod
    def _check_trace(cls, v: str) -> str:
        try:
            TraceId.of(v)
        except ValueError as exc:
            raise MemoryValidationError(f"provenance.trace_id 非法 {v!r}：{exc}") from exc
        return v


class RevisionEntry(BaseModel):
    """一次修正的历史快照（§1 ``revision_history``：旧值保留供审计）。

    粒度＝**整节点快照**（被替换前的完整节点 JSON）＋修正时刻＋缘由，故任一历史
    版本都可完整回放。修正只可能由**用户显式编辑**触发（§3.2 红线），触发方因此
    不入字段。
    """

    model_config = ConfigDict(frozen=True)

    replaced_at: datetime
    """被替换的时刻（本地时区）。"""
    previous: dict[str, Any]
    """被替换前的节点快照（``model_dump(mode="json")``）。"""
    reason: str | None = None
    """修正缘由（可选；面向人的中性措辞）。"""

    @model_validator(mode="after")
    def _entry_shape(self) -> "RevisionEntry":
        _require_tz(self.replaced_at, "replaced_at")
        return self


class EvolutionPoint(BaseModel):
    """``evolution`` 节点时间序列的一点（§1：某维度偏好随时间的迁移记录）。"""

    model_config = ConfigDict(frozen=True)

    at: datetime
    value: str
    """该时刻的偏好取值（如「偏激进」「偏稳健」）。"""

    @model_validator(mode="after")
    def _point_shape(self) -> "EvolutionPoint":
        _require_tz(self.at, "at")
        if not self.value.strip():
            raise MemoryValidationError("evolution 迁移点的 value 不得为空白")
        return self


class MemoryNode(BaseModel):
    """记忆节点基类（§1 通用字段 + 通用不变量）。

    不直接使用——实际节点是六类子类之一，经 :data:`MEMORY_NODE_ADAPTER` 构造。
    """

    model_config = ConfigDict(frozen=True)

    type: TypeName
    memory_node_id: Annotated[str, Field(min_length=3, max_length=128)]
    confidence: float = Field(ge=0.0, le=1.0)
    source: Source
    provenance: Provenance | None = None
    privacy_level: PrivacyLevel
    revision_history: tuple[RevisionEntry, ...] = ()
    created_at: datetime
    updated_at: datetime

    _own_fields: ClassVar[tuple[str, ...]] = ()
    """该类型的专属字段名（子类覆写；用于「至少一个非空」判据）。"""

    @model_validator(mode="after")
    def _common_invariants(self) -> "MemoryNode":
        """§1 通用字段不变量。

        方法名**不得**与任何子类的校验器同名——pydantic 中子类定义同名方法会
        **替换**继承来的校验器，被替换方将静默不执行（本叶测试撞出过此坑）。
        """
        check_node_id(self.memory_node_id)
        _require_tz(self.created_at, "created_at")
        _require_tz(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise MemoryValidationError("updated_at 不得早于 created_at")
        if self.source == "inferred":
            if self.provenance is None:
                raise MemoryValidationError(
                    "source=inferred 必带 provenance.trace_id（04 §1：推断依据可追溯）"
                )
        elif self.provenance is not None:
            raise MemoryValidationError(
                "source=user_stated 不得带 provenance（推断依据只属于推断类节点）"
            )
        if not any(_nonempty(getattr(self, f)) for f in self._own_fields):
            raise MemoryValidationError(
                f"{self.type} 节点至少需要一个非空专属字段（04 §1：空节点无意义，"
                "『某维度无记忆』表达为没有该节点）"
            )
        return self


class IdentityNode(MemoryNode):
    """§1 ``identity``（你是谁）：风险偏好 / 资金规模档 / 投资年限 / 认知偏差自评。"""

    type: Literal["identity"] = "identity"
    risk_preference: str | None = None
    capital_scale: str | None = None
    investing_years: str | None = None
    cognitive_bias_self_eval: str | None = None

    _own_fields: ClassVar[tuple[str, ...]] = (
        "risk_preference", "capital_scale", "investing_years", "cognitive_bias_self_eval",
    )


class AttentionNode(MemoryNode):
    """§1 ``attention``（你在关注什么）：持仓 / 观察池 / 板块偏好 / 题材兴趣。"""

    type: Literal["attention"] = "attention"
    holdings: tuple[str, ...] = ()
    """持仓（``stock_id`` 列表）。"""
    watchlist: tuple[str, ...] = ()
    sector_preferences: tuple[str, ...] = ()
    theme_interests: tuple[str, ...] = ()

    _own_fields: ClassVar[tuple[str, ...]] = (
        "holdings", "watchlist", "sector_preferences", "theme_interests",
    )

    @field_validator("holdings")
    @classmethod
    def _holdings_are_stock_ids(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        for sid in v:
            try:
                StockId.of(sid)
            except ValueError as exc:
                raise MemoryValidationError(f"持仓项非法 {sid!r}（须为 stock_id）：{exc}") from exc
        return v


class ThesisNode(MemoryNode):
    """§1 ``thesis``（你的 thesis）：对象（个股/板块）/ 观点内容 / 时间戳。

    信心度即通用字段 ``confidence``（§5 的衰减对象）。
    """

    type: Literal["thesis"] = "thesis"
    subject: str | None = None
    """观点对象（个股 ``stock_id`` 或板块名）。"""
    subject_kind: Literal["stock", "sector"] | None = None
    view: str | None = None
    """观点内容。"""
    stated_at: datetime | None = None
    """用户表达该观点的时间戳（§1）。"""

    _own_fields: ClassVar[tuple[str, ...]] = ("subject", "subject_kind", "view", "stated_at")

    @model_validator(mode="after")
    def _thesis_shape(self) -> "ThesisNode":
        if self.stated_at is not None:
            _require_tz(self.stated_at, "stated_at")
        if self.subject_kind == "stock" and self.subject:
            try:
                StockId.of(self.subject)
            except ValueError as exc:
                raise MemoryValidationError(
                    f"subject_kind=stock 时 subject 须为 stock_id，得到 {self.subject!r}：{exc}"
                ) from exc
        return self


class HistoryNode(MemoryNode):
    """§1 ``history``（你的历史）：决策事件 / 采纳或忽略的信号 / 结果对错。"""

    type: Literal["history"] = "history"
    event: str | None = None
    """决策事件。"""
    feedback_id: str | None = None
    """采纳 / 忽略的信号（``feedback_id``，01 §1）。"""
    outcome: Literal["right", "wrong", "unknown"] | None = None
    """结果对错。"""

    _own_fields: ClassVar[tuple[str, ...]] = ("event", "feedback_id", "outcome")

    @field_validator("feedback_id")
    @classmethod
    def _feedback_id_shape(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            FeedbackId.of(v)
        except ValueError as exc:
            raise MemoryValidationError(f"feedback_id 非法 {v!r}：{exc}") from exc
        return v


class PatternNode(MemoryNode):
    """§1 ``pattern``（你的模式）：副驾观察到的行为模式 + 触发依据。

    ``triggered_by`` 引用被聚合的 ``history`` 节点（§2 ``derived_from`` 边的
    语义载体），故逐项须为 ``memory_node_id``。
    """

    type: Literal["pattern"] = "pattern"
    pattern: str | None = None
    triggered_by: tuple[str, ...] = ()

    _own_fields: ClassVar[tuple[str, ...]] = ("pattern", "triggered_by")

    @field_validator("triggered_by")
    @classmethod
    def _triggers_are_node_ids(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        for nid in v:
            check_node_id(nid)
        return v


class EvolutionNode(MemoryNode):
    """§1 ``evolution``（你的偏好演化）：某维度偏好随时间的迁移记录。"""

    type: Literal["evolution"] = "evolution"
    dimension: str | None = None
    trajectory: tuple[EvolutionPoint, ...] = ()

    _own_fields: ClassVar[tuple[str, ...]] = ("dimension", "trajectory")


AnyNode = Annotated[
    IdentityNode | AttentionNode | ThesisNode | HistoryNode | PatternNode | EvolutionNode,
    Field(discriminator="type"),
]
"""六类节点的判别联合（``type`` 为判别键）。"""

MEMORY_NODE_ADAPTER: TypeAdapter[AnyNode] = TypeAdapter(AnyNode)
"""节点往返的单一入口（解析 ``memory`` 分区内记录时用它，不手写分支）。"""


class MemoryEdge(BaseModel):
    """一条记忆边（§2 边模型）。

    ``source_id`` 恒为 ``memory_node_id``；``target_id`` 对前三类边同为
    ``memory_node_id``，对 ``refers_to`` 可为外部 ID（见 :func:`edge_key`）。
    """

    model_config = ConfigDict(frozen=True)

    edge_type: EdgeTypeName
    source_id: Annotated[str, Field(min_length=3, max_length=128)]
    target_id: Annotated[str, Field(min_length=3, max_length=128)]
    created_at: datetime

    @model_validator(mode="after")
    def _edge_shape(self) -> "MemoryEdge":
        _require_tz(self.created_at, "created_at")
        check_node_id(self.source_id)
        if self.edge_type in INTERNAL_EDGE_TYPES:
            check_node_id(self.target_id)
        else:
            _check_ref_target(self.target_id)
        return self


EdgeKey = str


def edge_key(edge: MemoryEdge) -> EdgeKey:
    """边的落盘键（``memory`` 分区内文件名主干）。

    04 §2 未给边定义 ID，01 §1 亦无 ``edge_id``（新增契约 ID 类须先改 01，铁律 8）。
    故取**确定性组合键** ``<source>__<edge_type>__<target>``：同一条边天然幂等
    （重复写入不产生第二条），且不触碰契约。
    """
    return f"{edge.source_id}__{edge.edge_type}__{edge.target_id}"


def checked_node(**fields: Any) -> MemoryNode:
    """构造一个记忆节点（非法 → ``MemoryValidationError``，不抛裸 pydantic 异常）。"""
    try:
        return MEMORY_NODE_ADAPTER.validate_python(fields)
    except ValidationError as exc:
        raise MemoryValidationError(f"记忆节点非法：{exc}") from exc


def parse_node(raw: bytes) -> MemoryNode:
    """解析 ``memory`` 分区内的一条节点记录（损坏 → ``MemoryValidationError``）。"""
    try:
        return MEMORY_NODE_ADAPTER.validate_json(raw)
    except ValidationError as exc:
        raise MemoryValidationError(f"记忆节点记录损坏无法解析：{exc}") from exc


def checked_edge(**fields: Any) -> MemoryEdge:
    """构造一条记忆边（非法 → ``MemoryValidationError``，不抛裸 pydantic 异常）。

    直接 ``MemoryEdge(...)`` 时 pydantic 会把校验期抛出的本层错误再包成
    ``ValidationError``；本工厂把它还原为本层错误类型。
    """
    try:
        return MemoryEdge(**fields)
    except ValidationError as exc:
        raise MemoryValidationError(f"记忆边非法：{exc}") from exc


def node_text(node: MemoryNode) -> str:
    """节点的可检索文本（相关性判据取材面；递归收集全部字符串叶子）。"""
    return " ".join(_string_leaves(node.model_dump(mode="json")))
