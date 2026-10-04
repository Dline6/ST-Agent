"""任务派发总线（[05 §4](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

意图确认后派发执行：**所有派发产生 `trace_id`**（[01 §4](../../../../docs/技术架构-v2/01-平台共享契约.md)），
按 [§3.1](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 的六类去向路由，结果以
[01 §5](../../../../docs/技术架构-v2/01-平台共享契约.md) 的 ``ResultEnvelope`` 语义渲染。

**去向与接入态**（假设 A1）：六类去向逐一登记接入态。**尚未接入的去向不得伪造
执行、也不得静默丢弃**——按 ``unavailable`` 呈现且 `reason` **点名归属任务**
（同 [`card.py`](../home/card.py)「生产方未接入即 `unavailable` + 点名」的先例）。
本批接 ``query``（经注入的 L1 ``SkillRunner``）、``explain``（把调用方给的链
作为载荷返回；**展开渲染**归 [`T-L3-004`](../../../项目管理/tasks/T-L3-004-GenerativeUI推理链可视化.md)）
与 ``configure``（经注入的**配置草稿生成面**，见 [`l3.config.draft`](../config/draft.py)；
草稿的**落值与处置三态**归 [`T-L3-003.2`](../../../项目管理/tasks/T-L3-003.2-双通道登记面与处置三态.md)）
与 ``memory_op``（经注入的**冲突裁决面**，见 [`l3.conflict.adjudication`](../conflict/adjudication.py)；
裁决的承接 / 提交归 [`T-L3-005.1`](../../../项目管理/tasks/T-L3-005.1-冲突裁决承接事件与memory_op去向接线.md)，
其**偏好写入支**归 L2 写入面、组合根装配归 [`T-INT-002`](../../../项目管理/tasks/T-INT-002-M1集成关卡首次可对话.md)）。
``analyze`` 由 [`T-INT-003`](../../../项目管理/tasks/T-INT-003-M2集成关卡多视角决策闭环.md)（M2 关卡）接入——
经注入的**多视角编排面**触发 [06-L4](06-L4-多视角推理.md) Deliberation；因 [铁律 7](../../../项目管理/工程宪法.md)
禁 L3 import L4，该去向的载荷按**鸭子面**透出（本层只认其中的 `ResultEnvelope`，
其余由渲染方按属性取值），见 :attr:`DispatchOutcome.analysis`。

**`trace_id` 从哪来**（假设 A2）：[01 §1](../../../../docs/技术架构-v2/01-平台共享契约.md)
登记其产生方为「L1 调度器」，故 ``query`` 去向的链**由 L1 产**、本层经
``RunOutcome.trace`` 取用；未接入去向无 Skill 执行，其链为**空链**（01 §4 的五类
``step_type`` 无「派发」一类，**不伪造步骤**）——链存在、步骤为空，如实反映
「本次派发没有发生可追溯的动作」。``analyze`` 去向例外：其链取自**对照链**
（L4 的 `aggregation` 步 + `divergence_map` 结论锚，[06 §3](../../../docs/技术架构-v2/06-L4-多视角推理.md)）——
本次派发**确实**发生了可追溯的动作，故如实关联而非留空链。

**LLM 降级**（假设 A4）：真正调 LLM 的在 [`.1`](../intent/protocol.py) 的理解端口，
总线自身无 LLM 调用。故本模块只规定其**渲染口径**——``unavailable`` 若源于 LLM
路径（``llm_degraded=True``）则附**中性降级告知**，否则只是普通数据延迟；
**不依赖 LLM 的去向（``query``）不受牵连**，照常执行。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, model_validator

from st_agent.contracts.identifiers import TraceId
from st_agent.contracts.result_envelope import EnvelopeStatus, ResultEnvelope
from st_agent.contracts.trace import Trace
from st_agent.l3.commands.registry import INTENT_KINDS, IntentKind
from st_agent.l3.conflict.adjudication import (
    QUEUE_ABSENT_REASON,
    ConflictAdjudication,
)
from st_agent.l3.config.draft import CONFIG_DRAFT_ABSENT_REASON
from st_agent.l3.errors import IntentValidationError
from st_agent.l3.intent.protocol import IntentConfirmation

__all__ = [
    "ANALYZE_ABSENT_REASON",
    "DISPATCH_INITIATOR",
    "DISPATCH_PURPOSE",
    "LLM_DEGRADED_NOTICE",
    "ROUTE_BY_INTENT",
    "ROUTE_SPECS",
    "DispatchBus",
    "DispatchOutcome",
    "RouteSpec",
    "UiSemantics",
    "render_semantics",
]

DISPATCH_INITIATOR = "l3-dispatch"
DISPATCH_PURPOSE = "对话意图派发"

ANALYZE_ABSENT_REASON = "未注入多视角编排面，无法派发 analyze 去向（05 §4）"

LLM_DEGRADED_NOTICE = (
    "云端 LLM 端点暂不可用；可改用本地推理模式（能力受限），或稍后重试。"
)
"""LLM 端点不可用时的降级告知（§4；Story 原文措辞按铁律 2 中性改述）。"""

Presentation = Literal["normal", "empty_state", "delayed", "error", "input_error"]


class RouteSpec(BaseModel):
    """一类去向的登记（§3.1 的派发目标 + 本批的接入态）。"""

    model_config = ConfigDict(frozen=True)

    intent: IntentKind
    wired: bool
    owner: str | None = None
    """未接入时的**归属任务 id**（`reason` 点名用）。"""
    note: str = ""

    @model_validator(mode="after")
    def _route_shape(self) -> "RouteSpec":
        if not self.wired and not (self.owner or "").strip():
            raise IntentValidationError(
                f"去向 {self.intent} 未接入时必须点名归属任务（不静默留悬）"
            )
        return self


ROUTE_SPECS: tuple[RouteSpec, ...] = (
    RouteSpec(
        intent="query", wired=True,
        note="经注入的 L1 SkillRunner.run 执行，信封原样透出",
    ),
    RouteSpec(
        intent="configure", wired=True,
        note="经注入的配置草稿生成面产出 ConfigDraft（05 §5）；落值与三态归 T-L3-003.2",
    ),
    RouteSpec(
        intent="analyze", wired=True,
        note="经注入的多视角编排面触发 06-L4 Deliberation（载荷按鸭子面透出，L3 不 import L4）",
    ),
    RouteSpec(
        intent="memory_op", wired=True,
        note="经注入的冲突裁决面产出裁决卡（05 §9）；偏好写入支归 L2 写入面（04 §3.2），装配归 T-INT-002",
    ),
    RouteSpec(
        intent="train", wired=False, owner="T-L6-002",
        note="训练对话协议与主动提案（08 §3–§4）",
    ),
    RouteSpec(
        intent="explain", wired=True,
        note="把调用方给的推理链作为载荷返回；展开渲染归 T-L3-004",
    ),
)
"""六类去向的登记（[05 §3.1](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 分类表逐行对应、同序）。"""

ROUTE_BY_INTENT: dict[str, RouteSpec] = {spec.intent: spec for spec in ROUTE_SPECS}
assert set(ROUTE_BY_INTENT) == set(INTENT_KINDS), (
    "路由表必须与 05 §3.1 的六类意图逐项对应（缺项即契约漂移）"
)


class UiSemantics(BaseModel):
    """一条信封状态的渲染语义（§4 结果渲染语义表的机器可读形态）。"""

    model_config = ConfigDict(frozen=True)

    status: str
    presentation: Presentation
    must_show: tuple[str, ...] = ()
    """该态必展示的项（原因 / 最后更新时间 / 日志入口）。"""
    notice: str | None = None
    """附加告知（如 LLM 降级）；缺省无。"""


RENDER_SEMANTICS: dict[str, UiSemantics] = {
    "ok": UiSemantics(status="ok", presentation="normal"),
    "empty": UiSemantics(status="empty", presentation="empty_state",
                         must_show=("原因",)),
    "unavailable": UiSemantics(status="unavailable", presentation="delayed",
                               must_show=("最后更新时间", "原因")),
    "dependency_failed": UiSemantics(status="dependency_failed", presentation="error",
                                     must_show=("原因", "日志入口")),
    "failed": UiSemantics(status="failed", presentation="error",
                          must_show=("原因", "日志入口")),
    "validation_failed": UiSemantics(status="validation_failed", presentation="input_error",
                                     must_show=("理由",)),
}
"""六态渲染语义（§4）。``validation_failed`` 一行系 2026-09-28 补入——§4 原枚举遗漏该态。"""

assert set(RENDER_SEMANTICS) == set(get_args(EnvelopeStatus)), (
    "渲染语义表必须穷尽 01 §5 的六态（缺态即拒绝，见 §4）"
)


def render_semantics(envelope: ResultEnvelope, *, llm_degraded: bool = False) -> UiSemantics:
    """取一个信封的渲染语义（§4）。

    ``unavailable`` 且 ``llm_degraded`` 时附 :data:`LLM_DEGRADED_NOTICE`——**显式降级
    告知**而非静默失败；其余态不带告知。
    """
    base = RENDER_SEMANTICS[envelope.status]
    notice = (
        LLM_DEGRADED_NOTICE
        if llm_degraded and envelope.status == "unavailable"
        else None
    )
    return base.model_copy(update={"notice": notice})


class DispatchOutcome(BaseModel):
    """一次派发的产出（信封 + 链定位 + 去向台账）。"""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    envelope: ResultEnvelope
    trace_id: str
    intent: IntentKind
    route: IntentKind
    wired: bool
    owner: str | None = None
    run: Any = None
    """``query`` 去向的 ``RunOutcome``；其余去向为 ``None``。"""
    draft: Any = None
    """``configure`` 去向的草稿载荷（:class:`~st_agent.l3.config.draft.ConfigDraft`
    或工作流分支的 ``WorkflowDraft``）；其余去向为 ``None``。"""
    adjudications: tuple[ConflictAdjudication, ...] = ()
    """``memory_op`` 去向的裁决卡（[05 §9](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）；
    其余去向为空元组。**渲染**该卡的归 [`T-L3-004`](../../../项目管理/tasks/T-L3-004-GenerativeUI推理链可视化.md)。"""
    analysis: Any = None
    """``analyze`` 去向的**多视角载荷**（[05 §3.1](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 的
    「触发 06-L4 Deliberation」）；其余去向为 `None`。

    **鸭子类型**（本层不 import L4，[铁律 7](../../../项目管理/工程宪法.md)；`LAYER_ORDER` 为
    `l3 < l4`）：只要求含 `envelope`（本层已核为 `ResultEnvelope`）；视图 / 观点 / 链
    等 L4 产物由**渲染方按属性取值**（同 [`describe_divergence_map`](../render/describe.py)
    消费 L4 视图的先例）。"""
    trace: Trace | None = None
    """本次派发关联的链（``explain`` 去向即其载荷；未接入去向为**空链**）。"""


def _now() -> datetime:
    """用户本地时区当前时刻（01 §8）。"""
    return datetime.now().astimezone()


def _as_cards(payload: Any) -> tuple[ConflictAdjudication, ...] | None:
    """裁决面载荷 → 裁决卡序列（形状不合即 ``None``，由调用方显式失败）。

    卡可以是单张（``card_for``）或一序列（``cards``）；**不静默过滤**非卡元素。
    """
    if isinstance(payload, ConflictAdjudication):
        return (payload,)
    if isinstance(payload, (tuple, list)):
        if all(isinstance(p, ConflictAdjudication) for p in payload):
            return tuple(payload)
        return None
    return None


class DispatchBus:
    """派发总线（[05 §4](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

    :param runner: 注入的 Skill 执行面（鸭子类型 ``run(...) -> RunOutcome``，
        如 L1 的 ``SkillRunner``）；缺省 ``None`` → ``query`` 去向 fail-closed
        （``unavailable``），**不臆测**
    :param configs: 注入的配置草稿生成面（鸭子类型
        ``generate(confirmation, *, structure=None) -> ResultEnvelope``，如
        :class:`~st_agent.l3.config.draft.ConfigDraftProtocol`）；缺省 ``None`` →
        ``configure`` 去向 fail-closed（``unavailable`` + 点名），**不臆测**
    :param adjudications: 注入的冲突裁决面（鸭子类型
        ``card_for(conflict_id) -> ResultEnvelope`` / ``cards() -> ResultEnvelope``，
        如 :class:`~st_agent.l3.conflict.adjudication.ConflictAdjudicator`）；
        缺省 ``None`` → ``memory_op`` 去向 fail-closed（``unavailable`` + 点名），**不臆测**
    :param deliberations: 注入的多视角编排面（鸭子类型
        ``analyze(confirmation, *, values=None, now=None) -> 含 envelope 的载荷``，
        如 L4 的 ``AnalyzeService``——组合根注入，本层不 import L4）；
        缺省 ``None`` → ``analyze`` 去向 fail-closed（``unavailable`` + 点名），**不臆测**
    """

    def __init__(
        self, *, runner: Any = None, configs: Any = None, adjudications: Any = None,
        deliberations: Any = None,
    ) -> None:
        self._runner = runner
        self._configs = configs
        self._adjudications = adjudications
        self._deliberations = deliberations

    def dispatch(
        self,
        confirmation: IntentConfirmation,
        *,
        values: Mapping[str, Any] | None = None,
        inputs: Mapping[str, Any] | None = None,
        approved_permissions: tuple[str, ...] = (),
        trace: Trace | None = None,
        structure: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> DispatchOutcome:
        """派发一张**已确认**的意图确认卡（§4）。

        :param values: 覆盖确认卡上的参数取值；缺省用 ``confirmation.values``
        :param inputs: 对象类输入（[01 §2](../../../../docs/技术架构-v2/01-平台共享契约.md) 的通道之②，转交 L1）
        :param trace: ``explain`` 去向要展开的链
        :param structure: ``configure`` 去向的工作流结构载荷——**非空即工作流分支**
            （[05 §5](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 的判定只有这一处）
        :param now: 本次派发时刻（缺省取本机当前带时区时间）
        """
        moment = now if now is not None else _now()
        spec = ROUTE_BY_INTENT[confirmation.intent]
        if not confirmation.confirmed:
            chain = Trace(trace_id=TraceId.generate())
            return self._outcome(
                ResultEnvelope.validation_failed(
                    "意图确认卡未经用户确认，拒绝派发（05 §3.2）"),
                chain, spec,
            )
        if confirmation.intent == "query":
            return self._dispatch_query(
                confirmation, spec, values, inputs, approved_permissions, trace, moment)
        if confirmation.intent == "explain":
            return self._dispatch_explain(spec, trace, moment)
        if confirmation.intent == "configure":
            return self._dispatch_configure(confirmation, spec, structure, moment)
        if confirmation.intent == "memory_op":
            return self._dispatch_memory_op(confirmation, spec, moment)
        if confirmation.intent == "analyze":
            return self._dispatch_analyze(confirmation, spec, values, moment)
        return self._pending(spec, moment)

    # ───────────────────────── 去向实现 ─────────────────────────

    def _dispatch_query(
        self,
        confirmation: IntentConfirmation,
        spec: RouteSpec,
        values: Mapping[str, Any] | None,
        inputs: Mapping[str, Any] | None,
        approved_permissions: tuple[str, ...],
        trace: Trace | None,
        moment: datetime,
    ) -> DispatchOutcome:
        chain = Trace(trace_id=TraceId.generate())
        if not (confirmation.target or "").strip():
            return self._outcome(
                ResultEnvelope.validation_failed(
                    "query 去向缺少目标 Skill 标识（无法派发，05 §3.1）"),
                chain, spec,
            )
        if self._runner is None:
            return self._outcome(
                ResultEnvelope.unavailable(
                    "未注入 Skill 执行面，无法派发 query 去向（05 §4）",
                    last_updated_at=moment,
                ),
                chain, spec,
            )
        run = self._runner.run(
            confirmation.target,
            dict(values) if values is not None else dict(confirmation.values),
            inputs=dict(inputs or {}),
            trace=trace,
            approved_permissions=approved_permissions,
            initiator=DISPATCH_INITIATOR, purpose=DISPATCH_PURPOSE,
        )
        # 信封**原样透出**（GWT-2）：不重包、不改 status、不吞 reason
        return DispatchOutcome(
            envelope=run.envelope,
            trace_id=run.trace.trace_id.value,
            intent=confirmation.intent, route=confirmation.intent,
            wired=spec.wired, run=run, trace=run.trace,
        )

    def _dispatch_explain(
        self, spec: RouteSpec, trace: Trace | None, moment: datetime
    ) -> DispatchOutcome:
        if trace is None:
            chain = Trace(trace_id=TraceId.generate())
            return self._outcome(
                ResultEnvelope.empty(
                    "本次派发未提供可展开的推理链（explain 去向的载荷即 Trace）",
                    as_of=moment,
                ),
                chain, spec,
            )
        return DispatchOutcome(
            envelope=ResultEnvelope.ok(trace, as_of=moment),
            trace_id=trace.trace_id.value, intent="explain", route="explain",
            wired=spec.wired, trace=trace,
        )

    def _dispatch_configure(
        self,
        confirmation: IntentConfirmation,
        spec: RouteSpec,
        structure: Mapping[str, Any] | None,
        moment: datetime,
    ) -> DispatchOutcome:
        """``configure`` 去向：经注入的草稿生成面产出草稿（§5）。

        未注入生成面 → ``unavailable`` + 点名（同 ``query`` 去向「未注入 Skill
        执行面」的写法），**不伪造**；生成面的失败信封（``validation_failed`` /
        ``unavailable`` 等）**原样透出**——本层不重包、不改 status、不吞 reason。
        """
        chain = Trace(trace_id=TraceId.generate())
        if self._configs is None:
            return self._outcome(
                ResultEnvelope.unavailable(
                    CONFIG_DRAFT_ABSENT_REASON, last_updated_at=moment
                ),
                chain, spec,
            )
        outcome_result = self._configs.generate(confirmation, structure=structure)
        if not isinstance(outcome_result, ResultEnvelope):
            return self._outcome(
                ResultEnvelope.dependency_failed(
                    "配置草稿生成面返回非法类型 "
                    f"{type(outcome_result).__name__}（须为 ResultEnvelope）"
                ),
                chain, spec,
            )
        if outcome_result.status != "ok":
            return self._outcome(outcome_result, chain, spec)
        return DispatchOutcome(
            envelope=outcome_result,
            trace_id=chain.trace_id.value,
            intent=confirmation.intent, route=confirmation.intent,
            wired=spec.wired, draft=outcome_result.data, trace=chain,
        )

    def _dispatch_memory_op(
        self,
        confirmation: IntentConfirmation,
        spec: RouteSpec,
        moment: datetime,
    ) -> DispatchOutcome:
        """``memory_op`` 去向：经注入的冲突裁决面产出裁决卡（§9）。

        确认卡的 ``target`` 非空即视为**指定冲突 id**（只取那一张），否则列出
        **全部待裁决**项。未注入裁决面 → ``unavailable`` + 点名（同 ``query``
        去向「未注入 Skill 执行面」的写法），**不伪造**；裁决面的失败信封
        （``empty`` / ``validation_failed`` / ``unavailable``）**原样透出**。

        本次派发的链为**空链**（同 ``configure`` 去向）——裁决卡的推理锚点是
        提案自带的 ``trace_id``（[04 §1](../../../../docs/技术架构-v2/04-L2-记忆图谱.md)），
        本层不伪造派发步骤。
        """
        chain = Trace(trace_id=TraceId.generate())
        if self._adjudications is None:
            return self._outcome(
                ResultEnvelope.unavailable(QUEUE_ABSENT_REASON, last_updated_at=moment),
                chain, spec,
            )
        target = (confirmation.target or "").strip()
        envelope = (
            self._adjudications.card_for(target) if target
            else self._adjudications.cards()
        )
        if not isinstance(envelope, ResultEnvelope):
            return self._outcome(
                ResultEnvelope.dependency_failed(
                    "冲突裁决面返回非法类型 "
                    f"{type(envelope).__name__}（须为 ResultEnvelope）"
                ),
                chain, spec,
            )
        if envelope.status != "ok":
            return self._outcome(envelope, chain, spec)
        cards = _as_cards(envelope.data)
        if cards is None:
            return self._outcome(
                ResultEnvelope.dependency_failed(
                    "冲突裁决面返回的载荷不是裁决卡"
                    "（须为 ConflictAdjudication 或其一序列）"
                ),
                chain, spec,
            )
        return DispatchOutcome(
            envelope=envelope,
            trace_id=chain.trace_id.value,
            intent=confirmation.intent, route=confirmation.intent,
            wired=spec.wired, adjudications=cards, trace=chain,
        )

    def _dispatch_analyze(
        self,
        confirmation: IntentConfirmation,
        spec: RouteSpec,
        values: Mapping[str, Any] | None,
        moment: datetime,
    ) -> DispatchOutcome:
        """``analyze`` 去向：经注入的多视角编排面触发 L4 Deliberation（§3.1）。

        未注入编排面 → ``unavailable`` + 原因（同 ``query`` 去向「未注入 Skill 执行面」
        的写法），**不伪造**；编排面返回的载荷按**鸭子面**消费——只核其 ``envelope``
        为 ``ResultEnvelope``（形状不合即 ``dependency_failed``），其余字段原样承载
        给渲染方（本层不 import L4）。编排面的失败信封（``empty`` / ``unavailable`` /
        ``validation_failed``）**原样透出**，不重包、不改 status、不吞 reason。

        链取自载荷里的**对照链**（若给出）——本次派发确有可追溯动作（[06 §3](../../../docs/技术架构-v2/06-L4-多视角推理.md)
        的 ``aggregation`` 步 + `divergence_map` 结论锚）；编排未跑成时退为空链
        （不伪造步骤，同其余去向）。
        """
        chain = Trace(trace_id=TraceId.generate())
        if self._deliberations is None:
            return self._outcome(
                ResultEnvelope.unavailable(ANALYZE_ABSENT_REASON, last_updated_at=moment),
                chain, spec,
            )
        payload = self._deliberations.analyze(confirmation, values=values, now=moment)
        envelope = getattr(payload, "envelope", None)
        if not isinstance(envelope, ResultEnvelope):
            return self._outcome(
                ResultEnvelope.dependency_failed(
                    "多视角编排面返回非法结构（须含 envelope: ResultEnvelope）："
                    f"{type(payload).__name__}"
                ),
                chain, spec,
            )
        trace = getattr(getattr(payload, "map", None), "trace", None)
        if not isinstance(trace, Trace):
            trace = chain
        return DispatchOutcome(
            envelope=envelope, trace_id=trace.trace_id.value,
            intent=confirmation.intent, route=confirmation.intent,
            wired=spec.wired, analysis=payload, trace=trace,
        )

    def _pending(self, spec: RouteSpec, moment: datetime) -> DispatchOutcome:
        """未接入去向：``unavailable`` + 原因 + **归属任务 id**（不伪造、不静默丢）。

        ``last_updated_at`` 取派发时刻，并在 `reason` 里**明说该时刻不代表数据
        截止时间**——下游未接入时没有任何数据落地，不许冒充「最后更新时间 T」。
        """
        chain = Trace(trace_id=TraceId.generate())
        reason = (
            f"去向「{spec.intent}」尚未接入（归属 {spec.owner}：{spec.note}）；"
            f"本次派发记录时刻 {moment.isoformat()}，该时刻不代表数据截止时间"
        )
        return self._outcome(
            ResultEnvelope.unavailable(reason, last_updated_at=moment),
            chain, spec,
        )

    # ───────────────────────── 内部 ─────────────────────────

    def _outcome(
        self,
        envelope: ResultEnvelope,
        chain: Trace,
        spec: RouteSpec,
    ) -> DispatchOutcome:
        return DispatchOutcome(
            envelope=envelope, trace_id=chain.trace_id.value,
            intent=spec.intent, route=spec.intent, wired=spec.wired,
            owner=spec.owner, trace=chain,
        )
