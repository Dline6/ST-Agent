"""Onboarding 协议（04 §7）。

新用户首启的**对话式快速画像构建**：问题清单不超过 10 问、由 01 §7 配置注册表
管理（可随官方 Pack 更新）、支持跳过；答完把答案落成初始节点；某维度无记忆时
**可查到**（空状态语义），且不阻断其他功能。

三条落点：

- **清单是配置条目**（``memory-policy/onboarding-questions``），落 ``config`` 分区；
  改一版留一条 ``ChangeRecord``（``change_id`` 作回滚单位），与 ``write_policy`` /
  ``confidence`` 共用 [`config_store`](config_store.py) 的同一机制
- **答案 → 节点**：维度按 [§1](../memory/models.py) 的字段表映射到六类节点之一
  （本题清单覆盖的三处落在 ``identity`` / ``attention`` 两类上——[§7](../../../docs/技术架构-v2/04-L2-记忆图谱.md)
  说的「3 类」是**维度**口径）。同一节点类型的多个维度答案**合建一个节点**：
  §1 说「某维度无记忆」表达为没有信息，不是说每个字段一个节点
- **空状态是查询、不是阻断**（§7）：``empty_dimensions`` 只回答「哪些维度没有信息」，
  渲染与引导入口归交互层（L3），本模块不产 UI 文案之外的任何副作用

写入路径：Onboarding 是**用户显式**表达（§3.2），故 ``source=user_stated`` 直接写入，
不过 §4 的自主写入白名单（那条只管 ``inferred``）。

**措辞**：[§7](../../../docs/技术架构-v2/04-L2-记忆图谱.md) 原文的空状态示例含第一人称，
与 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md) 输出校验（渲染前禁用第一人称）
相抵，本模块按铁律 2 取中性改述（``EMPTY_STATE_HINT``）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from st_agent.contracts.registry_types import (
    ChangePolicy,
    ChangeRecord,
    ConfigEntry,
    PanelField,
)
from st_agent.l2.memory.config_store import MemoryPolicyStore
from st_agent.l2.memory.errors import MemoryValidationError
from st_agent.l2.memory.graph import MemoryGraph
from st_agent.l2.memory.models import (
    NODE_TYPES,
    AttentionNode,
    EvolutionNode,
    HistoryNode,
    IdentityNode,
    MemoryNode,
    PatternNode,
    ThesisNode,
    TypeName,
    checked_node,
    new_node_id,
)
from st_agent.l2.memory.writer import MemoryWriter

__all__ = [
    "DEFAULT_ONBOARDING_QUESTIONS",
    "DEFAULT_STATED_CONFIDENCE",
    "EMPTY_STATE_HINT",
    "MAX_ONBOARDING_QUESTIONS",
    "ONBOARDING_CONFIG_ID",
    "REQUIRED_DIMENSIONS",
    "OnboardingProtocol",
    "OnboardingQuestion",
    "checked_questions",
]

ONBOARDING_CONFIG_ID = "memory-policy/onboarding-questions"
"""问题清单条目的 ``config_id``（01 §7）。"""

MAX_ONBOARDING_QUESTIONS = 10
"""§7：Onboarding 不超过 10 个问题（硬上限，写入时显式拒超）。"""

REQUIRED_DIMENSIONS: tuple[str, ...] = (
    "risk_preference", "investing_years", "sector_preferences",
)
"""§7 点名的三处初始画像维度（风险偏好 / 投资年限 / 当前关注板块）。

