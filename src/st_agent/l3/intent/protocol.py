"""意图理解与澄清协议（[05 §3](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

把一段用户输入收敛为 **§3.1 七类意图之一 + 派发目标**；模糊处按 **§3.2 澄清协议**
追问（预算 ≤3、每题可跳过、跳过取默认值）；无法收敛给 ≤3 个方向；收敛后产出
**意图确认卡**（「已理解 N 项」），用户确认后交 [`dispatch.bus`](../dispatch/bus.py) 派发。

**理解经注入**（假设 A1）：文本 → 七类意图的「理解」由**注入的理解端口**承担
（鸭子类型 ``understand(text) -> ResultEnvelope``，载荷为 :class:`IntentDraft`）；
缺省不注入即 fail-closed（``unavailable`` + 原因），**不硬猜**。本模块另交付一个
经注入 ``LlmClient`` 的实现 :class:`LlmIntentUnderstander`，使组合根有可跑的默认
路径——它的失败一律走信封（端点不可用 → ``unavailable``，与 [05 §4](../../../../docs/技术架构-v2/05-L3-对话主入口.md)
的「显式降级告知」同源）。

**澄清问项取自声明参数**（假设 A2）：一条问项 ＝ ``SkillDescriptor.parameters`` 中
**未给出值**的一项；「跳过」即取该参数的 ``default``；超出预算者以默认值填充并在
确认卡上标注来源。故「跳过用默认值继续」有确定的来源，而非由理解方自由产问题。
**无 target Skill 的意图**（`analyze`——其派发目标是整条 L4 流程）改取**意图级参数
声明** [`INTENT_PARAM_SPECS`]，规则逐条同 Skill 侧（见 [05 §3.2](../../../../docs/技术架构-v2/05-L3-对话主入口.md)
的「无 target Skill 的意图」条）。

**方向候选取自理解产物**（假设 A3）：本层只裁剪到 ≤3 条 + 中性改述；理解端口
不可用时**不给**方向（无从给出），走 ``unavailable`` + 原因。

**中性视角（铁律 2）**：本模块**自有**的生成文案（确认卡抬头 / 追问问句 / 方向
抬头 / 来源标签）过 [01 §6](../../../../docs/技术架构-v2/01-平台共享契约.md) 执行点 2
``check_output``，**命中即阻断**（:class:`IntentValidationError`）；而**参数值**与
方向候选的**文本本体**属数据展示 / 理解产物，按 [D-053](../../../../项目管理/决策日志.md)
的口径原样承载——故 :class:`ConfirmationItem` 把「生成描述」与「原样值」分成两个
字段，只校验前者。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from st_agent.contracts.capability_types import ParameterSpec
from st_agent.contracts.neutrality import NeutralityGuard
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l3.commands.registry import (
    COMMAND_PREFIX,
    INTENT_KINDS,
    CommandRegistry,
    IntentKind,
    ParamValue,
)
from st_agent.l3.errors import IntentValidationError

__all__ = [
    "CLARIFICATION_BUDGET",
    "CONFIRMATION_HEADER",
    "DIRECTIONS_HEADER",
    "DIRECTION_LIMIT",
    "INTENT_PARAM_SPECS",
    "INTENT_TARGETS",
    "NO_DIRECTIONS_REASON",
    "UNDERSTANDER_ABSENT_REASON",
    "UNSPECIFIED_LABEL",
    "LlmIntentUnderstander",
    "ClarificationQuestion",
    "ClarificationRound",
    "ConfirmationItem",
    "IntentConfirmation",
    "IntentDraft",
    "IntentProtocol",
    "checked_confirmation",
    "checked_draft",
    "checked_question",
    "llm_understanding_prompt",
    "parse_understanding",
]

CLARIFICATION_BUDGET = 3
"""§3.2：澄清预算 ≤ 3 个问题（硬上限）。"""

DIRECTION_LIMIT = 3
"""§3.2：无法收敛时给出 ≤ 3 个可能方向。"""

SKIP = "<skip>"
"""「跳过」哨兵（§3.2：每个问题可跳过，跳过用默认值继续）。"""

CONFIRMATION_HEADER = "已理解 {n} 项："
"""确认卡抬头。§3.2 原文作「我理解到 N 件事」，含第一人称，按铁律 2 中性改述。"""

DIRECTIONS_HEADER = "未能确定意图；可能的方向如下（至多 3 项）："
"""无法收敛时的方向抬头（§3.2；中性表述，不含第一人称）。"""

NO_DIRECTIONS_REASON = "未能确定意图，且理解结果未给出方向候选"
""":class:`IntentDraft` 既无意图又无方向时的构造拒绝理由。"""

UNDERSTANDER_ABSENT_REASON = "未注入意图理解端口，无法把输入收敛为意图（05 §3.1）"

UNSPECIFIED_LABEL = "未指定"
"""参数既无给出值也无默认值时的展示标签（生成文案，过 §6）。"""

SOURCE_LABELS: dict[str, str] = {
    "intent": "意图",
    "stated": "用户给出",
    "shortcut": "指令预填",
    "default": "默认值",
}
"""取值来源的展示标签（生成文案，逐条过 §6）。"""

ValueSource = Literal["intent", "stated", "shortcut", "default"]

INTENT_TARGETS: dict[str, str] = {
    "query": "L1 Skill 调用 → 结果渲染",
    "configure": "对话即配置（§5）",
    "analyze": "触发 06-L4 Deliberation（经确认）",
    "memory_op": "L2 写入或冲突裁决对话",
    "train": "08-L6 训练对话协议",
    "explain": "Trace 展开渲染",
    "investigate": "自主查证循环（§10）→ 证据包渲染",
}
"""[05 §3.1](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 分类表的机器可读副本（同 ``INTENT_KINDS`` 的写法）。"""

INTENT_PARAM_SPECS: dict[str, tuple[ParameterSpec, ...]] = {
    "analyze": (
        ParameterSpec(
            name="mode", type="enum", default="deep", choices=("quick", "deep"),
            description="分析模式（快速 = 核心少数视角，深度 = 全部启用视角）",
        ),
    ),
    "train": (
        ParameterSpec(
            name="correction", type="string", default="",
            description="对既有结论 / 推送的修正说明（用户原话）",
        ),
    ),
}
"""**意图级参数声明**——**没有**被调用 Skill 的意图（[05 §3.2](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 的问项来源）。

[05 §3.2](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 的问项来源是「目标 Skill 的
``SkillDescriptor.parameters`` 中未给出值的一项」，而 ``analyze`` / ``train`` 的派发目标是
**整条流程**（[06-L4](../../../../docs/技术架构-v2/06-L4-多视角推理.md) Deliberation /
[08-L6 §3](../../../../docs/技术架构-v2/08-L6-反思演进.md) 训练对话协议）、非某个 Skill，
故它们没有 ``parameters`` 可问——[06 §2.1](../../../../docs/技术架构-v2/06-L4-多视角推理.md)「询问模式
（快速 / 深度）」与 [08 §3](../../../../docs/技术架构-v2/08-L6-反思演进.md) 的「修正原话」会因此空悬。
本表即该情形的承载者：形态与问项来源规则**逐条同 Skill 侧**（一问一参、取值域取自
``ParameterSpec``、跳过取 ``default``、超预算落默认值并标注来源），只是声明处从 Skill
描述体换成了意图自身。见 [05 §3.2](../../../../docs/技术架构-v2/05-L3-对话主入口.md)
的「无 target Skill 的意图」条（[`T-INT-003`](../../../项目管理/tasks/done/M2/T-INT-003-M2集成关卡多视角决策闭环.md)
交付该机制、[`T-L6-002.1`](../../../项目管理/tasks/T-L6-002.1-训练对话协议.md) 补 ``train`` 一项）。

``train`` 的 ``correction`` 是**用户原话**——它属数据展示（[D-053](../../../项目管理/决策日志.md)），
随确认卡条目的 ``value`` 原样承载、**不过** [01 §6](../../../../docs/技术架构-v2/01-平台共享契约.md)；
L6 侧据此做概念性理解（[08 §3](../../../../docs/技术架构-v2/08-L6-反思演进.md)）。

``investigate`` **同属**无 target Skill 的意图（其派发目标是整条自主查证循环，[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)），
却**刻意不在此声明任何参数**——其循环上界（步数 / LLM 调用次数）是**系统预算、非用户可调项**，
故该意图的确认卡只有「意图」一条目（[05 §3.2](../../../../docs/技术架构-v2/05-L3-对话主入口.md)
的 N ≥ 1 由此满足），**不臆造问项**。要开放端用户可调须先改 [05 §10 的上界口径](../../../../docs/技术架构-v2/05-L3-对话主入口.md)
（见 [L3 册 `A3`](../../../项目管理/遗留问题/L3-遗留问题.md)）。
"""


def _now() -> datetime:
    """用户本地时区当前时刻（01 §8）。"""
    return datetime.now().astimezone()


class IntentDraft(BaseModel):
    """一次意图理解的产物（[05 §3.1](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 的机器可读形态）。

    ``intent is None`` 即**未能收敛**——此时 ``directions`` 必非空（§3.2：不硬猜、
    给方向）。``open_questions`` 是尚缺值的关键参数名（取自目标描述体，见假设 A2）。
    """

    model_config = ConfigDict(frozen=True)

    intent: IntentKind | None = None
    target: str | None = None
    """派发目标（Skill 标识；``query`` 去向指被调用的 Skill，``configure`` 指待配置对象）。"""
    understood: dict[str, Any] = Field(default_factory=dict)
    """已提取的参数值（键对齐描述体 ``parameters`` 的声明名）。"""
    open_questions: tuple[str, ...] = ()
    """尚缺值的关键参数名（澄清问项的候选源）。"""
    directions: tuple[str, ...] = ()
    """未能收敛时的候选方向（本层裁剪到 ≤ :data:`DIRECTION_LIMIT` 条）。"""
    reason: str | None = None
    via_shortcut: str | None = None
    """命中的快捷指令名（§8 短路来源；非空即「预置意图模板」路径）。"""

    @model_validator(mode="after")
    def _draft_shape(self) -> "IntentDraft":
        if self.intent is None and not self.directions:
            raise IntentValidationError(NO_DIRECTIONS_REASON)
        return self


class ClarificationQuestion(BaseModel):
    """一条追问（§3.2：一问一参、可跳过、跳过取默认值）。"""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=64)
    prompt: str = Field(min_length=1)
    default: Any = None
    choices: tuple[str, ...] = ()
    skippable: bool = True


class ClarificationRound(BaseModel):
    """一轮澄清（§3.2）。"""

    model_config = ConfigDict(frozen=True)

    envelope: ResultEnvelope
    intent: IntentKind | None = None
    target: str | None = None
    questions: tuple[ClarificationQuestion, ...] = ()
    deferred: tuple[str, ...] = ()
    """超出澄清预算、以默认值填充的参数名（在确认卡上标注来源）。"""
    directions: tuple[str, ...] = ()
    via_shortcut: str | None = None

    @model_validator(mode="after")
    def _round_shape(self) -> "ClarificationRound":
        if len(self.questions) > CLARIFICATION_BUDGET:
            raise IntentValidationError(
                f"澄清问项不得超过 {CLARIFICATION_BUDGET} 条，得到 {len(self.questions)}"
            )
        if self.directions and len(self.directions) > DIRECTION_LIMIT:
            raise IntentValidationError(
                f"方向候选不得超过 {DIRECTION_LIMIT} 条，得到 {len(self.directions)}"
            )
        return self


class ConfirmationItem(BaseModel):
    """确认卡的一条（「已理解 N 项」的每一项）。

    **生成描述与用户数据分栏**（D-053）：``text`` 是本层生成的文案（过 §6），
    ``value`` 是取值本体（用户数据 / 默认值，原样承载、不过 §6）。
    """

    model_config = ConfigDict(frozen=True)

    param: str | None = None
    text: str = Field(min_length=1)
    value: str = UNSPECIFIED_LABEL
    source: ValueSource

    def line(self) -> str:
        """渲染为一行（生成描述 + 原样值）。"""
        return f"{self.text}：{self.value}"


class IntentConfirmation(BaseModel):
    """意图确认卡（§3.2：用户确认后才执行）。

    ``items`` 即 [05 §5](../../../../docs/技术架构-v2/05-L3-对话主入口.md)
    ``ConfigDraft.understanding_summary`` 的**同一份**内容源（假设 A4），不另造摘要。
    """

    model_config = ConfigDict(frozen=True)

    envelope: ResultEnvelope
    intent: IntentKind
    target: str | None = None
    items: tuple[ConfirmationItem, ...] = ()
    values: dict[str, Any] = Field(default_factory=dict)
    """合并后的参数取值（用户给出 / 指令预填 / 默认值；**不含**无值项）。"""
    confirmed: bool = False

    @model_validator(mode="after")
    def _confirmation_shape(self) -> "IntentConfirmation":
        if not self.items:
            raise IntentValidationError("确认卡至少要有一项理解条目（§3.2）")
        return self

    def header(self) -> str:
        """确认卡抬头（生成文案，过 §6）。"""
        return CONFIRMATION_HEADER.format(n=len(self.items))

    def mark_confirmed(self) -> "IntentConfirmation":
        """把卡置为**已确认**（用户确认动作的落点；§3.2「用户确认后才执行」）。"""
        return checked_confirmation(
            envelope=self.envelope, intent=self.intent, target=self.target,
            items=self.items, values=dict(self.values), confirmed=True,
        )


def checked_draft(**fields: Any) -> IntentDraft:
    """构造一个意图产物（非法 → :class:`IntentValidationError`）。"""
    try:
        return IntentDraft(**fields)
    except ValueError as exc:
        raise IntentValidationError(f"意图产物非法：{exc}") from exc


def checked_question(**fields: Any) -> ClarificationQuestion:
    """构造一条追问（非法 → :class:`IntentValidationError`）。"""
    try:
        return ClarificationQuestion(**fields)
    except ValueError as exc:
        raise IntentValidationError(f"追问非法：{exc}") from exc


def checked_confirmation(**fields: Any) -> IntentConfirmation:
    """构造一张确认卡（非法 → :class:`IntentValidationError`）。"""
    try:
        return IntentConfirmation(**fields)
    except ValueError as exc:
        raise IntentValidationError(f"确认卡非法：{exc}") from exc


class IntentProtocol:
    """意图理解与澄清协议的编排面（[05 §3](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

    :param understander: 注入的理解端口（鸭子类型 ``understand(text) -> ResultEnvelope``）；
        缺省 ``None`` → 文本路径 fail-closed（``unavailable``），**不硬猜**
    :param commands: 快捷指令注册表（§8 短路来源）
    :param matcher: 目标匹配口（鸭子类型 ``match(query) -> SkillDescriptor | None``，
        如 L1 的 ``SkillRunner``）
    :param descriptors: 描述体取数口（鸭子类型 ``get(skill_id) -> SkillDescriptor``，
        如 L1 的 ``SkillRegistry``）——澄清问项与默认值的来源
    :param guard: 中性规则库守卫（缺省用默认规则库；**无**关闭开关）
    """

    def __init__(
        self,
        *,
        understander: Any = None,
        commands: CommandRegistry | None = None,
        matcher: Any = None,
        descriptors: Any = None,
        guard: NeutralityGuard | None = None,
    ) -> None:
        self._understander = understander
        self._commands = commands
        self._matcher = matcher
        self._descriptors = descriptors
        self._guard = guard if guard is not None else NeutralityGuard()

    # ───────────────────────── §3.1 理解 ─────────────────────────

    def understand(self, text: str) -> ResultEnvelope:
        """把一段输入收敛为 :class:`IntentDraft`（载荷即草稿）。

        快捷指令命中即**短路**（取指令的意图模板与参数预填）；否则交理解端口。
        端口异常 → ``dependency_failed``（它是被注入的下游依赖，不吞、不编造）。
        """
        raw = (text or "").strip()
        if not raw:
            return ResultEnvelope.validation_failed("输入为空，无法收敛意图（05 §3.1）")
        if self._commands is not None and raw.startswith(COMMAND_PREFIX):
            return self._from_command(raw)
        if self._understander is None:
            return ResultEnvelope.unavailable(
                UNDERSTANDER_ABSENT_REASON, last_updated_at=_now()
            )
        try:
            result = self._understander.understand(raw)
        except Exception as exc:  # 注入端口的实现缺陷 → 显式失败，不静默降级
            return ResultEnvelope.dependency_failed(f"意图理解端口异常：{exc}")
        if not isinstance(result, ResultEnvelope):
            return ResultEnvelope.dependency_failed(
                f"意图理解端口返回非法类型 {type(result).__name__}（须为 ResultEnvelope）"
            )
        if result.status != "ok":
            return result
        if not isinstance(result.data, IntentDraft):
            return ResultEnvelope.dependency_failed(
                "意图理解端口返回的载荷不是 IntentDraft"
            )
        return ResultEnvelope.ok(self._filled(result.data), as_of=result.as_of)

    def _from_command(self, raw: str) -> ResultEnvelope:
        """§8 短路：命中指令直接取意图模板与参数预填（已预填参数不再追问）。"""
        assert self._commands is not None
        resolution = self._commands.resolve(raw)
        if resolution.envelope.status != "ok" or resolution.command is None:
            return resolution.envelope
        command = resolution.command
        target = self._match(f"{command.name} {command.description}")
        specs = self._params_for(command.intent, target)
        open_questions = tuple(
            s.name for s in specs if s.name not in command.param_defaults
        )
        return ResultEnvelope.ok(checked_draft(
            intent=command.intent, target=target,
            understood=dict(command.param_defaults),
            open_questions=open_questions, via_shortcut=command.name,
        ))

    def _filled(self, draft: IntentDraft) -> IntentDraft:
        """补齐 ``open_questions``（理解端口可只给 target 与已提取值）。

        参数源含**意图级声明**（[`INTENT_PARAM_SPECS`]）——故 `analyze` 这类无
        ``target`` 的意图也能得到 ``mode`` 问项，而非因「无目标 Skill」被静默略过。
        """
        specs = self._params_for(draft.intent, draft.target)
        if draft.open_questions or not specs:
            return draft
        return checked_draft(
            intent=draft.intent, target=draft.target,
            understood=dict(draft.understood),
            open_questions=tuple(
                s.name for s in specs if s.name not in draft.understood
            ),
            directions=draft.directions, reason=draft.reason,
            via_shortcut=draft.via_shortcut,
        )

    def _match(self, query: str) -> str | None:
        if self._matcher is None:
            return None
        try:
            found = self._matcher.match(query)
        except Exception:
            return None
        return getattr(found, "skill_id", None)

    def _specs(self, skill_id: str | None) -> tuple[ParameterSpec, ...]:
        if skill_id is None or self._descriptors is None:
            return ()
        try:
            descriptor = self._descriptors.get(skill_id)
        except Exception:
            return ()
        return tuple(getattr(descriptor, "parameters", ()) or ())

    def _params_for(
        self, intent: IntentKind | None, skill_id: str | None
    ) -> tuple[ParameterSpec, ...]:
        """澄清 / 补值的**参数源**：意图级声明 ∪ 目标 Skill 的 ``parameters``。

        同名时 **Skill 侧覆盖**意图级声明（描述体是更具体的来源）；顺序取声明在先、
        故问项次序稳定（`analyze` 的 `mode` 恒为首问）。见 [`INTENT_PARAM_SPECS`]。
        """
        ordered = {s.name: s for s in INTENT_PARAM_SPECS.get(intent or "", ())}
        for spec in self._specs(skill_id):
            ordered[spec.name] = spec
        return tuple(ordered.values())

    # ───────────────────────── §3.2 澄清 ─────────────────────────

    def clarify(self, draft: IntentDraft) -> ClarificationRound:
        """产一轮追问（预算 ≤3；超预算者以默认值填充并记入 ``deferred``）。

        未能收敛（``intent is None``）时不产问项，改产方向候选（§3.2）。
        """
        if draft.intent is None:
            directions = self._checked_texts(
                tuple(draft.directions[:DIRECTION_LIMIT]), DIRECTIONS_HEADER
            )
            return ClarificationRound(
                envelope=ResultEnvelope.empty(
                    draft.reason or DIRECTIONS_HEADER, as_of=_now()
                ),
                directions=directions,
            )
        specs = {s.name: s for s in self._params_for(draft.intent, draft.target)}
        asked = draft.open_questions[:CLARIFICATION_BUDGET]
        deferred = tuple(
            n for n in draft.open_questions[CLARIFICATION_BUDGET:] if n in specs
        )
        questions = tuple(
            self._question(specs[name], name) for name in asked if name in specs
        )
        return ClarificationRound(
            envelope=ResultEnvelope.ok(list(questions)) if questions
            else ResultEnvelope.empty("该意图下没有待澄清的关键参数", as_of=_now()),
            intent=draft.intent, target=draft.target,
            questions=questions, deferred=deferred,
            via_shortcut=draft.via_shortcut,
        )

    def _question(self, spec: ParameterSpec, name: str) -> ClarificationQuestion:
        prompt = self._checked_texts((f"参数 {spec.name}：{spec.description}",))[0]
        return checked_question(
            name=name, prompt=prompt, default=spec.default,
            choices=tuple(spec.choices), skippable=True,
        )

    # ───────────────────────── §3.2 确认卡 ───────────────────────

    def confirm(
        self,
        draft: IntentDraft,
        answers: Mapping[str, Any] | None = None,
    ) -> IntentConfirmation:
        """收敛为意图确认卡（§3.2）。

        ``answers`` 为对追问的回答；缺键或值 ＝ :data:`SKIP` 即「跳过」，取默认值。
        无默认值的未指定项**不进** ``values``（沿用 Skill 自身默认），但在卡上
        以「未指定」显式呈现，不静默略过。
        """
        if draft.intent is None:
            raise IntentValidationError(NO_DIRECTIONS_REASON)
        given = dict(answers or {})
        specs = {s.name: s for s in self._params_for(draft.intent, draft.target)}
        values: dict[str, Any] = {}
        items: list[ConfirmationItem] = [self._intent_item(draft)]

        for name, value in draft.understood.items():
            values[name] = value
            items.append(self._item(specs.get(name), name, value,
                                    "shortcut" if draft.via_shortcut else "stated"))

        asked = tuple(n for n in draft.open_questions[:CLARIFICATION_BUDGET] if n in specs)
        deferred = tuple(n for n in draft.open_questions[CLARIFICATION_BUDGET:] if n in specs)
        for name in (*asked, *deferred):
            spec = specs[name]
            answer = given.get(name, SKIP)
            if answer is None or answer == SKIP:
                if spec.default is not None:
                    values[name] = spec.default
                items.append(self._item(spec, name, _as_text(spec.default), "default"))
            else:
                values[name] = answer
                items.append(self._item(spec, name, _as_text(answer), "stated"))

        return checked_confirmation(
            envelope=ResultEnvelope.ok(list(items), as_of=_now()),
            intent=draft.intent, target=draft.target,
            items=tuple(items), values=values, confirmed=False,
        )

    def _intent_item(self, draft: IntentDraft) -> ConfirmationItem:
        """确认卡的第 ① 项：意图本身与它的派发目标（N ≥ 1 的兜底）。"""
        assert draft.intent is not None
        text = self._checked_texts(
            (f"意图 {draft.intent} · {INTENT_TARGETS[draft.intent]}",)
        )[0]
        return ConfirmationItem(
            text=text, value=draft.target or UNSPECIFIED_LABEL, source="intent"
        )

    def _item(
        self, spec: ParameterSpec | None, name: str, value: Any, source: ValueSource
    ) -> ConfirmationItem:
        label = SOURCE_LABELS[source]
        prompt = spec.description if spec is not None else None
        text = self._checked_texts(
            (f"参数 {name}（{prompt}）" if prompt else f"参数 {name}",)
        )[0]
        return ConfirmationItem(
            param=name, text=f"{text} · {label}", value=_as_text(value), source=source
        )

    def _checked_texts(
        self, texts: tuple[str, ...], header: str | None = None
    ) -> tuple[str, ...]:
        """逐条过 [01 §6](../../../../docs/技术架构-v2/01-平台共享契约.md) 执行点 2；命中即阻断。

        红-绿已验：把本方法改为直通时，「含第一人称的生成文案被阻断」用例 FAIL。
        """
        targets = (*texts, *((header,) if header else ()))
        for text in targets:
            verdict = self._guard.check_output(text)
            if not verdict.passed:
                hits = "、".join(f"{f.kind}:{f.matched}" for f in verdict.findings)
                raise IntentValidationError(
                    f"生成文案未过中性校验（{hits}）：{text!r}", findings=tuple(hits.split("、"))
                )
        return texts


def _as_text(value: Any) -> str:
    """取值 → 展示字符串（``None`` 即「未指定」，不着色为具体值）。"""
    return UNSPECIFIED_LABEL if value is None else str(value)


# ───────────────────────── LLM 理解实现（假设 A1） ─────────────────────────

_JSON_CONTRACT = (
    '{"intent": <下列枚举之一或 null>, "target": <Skill 标识串或 null>, '
    '"understood": {<参数名>: <值>}, "open_questions": [<参数名>...], '
    '"directions": [<方向描述>...]}'
)

_UNKNOWN_DRAFT_REASON = "意图理解端点返回无法解析为结构化意图结果"


def llm_understanding_prompt(text: str) -> str:
    """构造意图理解的结构化提示词（实现细节，非契约）。

    只此一份提示词，不在别处再写第二版；枚举取自 :data:`INTENT_KINDS`（机器可读
    副本），故契约增删意图类型时此处自动跟随。
    """
    kinds = " / ".join(INTENT_KINDS)
    return (
        "任务：把用户输入归入下列意图类型之一，并抽取其中的参数。\n"
        f"意图类型枚举：{kinds}。\n"
        f"仅输出一行 JSON，结构为：{_JSON_CONTRACT}\n"
        "规则：无法确定意图时 intent 置 null，并在 directions 给出至多 3 个可能的"
        "方向；不得输出 JSON 以外的任何字符。\n"
        f"用户输入：{text}"
    )


def parse_understanding(raw: str) -> ResultEnvelope:
    """把端点回的文本解析为 :class:`IntentDraft` 信封。

    解析失败 → ``dependency_failed``（端点是下游依赖，不吞错、不编造）。
    """
    body = (raw or "").strip()
    if body.startswith("```"):
        body = body.strip("`")
        body = body.split("\n", 1)[1] if "\n" in body else ""
    try:
        payload = json.loads(body)
    except (ValueError, TypeError) as exc:
        return ResultEnvelope.dependency_failed(f"{_UNKNOWN_DRAFT_REASON}：{exc}")
    if not isinstance(payload, dict):
        return ResultEnvelope.dependency_failed(f"{_UNKNOWN_DRAFT_REASON}：顶层不是对象")
    intent = payload.get("intent")
    if intent is not None and intent not in INTENT_KINDS:
        return ResultEnvelope.dependency_failed(
            f"{_UNKNOWN_DRAFT_REASON}：未知意图 {intent!r}（须为 {INTENT_KINDS} 之一或 null）"
        )
    try:
        draft = IntentDraft(
            intent=intent,
            target=_opt_str(payload.get("target")),
            understood=_as_mapping(payload.get("understood")),
            open_questions=tuple(
                str(n) for n in (payload.get("open_questions") or ())
            ),
            directions=tuple(str(d) for d in (payload.get("directions") or ())),
        )
    except ValueError as exc:
        return ResultEnvelope.dependency_failed(f"{_UNKNOWN_DRAFT_REASON}：{exc}")
    return ResultEnvelope.ok(draft)


def _opt_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


class LlmIntentUnderstander:
    """经注入 ``LlmClient`` 的默认理解实现（假设 A1）。

    失败一律走信封（[02 §4](../../../../docs/技术架构-v2/02-L0-本地优先基座.md) 的
    ``unavailable`` / ``failed`` / ``dependency_failed`` 原样透出）——端点不可用时
    用户侧看到的是**显式降级告知**（[05 §4](../../../../docs/技术架构-v2/05-L3-对话主入口.md)），
    不是静默失败。明文 prompt 不落盘（LlmClient 的口径）。
    """

    def __init__(
        self,
        llm: Any,
        endpoint_id: str,
        *,
        initiator: str = "l3-intent",
        purpose: str = "意图理解",
    ) -> None:
        self._llm = llm
        self._endpoint_id = endpoint_id
        self._initiator = initiator
        self._purpose = purpose

    def understand(self, text: str) -> ResultEnvelope:
        """调用端点并解析结果（事件流：``chunk*`` → ``done``，或单个 ``error``）。"""
        chunks: list[str] = []
        events = self._llm.invoke(
            self._endpoint_id, llm_understanding_prompt(text),
            initiator=self._initiator, purpose=self._purpose,
        )
        for event in events:
            if event.kind == "chunk":
                chunks.append(event.text)
            elif event.kind == "error" and event.error_envelope is not None:
                return event.error_envelope
        return parse_understanding("".join(chunks))
