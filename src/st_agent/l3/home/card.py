"""上下文卡片（05 §2）。

首页常驻卡片：内容 ＝ [04 §3.1](../../../docs/技术架构-v2/04-L2-记忆图谱.md) 的
Memory 关键切片——持仓摘要 / 关注池 / 当前 thesis / 偏好画像（「副驾眼中的我」，
**不超过 5 条关键标签**）；点击进入完整图谱由卡片携带的**导航目标**表达；新用户
无 Memory 时走空状态、引导 Onboarding。本模块只产**结构化内容**，不含渲染。

**六段固定**（:data:`SECTION_KEYS`）——§2 明列的六项各占一段，段序与段名不随
数据变。两段的**生产方不在本层依赖内**，故以 ``unavailable`` 形态给槽位并写明
原因（假设 A2）：未读告警数归 L5（M3 未建）；近期热点摘要须经
[05 §4](../../../docs/技术架构-v2/05-L3-对话主入口.md) 派发走 L1 Skill（归
``T-L3-002``）。生产方落地时**填槽不改造型**。

**中性视角（铁律 2）**：§6 输出校验管的是**系统生成的结论性文案**；记忆本体内容
（用户原话、推断记录）是**数据展示**、原样呈现不过校验（口径见
[D-053](../../../项目管理/决策日志.md)）。本模块**自有**的固定文案（卡片名、
引导语、空状态提示、段名、不可用原因）则按铁律 2 中性改述并过校验——§2 与 Story
原文的示例措辞含第一人称，沿用
[T-L2-004.1](../../../项目管理/tasks/T-L2-004.1-Onboarding协议与空状态.md) 的
``EMPTY_STATE_HINT`` 先例。
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l2.memory.models import AttentionNode, IdentityNode, ThesisNode
from st_agent.l2.memory.reader import MemoryReader, SliceQuery
from st_agent.l3.errors import SessionValidationError

__all__ = [
    "CARD_TITLE",
    "EMPTY_CARD_HINT",
    "HOT_TOPICS_REASON",
    "HOME_GREETING",
    "NO_MEMORY_REASON",
    "PROFILE_TAG_LIMIT",
    "SECTION_KEYS",
    "SECTION_TITLES",
    "UNREAD_ALERTS_REASON",
    "CardSection",
    "CardTag",
    "CardTarget",
    "ContextCard",
    "build_context_card",
    "checked_card",
    "checked_section",
]

CARD_TITLE = "当前上下文"
"""卡片名。§2 原文作「副驾眼中的我」，按铁律 2 中性改述（见模块文档）。"""

HOME_GREETING = "输入关注标的或想跟踪的条件，即可开始。"
"""首页引导语。Story 原文示例含第一人称，按铁律 2 中性改述。"""

EMPTY_CARD_HINT = "尚未记录任何偏好，先从关注面开始。"
"""空状态提示（§2 原文示例含第一人称，中性改述）。"""

PROFILE_TAG_LIMIT = 5
"""§2：偏好画像卡**不超过 5 条**关键标签（硬上限）。"""

SECTION_KEYS: tuple[str, ...] = (
    "profile", "holdings", "watchlist", "thesis", "unread_alerts", "hot_topics",
)
"""六段固定段序（§2 明列六项）。"""

SECTION_TITLES: dict[str, str] = {
    "profile": "偏好画像",
    "holdings": "持仓摘要",
    "watchlist": "关注池",
    "thesis": "当前观点",
    "unread_alerts": "未读告警",
    "hot_topics": "近期热点",
}

NO_MEMORY_REASON = "尚无记忆切片（新用户空状态）"
NO_HOLDINGS_REASON = "记忆中没有持仓信息"
NO_WATCHLIST_REASON = "记忆中没有关注池信息"
NO_THESIS_REASON = "记忆中没有当前观点"
NO_PROFILE_REASON = "记忆中没有可提炼的画像维度"

UNREAD_ALERTS_REASON = "未读告警数由 L5 主动触达提供，尚未接入"
"""§2 的「未读告警数」：生产方 L5（M3 未建），故本段暂不可用。"""
UNREAD_ALERTS_PRODUCER = "L5 · Ambient Delivery"
HOT_TOPICS_REASON = "近期热点摘要须经 L3 派发取数，尚未接入"
"""§2 的「近期热点摘要」：须经 05 §4 派发走 L1 热点类 Skill。"""
HOT_TOPICS_PRODUCER = "L3 §4 派发 → L1 Skill"

SectionState = Literal["ok", "empty", "unavailable"]
TagKind = Literal["sector", "theme", "risk", "years"]
TargetKind = Literal["memory_graph", "onboarding"]


class CardTag(BaseModel):
    """一条画像标签（可回溯到记忆节点，便于「可解释」）。"""

    model_config = ConfigDict(frozen=True)

    label: str
    kind: TagKind
    source_node_id: str


class CardSection(BaseModel):
    """卡片的一段。

    ``state`` 与 ``reason`` 是**强绑定**的：``ok`` 段不得带原因，``empty`` /
    ``unavailable`` 段**必须**带原因（01 §5：空 / 不可用必须携带原因，不得裸空）。
    """

    model_config = ConfigDict(frozen=True)

    key: str
    title: str
    state: SectionState
    items: tuple[str, ...] = ()
    tags: tuple[CardTag, ...] = ()
    total: int | None = None
    """截断**前**的候选条数（画像段用；与 ``limit`` 并置使截断可见）。"""
    limit: int | None = None
    reason: str | None = None
    producer: str | None = None
    """``unavailable`` 时的数据生产方说明（01 §5 的「最后更新时间」在卡片面无从给出，
    因为该段尚无任何数据落地，故记生产方而不是时间）。"""

    @model_validator(mode="after")
    def _section_shape(self) -> "CardSection":
        if self.tags and self.key != "profile":
            raise SessionValidationError(f"仅画像段可携 tags，得到 {self.key}")
        if len(self.tags) > PROFILE_TAG_LIMIT:
            raise SessionValidationError(
                f"画像标签不得超过 {PROFILE_TAG_LIMIT} 条，得到 {len(self.tags)}"
            )
        if self.state == "ok":
            if self.reason is not None:
                raise SessionValidationError(f"ok 段不得带原因（{self.key}）")
            if not self.items and not self.tags:
                raise SessionValidationError(f"ok 段必须有内容（{self.key}）")
        else:
            if not (self.reason or "").strip():
                # 红-绿已验：关掉本条时 GWT-2 的「非 ok 必带原因」用例 FAIL
                raise SessionValidationError(f"{self.state} 段必须带原因（{self.key}）")
            if self.items or self.tags:
                raise SessionValidationError(f"{self.state} 段不得携带内容（{self.key}）")
        return self


class CardTarget(BaseModel):
    """卡片上的一个导航目标（「点击进入…」的落点描述；渲染归界面层）。"""

    model_config = ConfigDict(frozen=True)

    kind: TargetKind
    query: SliceQuery | None = None
    """``kind="memory_graph"`` 时的图谱视图查询（§3.1 的 graph 视图）。"""
    reason: str | None = None

    @model_validator(mode="after")
    def _target_shape(self) -> "CardTarget":
        if self.kind == "memory_graph" and self.query is None:
            raise SessionValidationError("图谱目标必须带查询规格")
        if self.kind == "onboarding" and self.query is not None:
            raise SessionValidationError("Onboarding 目标不带记忆查询规格")
        return self


class ContextCard(BaseModel):
    """上下文卡片（§2 的结构化内容面）。"""

    model_config = ConfigDict(frozen=True)

    envelope: ResultEnvelope
    """``ok``（有记忆）/ ``empty``（新用户无 Memory，带原因）。"""
    title: str
    greeting: str
    is_empty: bool
    sections: tuple[CardSection, ...]
    graph_target: CardTarget
    onboarding_target: CardTarget | None = None
    empty_hint: str | None = None
    as_of: datetime | None = None

    @model_validator(mode="after")
    def _card_shape(self) -> "ContextCard":
        keys = tuple(s.key for s in self.sections)
        if keys != SECTION_KEYS:
            raise SessionValidationError(
                f"卡片段序与段集必须恒为 {SECTION_KEYS}（填槽不改造型），得到 {keys}"
            )
        if self.is_empty:
            if not self.onboarding_target or not self.empty_hint:
                raise SessionValidationError("空状态卡片必须带引导文案与 Onboarding 目标")
        elif self.onboarding_target is not None or self.empty_hint is not None:
            raise SessionValidationError("非空卡片不得带 Onboarding 目标或空状态文案")
        return self


def checked_section(**fields) -> CardSection:
    """构造卡片的一段（非法 → :class:`SessionValidationError`，不抛裸 pydantic 异常）。

    同 [T-L2-001.1](../../../项目管理/tasks/T-L2-001.1-记忆图谱本体节点边模型与落盘.md)
    的 ``checked_node`` 先例：pydantic 会把校验期内抛出的本层错误再包一层，工厂把它
    还原回来，使「非法即显式 ``SessionValidationError``」在 API 形状上成立。
    """
    try:
        return CardSection(**fields)
    except ValidationError as exc:
        raise SessionValidationError(f"卡片段非法：{exc}") from exc


def checked_card(**fields) -> ContextCard:
    """构造一张卡片（非法 → :class:`SessionValidationError`）。"""
    try:
        return ContextCard(**fields)
    except ValidationError as exc:
        raise SessionValidationError(f"上下文卡片非法：{exc}") from exc


def build_context_card(reader: MemoryReader, *, topic: str = "") -> ContextCard:
    """按当前记忆切片组装上下文卡片。

    ``token_budget=None``（假设 A4）：卡片要展示**全貌**而非对话注入用的小切片，
    沿用 ``SliceQuery`` 默认的 800 字符预算会把六段截到只剩一两段；限量改由本
    模块按段自理（画像段按 :data:`PROFILE_TAG_LIMIT`）。
    """
    spec = SliceQuery(task_type="chat", topic=topic, token_budget=None, view="list")
    result = reader.query(spec)

    graph_target = CardTarget(
        kind="memory_graph",
        query=SliceQuery(task_type="chat", topic=topic, token_budget=None, view="graph"),
    )
    if not result.slices:
        # 无记忆时：记忆来源的四段走 empty，另两段的**生产方本就缺失**（与有无记忆
        # 无关），仍走 unavailable——空状态不掩盖结构性的数据缺口。
        return checked_card(
            envelope=ResultEnvelope.empty(NO_MEMORY_REASON),
            title=CARD_TITLE,
            greeting=HOME_GREETING,
            is_empty=True,
            sections=(
                *(_empty_section(key, NO_MEMORY_REASON)
                  for key in ("profile", "holdings", "watchlist", "thesis")),
                _unavailable_section("unread_alerts", UNREAD_ALERTS_REASON, UNREAD_ALERTS_PRODUCER),
                _unavailable_section("hot_topics", HOT_TOPICS_REASON, HOT_TOPICS_PRODUCER),
            ),
            graph_target=graph_target,
            onboarding_target=CardTarget(kind="onboarding", reason=EMPTY_CARD_HINT),
            empty_hint=EMPTY_CARD_HINT,
            as_of=None,
        )

    attention = [s.node for s in result.slices if isinstance(s.node, AttentionNode)]
    identity = [s.node for s in result.slices if isinstance(s.node, IdentityNode)]
    thesis = [s.node for s in result.slices if isinstance(s.node, ThesisNode)]

    return checked_card(
        envelope=ResultEnvelope.ok(list(result.slices), as_of=result.as_of),
        title=CARD_TITLE,
        greeting=HOME_GREETING,
        is_empty=False,
        sections=(
            _profile_section(attention, identity),
            _items_section("holdings",
                           _flat(n.holdings for n in attention), NO_HOLDINGS_REASON),
            _items_section("watchlist",
                           _flat(n.watchlist for n in attention), NO_WATCHLIST_REASON),
            _items_section("thesis",
                           [_thesis_line(n) for n in thesis], NO_THESIS_REASON),
            _unavailable_section("unread_alerts", UNREAD_ALERTS_REASON, UNREAD_ALERTS_PRODUCER),
            _unavailable_section("hot_topics", HOT_TOPICS_REASON, HOT_TOPICS_PRODUCER),
        ),
        graph_target=graph_target,
        as_of=result.as_of,
    )


def _profile_section(
    attention: list[AttentionNode], identity: list[IdentityNode]
) -> CardSection:
    """偏好画像段：取材于**既有字段**（假设 A3），按相关性序取前 5 条。"""
    pairs: list[tuple[str, TagKind, str]] = []
    for node in attention:
        pairs += [(v, "sector", node.memory_node_id) for v in node.sector_preferences]
        pairs += [(v, "theme", node.memory_node_id) for v in node.theme_interests]
    for node in identity:
        if node.risk_preference:
            pairs.append((node.risk_preference, "risk", node.memory_node_id))
        if node.investing_years:
            pairs.append((node.investing_years, "years", node.memory_node_id))

    seen: set[str] = set()
    tags: list[CardTag] = []
    for label, kind, node_id in pairs:
        if label in seen:
            continue
        seen.add(label)
        tags.append(CardTag(label=label, kind=kind, source_node_id=node_id))

    total = len(tags)
    kept = tuple(tags[:PROFILE_TAG_LIMIT])
    if not kept:
        return _empty_section("profile", NO_PROFILE_REASON)
    return checked_section(
        key="profile",
        title=SECTION_TITLES["profile"],
        state="ok",
        tags=kept,
        total=total,
        limit=PROFILE_TAG_LIMIT,
    )


def _items_section(key: str, items: list[str], empty_reason: str) -> CardSection:
    kept = tuple(i for i in items if i.strip())
    if not kept:
        return _empty_section(key, empty_reason)
    return checked_section(key=key, title=SECTION_TITLES[key], state="ok", items=kept)


def _empty_section(key: str, reason: str) -> CardSection:
    return checked_section(key=key, title=SECTION_TITLES[key], state="empty", reason=reason)


def _unavailable_section(key: str, reason: str, producer: str) -> CardSection:
    return checked_section(
        key=key, title=SECTION_TITLES[key], state="unavailable",
        reason=reason, producer=producer,
    )


def _flat(groups: Iterable[tuple[str, ...]]) -> list[str]:
    """把多个节点的同名字段按序拍平（保持节点顺序，便于回溯）。"""
    return [v for group in groups for v in group]


def _thesis_line(node: ThesisNode) -> str:
    """一条 thesis 的展示行（对象：观点）。"""
    if node.subject:
        return f"{node.subject}：{node.view or ''}"
    return node.view or ""