[§1](../../../docs/技术架构-v2/04-L2-记忆图谱.md) 的字段表把它们落在**两类**节点上：
前两项是 ``identity`` 的字段，第三项是 ``attention`` 的字段——故 §7 的「3 类」
按**维度**读（见任务 `A1`）。
"""

DEFAULT_STATED_CONFIDENCE = 0.9
"""初始画像的置信度初版值（§7 未规定；取 [§5](../../../docs/技术架构-v2/04-L2-记忆图谱.md)
基线 ``user_stated`` 的缺省值，读面另按基线封顶）。"""

EMPTY_STATE_HINT = "这个维度还没有任何信息"
"""空状态提示（中性措辞——§7 原文示例含第一人称，与 01 §6 相抵，见模块文档）。"""


class OnboardingQuestion(BaseModel):
    """一条 Onboarding 问题（01 §7 条目 ``default`` 槽里的一项）。

    ``question_id`` 缺省等于维度名（如 ``risk_preference``），空状态查询按它点名。
    """

    model_config = ConfigDict(frozen=True)

    question_id: Annotated[str, Field(min_length=1, max_length=64)]
    prompt: Annotated[str, Field(min_length=1)]
    """面向用户的提问（中性措辞）。"""
    node_type: TypeName
    """答案落到的节点类型（§1 六类之一）。"""
    field: Annotated[str, Field(min_length=1)]
    """答案落到的该类型字段名。"""

    @model_validator(mode="after")
    def _field_exists_on_type(self) -> "OnboardingQuestion":
        model = _NODE_MODELS[self.node_type]
        if self.field not in model.model_fields:
            raise MemoryValidationError(
                f"问题 {self.question_id!r} 的目标字段 {self.field!r} 不在 "
                f"{self.node_type} 节点上（合法字段：{sorted(model.model_fields)}）"
            )
        return self


_NODE_MODELS: dict[str, type[MemoryNode]] = {
    "identity": IdentityNode,
    "attention": AttentionNode,
    "thesis": ThesisNode,
    "history": HistoryNode,
    "pattern": PatternNode,
    "evolution": EvolutionNode,
}
"""类型名 → 节点模型（字段名合法性的判据面；键域由 §1 固定）。"""

assert tuple(_NODE_MODELS) == NODE_TYPES, "问题清单的类型域须与 §1 六类同步"


DEFAULT_ONBOARDING_QUESTIONS: tuple[OnboardingQuestion, ...] = (
    OnboardingQuestion(question_id="risk_preference", node_type="identity",
                       field="risk_preference",
                       prompt="你的风险偏好属于哪一类（如：保守 / 稳健 / 进取）？"),
    OnboardingQuestion(question_id="investing_years", node_type="identity",
                       field="investing_years",
                       prompt="你的投资年限大约多久？"),
    OnboardingQuestion(question_id="capital_scale", node_type="identity",
                       field="capital_scale",
                       prompt="可投资资金大致处于什么规模档？"),
    OnboardingQuestion(question_id="cognitive_bias_self_eval", node_type="identity",
                       field="cognitive_bias_self_eval",
                       prompt="你认为自己在投资上最容易受哪类认知偏差影响？"),
    OnboardingQuestion(question_id="sector_preferences", node_type="attention",
                       field="sector_preferences",
                       prompt="当前主要关注哪些板块？"),
    OnboardingQuestion(question_id="theme_interests", node_type="attention",
                       field="theme_interests",
                       prompt="当前关注哪些题材？"),
)
"""缺省问题清单（6 问 ≤ 10，覆盖 §7 点名的三个必需维度；可随官方 Pack 更新）。"""


def _system_now() -> datetime:
    """用户本地时区当前时刻（01 §8）。"""
    return datetime.now().astimezone()


def checked_questions(raw: Any) -> tuple[OnboardingQuestion, ...]:
    """把条目 ``default`` 槽的原始取值解成问题清单（非法 → ``MemoryValidationError``）。

    校验两件事：逐条形状合法；总条数 ≤ :data:`MAX_ONBOARDING_QUESTIONS`（§7 硬上限）。
    """
    if not isinstance(raw, (list, tuple)):
        raise MemoryValidationError(f"Onboarding 问题清单须为列表，得到 {type(raw).__name__}")
    try:
        questions = tuple(OnboardingQuestion(**q) for q in raw)
    except (ValidationError, TypeError, MemoryValidationError) as exc:
        raise MemoryValidationError(f"Onboarding 问题清单非法：{exc}") from exc
    if len(questions) > MAX_ONBOARDING_QUESTIONS:
        raise MemoryValidationError(
            f"Onboarding 问题清单不得超过 {MAX_ONBOARDING_QUESTIONS} 条"
            f"（04 §7），得到 {len(questions)} 条"
        )
    seen: set[str] = set()
    for q in questions:
        if q.question_id in seen:
            raise MemoryValidationError(f"Onboarding 问题 id 重复：{q.question_id!r}")
        seen.add(q.question_id)
    return questions


DEFAULT_SERIALIZED: list[dict[str, str]] = [
    q.model_dump(mode="json") for q in DEFAULT_ONBOARDING_QUESTIONS
]
"""缺省清单的 JSON 形态（条目缺省值 / 损坏回落用）。"""


def _questions_entry(questions: tuple[OnboardingQuestion, ...]) -> ConfigEntry:
    """按 01 §7 七字段组装问题清单条目（``default`` 槽＝当前取值）。"""
    return ConfigEntry(
        config_id=ONBOARDING_CONFIG_ID,
        display_name="Onboarding 问题清单",
        value_schema={
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question_id": {"type": "string"},
                    "prompt": {"type": "string"},
                    "node_type": {"type": "string", "enum": list(NODE_TYPES)},
                    "field": {"type": "string"},
                },
                "required": ["question_id", "prompt", "node_type", "field"],
            },
            "maxItems": MAX_ONBOARDING_QUESTIONS,
        },
        default=[q.model_dump(mode="json") for q in questions],
        description_for_chat=(
            "首次使用时用来建立初始画像的问题清单，最多 10 问，"
            "每一问对应一个记忆维度，可随官方 Pack 更新"
        ),
        panel_form_spec=PanelField(
            widget="matrix",
            label="Onboarding 问题清单",
            help_text=f"逐行一条问题；最多 {MAX_ONBOARDING_QUESTIONS} 条；每条的 node_type 取六类记忆之一",
            choices=NODE_TYPES,
        ),
        scope="global",
        change_policy=ChangePolicy(requires_confirmation=True),
    )


class OnboardingProtocol:
    """Onboarding 协议门面（04 §7；条目落 ``config``，留痕落 ``execution_log``）。

    :param graph: :class:`MemoryGraph`（读配置与空状态判据同源）
    :param writer: :class:`MemoryWriter`（初始画像的写入面）
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    def __init__(
        self,
        graph: MemoryGraph,
        writer: MemoryWriter,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._graph = graph
        self._writer = writer
        self._store = MemoryPolicyStore(graph.store, now=now)
        self._now = _system_now if now is None else now

    # ───────────────────────── 读 ─────────────────────────

    def questions(self) -> tuple[OnboardingQuestion, ...]:
        """当前问题清单（条目缺失或损坏 → 缺省清单，不因一条配置读不动就停摆）。"""
        raw = self._store.current(ONBOARDING_CONFIG_ID, DEFAULT_SERIALIZED)
        try:
            return checked_questions(raw)
        except MemoryValidationError:
            return DEFAULT_ONBOARDING_QUESTIONS

    def question_entry(self) -> ConfigEntry:
        """问题清单条目的登记形态（01 §7 七字段；供配置注册表面浏览）。"""
        return _questions_entry(self.questions())

    def changes(self) -> tuple[ChangeRecord, ...]:
        """全部条目变更留痕（按 ``change_id`` 升序）。"""
        return self._store.changes()

    # ───────────────────────── 写 ─────────────────────────

    def set_questions(
        self, questions: tuple[OnboardingQuestion, ...], *, trace_ref: str | None = None
    ) -> ChangeRecord | None:
        """改一版问题清单并留痕（**取值未变则不留痕**，返回 ``None``）。

        超 10 条、字段不在目标节点上、问题 id 重复——一律显式拒（不静默截断）。
        """
        checked = checked_questions([q.model_dump(mode="json") for q in questions])
        return self._store.set(
            _questions_entry(checked),
            previous=[q.model_dump(mode="json") for q in self.questions()],
            trace_ref=trace_ref,
        )

    def build_initial_nodes(
        self,
        answers: Mapping[str, object],
        *,
        confidence: float = DEFAULT_STATED_CONFIDENCE,
        privacy_level: str = "private",
    ) -> tuple[MemoryNode, ...]:
        """把 Onboarding 答案落成初始画像节点（§7：产出至少三个维度的初始节点）。

        :param answers: ``question_id → 答案``。空白按**跳过**处理（§7 支持跳过）；
            未知的 ``question_id`` 显式拒（不静默丢弃）。答案可以是字符串（标量字段，
            如 ``risk_preference``），也可以是字符串序列（集合字段，如
            ``sector_preferences``）——**同一串多值由调用方拆好**，本模块不猜分隔符
        :param confidence: 初始画像的置信度（缺省 = §5 ``user_stated`` 基线）
        :param privacy_level: 初始画像的隐私分级（缺省 ``private``——画像属个人面）

        同一节点类型的多个维度答案**合建一个节点**（§1：「某维度无记忆」表达为没有
        该字段，不是没有节点）。全部维度都被跳过时不产任何节点，也不报错。
        """
        by_id = {q.question_id: q for q in self.questions()}
        unknown = sorted(set(answers) - set(by_id))
        if unknown:
            raise MemoryValidationError(
                f"未知的 Onboarding 问题 id：{unknown}（合法 id：{sorted(by_id)}）"
            )

        grouped: dict[str, dict[str, object]] = {}
        for question_id, value in answers.items():
            if not _nonempty(value):
                continue                       # §7：跳过（含空白值）
            q = by_id[question_id]
            grouped.setdefault(q.node_type, {})[q.field] = _answer_value(q, value)

        moment = self._now()
        created: list[MemoryNode] = []
        for type_name in NODE_TYPES:            # 按 §1 的枚举序产出，确定序便于比对
            fields = grouped.get(type_name)
            if not fields:
                continue
            node = checked_node(
                type=type_name,
                memory_node_id=new_node_id(),
                confidence=confidence,
                source="user_stated",
                privacy_level=privacy_level,
                created_at=moment,
                updated_at=moment,
                **fields,
            )
            created.append(self._writer.add_node(node))
        return tuple(created)

    # ───────────────────────── 空状态 ─────────────────────────

    def empty_dimensions(self) -> tuple[str, ...]:
        """没有信息的维度（§7 空状态语义；按当前问题清单的 ``question_id`` 点名）。

        某维度为「空」＝ 该问题指向的节点类型下，**没有任何节点**在该字段上有非空值。
        这是**查询**——不阻断任何其他功能，渲染与引导入口归交互层。
        """
        nodes = self._graph.nodes()
        empty: list[str] = []
        for q in self.questions():
            filled = any(
                _nonempty(getattr(n, q.field, None))
                for n in nodes
                if n.type == q.node_type
            )
            if not filled:
                empty.append(q.question_id)
        return tuple(empty)

    def empty_state(self) -> dict[str, str]:
        """空状态报告：``question_id → 中性提示语``（供交互层直接渲染）。"""
        return {qid: EMPTY_STATE_HINT for qid in self.empty_dimensions()}


def _nonempty(value: Any) -> bool:
    """非空判据（与 §1 的「至少一个非空专属字段」同款口径）。"""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (tuple, list, dict, set)):
        return len(value) > 0
    return True


def _is_sequence_field(node_type: str, field: str) -> bool:
    """该字段是否为集合型（``tuple[...]`` / ``list[...]``）——答案归一的判据。"""
    annotation = _NODE_MODELS[node_type].model_fields[field].annotation
    return getattr(annotation, "__origin__", None) in (tuple, list)


def _answer_value(question: OnboardingQuestion, value: object) -> object:
    """把一条答案归一成目标字段的取值形态。

    集合字段收 ``str`` 时按**单元素**处理（不猜分隔符——多值由调用方拆好传入）。
    """
    if _is_sequence_field(question.node_type, question.field):
        if isinstance(value, str):
            return (value.strip(),)
        return tuple(str(v).strip() for v in value)      # type: ignore[union-attr]
    return value.strip() if isinstance(value, str) else value
