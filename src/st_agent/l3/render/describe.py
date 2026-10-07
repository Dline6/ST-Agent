"""L3 特有组件的**描述件**（[05 §6](../../../../docs/技术架构-v2/05-L3-对话主入口.md) / [§7](../../../../docs/技术架构-v2/05-L3-对话主入口.md) / [§9](../../../../docs/技术架构-v2/05-L3-对话主入口.md)；[01 §12](../../../../docs/技术架构-v2/01-平台共享契约.md)）。

| 描述件 | 组件类型 | 输入（上游已交付的视图数据） |
| --- | --- | --- |
| :func:`describe_trace` | ``trace_timeline`` | [`contracts.trace.Trace`](../../contracts/trace.py)（[05 §7](../../../../docs/技术架构-v2/05-L3-对话主入口.md)） |
| :func:`describe_agent_run` | ``table`` | **自主查证循环的产出**（[`l3.runtime.loop.LoopOutcome`](../runtime/loop.py)，[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）——**鸭子类型**：本层只按属性取值 |
| :func:`describe_context_card` | ``context_card`` | [`l3.home.card.ContextCard`](../home/card.py)（[05 §2](../../../../docs/技术架构-v2/05-L3-对话主入口.md)） |
| :func:`describe_draft` | ``config_draft_card`` | [`l3.config.draft.ConfigDraft`](../config/draft.py) / [`l3.config.handling.PanelView`](../config/handling.py)（[05 §5](../../../../docs/技术架构-v2/05-L3-对话主入口.md)） |
| :func:`describe_adjudication` | ``conflict_adjudication_card`` | [`l3.conflict.adjudication.ConflictAdjudication`](../conflict/adjudication.py)（[05 §9](../../../../docs/技术架构-v2/05-L3-对话主入口.md)） |
| :func:`describe_approval` | ``permission_approval_card`` | [`l3.approval.panel.CapabilityApprovalView`](../approval/panel.py)（[01 §10](../../../../docs/技术架构-v2/01-平台共享契约.md)；[T-L3-006](../../../../项目管理/tasks/T-L3-006-能力安装与导入审批面.md)） |
| :func:`describe_divergence_map` | ``divergence_map`` | **L4 的视图投影件**（[`l4.divergence_view.DivergenceView`](../../l4/divergence_view.py)，[06 §5–§6](../../../../docs/技术架构-v2/06-L4-多视角推理.md)；[T-L4-004.2](../../../../项目管理/tasks/T-L4-004.2-divergence_map描述件与组件升真.md)）——**鸭子类型**：本层只按属性取值，**不 import `st_agent.l4`**（[铁律 7](../../../../项目管理/工程宪法.md)，`LAYER_ORDER` 为 `l3 < l4`） |

**中性口径的切分**（[D-053](../../../../项目管理/决策日志.md) / [D-064](../../../../项目管理/决策日志.md)）：
本模块**不**对生成文案做构造期复检——上游（上下文卡片 / 草稿 / 裁决卡）在构造时已各自
过 [01 §6](../../../../docs/技术架构-v2/01-平台共享契约.md) 执行点 2，而 [01 §12](../../../../docs/技术架构-v2/01-平台共享契约.md)
把**渲染前**那一次复检落在回环服务边界（[`ui/neutrality_gate.py`](../../ui/neutrality_gate.py)，
执行点 2 的落点）。故本模块的职责是**如实分栏**：哪些槽是生成文案（``generated``）、
哪些是数据（``data``）——标错会让用户记忆被误判为违规（标宽）或让生成文案漏检（标窄）。
本模块**自有**的固定文案（方向未判定标注、两方标签、解析来源标签）是常量，
由 ``tests/l3/test_render_describe.py`` 断言其过 [01 §6](../../../../docs/技术架构-v2/01-平台共享契约.md)。

**降级不静默**：输入缺失走 ``unavailable`` + 原因；合法空结果走 ``empty`` + 原因；
载荷形状不合 [01 §12](../../../../docs/技术架构-v2/01-平台共享契约.md)（如槽值不可 JSON 化）
走 ``validation_failed`` + 理由——**不返回一张残缺或猜出来的卡**。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from st_agent.contracts.errors import ContractViolation
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.trace import Trace, TraceStep
from st_agent.contracts.ui_description import UiDescription, checked_description, new_description_id
from st_agent.l3.approval.panel import CapabilityApprovalView
from st_agent.l3.config.draft import OPEN_QUESTION_LABELS, ConfigDraft
from st_agent.l3.config.handling import PanelView
from st_agent.l3.conflict.adjudication import (
    STANCE_UNDECIDED_LABEL,
    ConflictAdjudication,
    ConflictSide,
)
from st_agent.l3.home.card import ContextCard

__all__ = [
    "AGENT_RUN_COLUMNS",
    "AGENT_RUN_TITLE",
    "AGENT_TERMINATION_LABELS",
    "DIVERGENCE_ABSENT_REASON",
    "DIVERGENCE_EMPTY_REASON",
    "DIVERGENCE_TITLE",
    "ORIGIN_LABELS",
    "SIDE_EXISTING_LABEL",
    "SIDE_PROPOSED_LABEL",
    "STANCE_LABELS",
    "TRACE_ABSENT_REASON",
    "TRACE_EMPTY_REASON",
    "describe_adjudication",
    "describe_agent_run",
    "describe_approval",
    "describe_context_card",
    "describe_divergence_map",
    "describe_draft",
    "describe_trace",
]

TRACE_ABSENT_REASON = "本次没有可展开的推理链（05 §7）"
"""链**缺失**（``None``）时的原因——不是空链，见 :data:`TRACE_EMPTY_REASON`。"""

TRACE_EMPTY_REASON = "该推理链尚无步骤（本次派发未发生可追溯的动作，05 §7）"
"""链**存在但为空**时的原因（05 §4：未接入去向的链存在、步骤为空，如实反映）。"""

DIVERGENCE_TITLE = "分歧图"
"""分歧图的卡片名（[06 §5](../../../../docs/技术架构-v2/06-L4-多视角推理.md)；生成文案，过 §6）。"""

STANCE_LABELS: dict[str, str] = {
    "positive": "看好",
    "negative": "看空",
    "neutral": "中性",
    "insufficient-data": "数据不足",
}
"""立场的展示标签（Story 4 的输出枚举措辞；生成文案，过 §6）。

放在**生成文案槽**里下发，而不是让前端按枚举键自己映射——前端不携词表（[01 §12](../../../../docs/技术架构-v2/01-平台共享契约.md)），
由本层给出才能被回环边界的校验门看到（同 :data:`ORIGIN_LABELS`）。
"""

DIVERGENCE_ABSENT_REASON = "本次没有可呈现的分歧图（06 §5）"
"""视图**缺失**（``None``）时的原因——不是「空图」，见 :data:`DIVERGENCE_EMPTY_REASON`。"""

DIVERGENCE_EMPTY_REASON = "本次编排无参与视角，分歧图没有可呈现的视角行（06 §6；空态由渲染面呈现入口）"
"""视图**存在但无矩阵行**时的原因（[06 §6](../../../../docs/技术架构-v2/06-L4-多视角推理.md)）。"""

SIDE_EXISTING_LABEL = "既有记录"
"""裁决卡上「既有」一方的标签（生成文案，过 §6）。"""

SIDE_PROPOSED_LABEL = "新的推断"
"""裁决卡上「提案」一方的标签（生成文案，过 §6）。"""

ORIGIN_LABELS: dict[str, str] = {
    "registry": "登记项",
    "declared": "声明面",
}
"""参数解析来源的展示标签（[05 §5](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 的 ``origin``；生成文案，过 §6）。

放在**生成文案槽**里下发，而不是让前端按枚举键自己映射——前端不携词表（[01 §12](../../../../docs/技术架构-v2/01-平台共享契约.md)），
由本层给出才能被回环边界的校验门看到。
"""

DRAFT_TITLES: dict[str, str] = {
    "draft": "配置草稿",
    "panel": "参数面板",
}
"""``config_draft_card`` 两个分支的卡片名（生成文案，过 §6）。"""


def _now() -> datetime:
    """用户本地时区当前时刻（[01 §8](../../../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    return datetime.now().astimezone()


def _envelope(
    *,
    component_type: str,
    title: str | None,
    slots: dict[str, Any],
    text_kinds: dict[str, str],
    as_of: datetime | None,
) -> ResultEnvelope:
    """构造描述并包进 ``ok`` 信封；形状不合 §12 即 ``validation_failed``。

    :func:`~st_agent.contracts.ui_description.checked_description` 的不变量在构造期把关
    （槽值须为**已解析的 JSON 值**，禁表达式 / 模板 / 可执行片段）。上游载荷若夹带
    不可 JSON 化的值（如裸 ``datetime``），结果是一条**点名理由**的 ``validation_failed``，
    而不是一个异常穿到回环服务。
    """
    try:
        description = checked_description(
            description_id=new_description_id(),
            component_type=component_type,
            title=title,
            slots=slots,
            text_kinds=text_kinds,
            as_of=as_of,
        )
    except ContractViolation as exc:
        return ResultEnvelope.validation_failed(f"{component_type} 描述不合 01 §12：{exc}")
    return ResultEnvelope.ok(description, as_of=as_of)


def _json(model: BaseModel) -> dict[str, Any]:
    """把 pydantic 模型摊成 JSON 值（§12 只承载已解析的 JSON 值）。"""
    return model.model_dump(mode="json")


# ───────────────────────── trace_timeline（05 §7） ─────────────────────────


def describe_trace(trace: Trace | None, *, now: datetime | None = None) -> ResultEnvelope:
    """推理链 → ``trace_timeline`` 描述（[05 §7](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

    ``steps`` 整槽标 ``generated``——[01 §4](../../../../docs/技术架构-v2/01-平台共享契约.md)
    的 ``note`` 契约定为**中性措辞**，且整条链由系统产出（无用户原话），故无需分槽。
    每步八个字段**原样携带**，使「展开细看」能落到证据：``step_type`` / ``ref`` /
    ``input_digest`` / ``output_digest`` / ``duration_ms`` / ``timestamp`` / ``degraded`` / ``note``。
    """
    if trace is None:
        return ResultEnvelope.unavailable(
            TRACE_ABSENT_REASON, last_updated_at=now if now is not None else _now()
        )
    if not trace.steps:
        return ResultEnvelope.empty(TRACE_EMPTY_REASON, as_of=now)
    as_of = trace.steps[-1].timestamp
    return _envelope(
        component_type="trace_timeline",
        title=None,
        slots={
            "steps": [_step_slot(step) for step in trace.steps],
            "conclusion_ref": (
                _json(trace.conclusion_ref) if trace.conclusion_ref is not None else None
            ),
            "closed": trace.closed,
        },
        text_kinds={"steps": "generated", "conclusion_ref": "data", "closed": "data"},
        as_of=as_of,
    )


def _step_slot(step: TraceStep) -> dict[str, Any]:
    """一步 → 槽值（八个字段逐一携带，展开即是证据）。"""
    return {
        "step_type": step.step_type,
        "ref": step.ref,
        "input_digest": step.input_digest,
        "output_digest": step.output_digest,
        "duration_ms": step.duration_ms,
        "timestamp": step.timestamp.isoformat(),
        "degraded": step.degraded,
        "note": step.note,
    }


# ───────────────────────── agent_run（05 §10） ─────────────────────────


AGENT_RUN_TITLE = "自主查证循环证据"
"""循环证据包的表名（生成文案，过 §6）。"""

AGENT_TERMINATION_LABELS: dict[str, str] = {
    "finished": "模型给出结束",
    "step_limit": "达到步数上界",
    "llm_call_limit": "达到 LLM 调用次数上界",
    "gate_absent": "未注入授权闸门（按 fail-closed 未执行）",
    "needs_confirmation": "下一步待用户确认",
    "denied": "下一步未获自主执行授权",
    "endpoint_unavailable": "端点未声明支持工具调用",
    "llm_failed": "LLM 调用失败",
    "unknown_tool": "模型请求的工具不在目录内",
    "catalog_empty": "工具目录为空",
}
"""终止原因的展示标签（生成文案，过 §6）。

覆盖性由 ``tests/l3/test_agent_report.py`` 对着循环侧的 ``TERMINATION_REASONS`` 钉死——
**不在此 import 循环模块**：``l3.runtime`` 反向要用本模块（产出装描述件），互相 import 会成环，
故镜像面放在测试里把关（同 crate 内「枚举副本 + 测试钉住」的既有做法）。
"""

AGENT_RUN_COLUMNS: tuple[dict[str, str], ...] = (
    {"key": "index", "label": "步骤"},
    {"key": "tool_name", "label": "工具"},
    {"key": "skill_id", "label": "执行目标"},
    {"key": "status", "label": "结果状态"},
    {"key": "truncated", "label": "结果已截断"},
    {"key": "log_ref", "label": "留痕指针"},
)
"""证据表的列（``table`` 渲染件按 ``[{key, label}]`` 取值——渲染面是形态的权威）。"""


def describe_agent_run(
    view: Any, *, agent_run_id: str | None = None, now: datetime | None = None,
) -> ResultEnvelope:
    """自主查证循环的产出 → ``table`` 描述（[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

    **鸭子类型**（同 :func:`describe_divergence_map` 消费 L4 视图的先例）：只按属性取值，
    不 import ``st_agent.l3.runtime``——产出装配反向消费本模块，互相 import 会成环。

    四段分栏（[D-053](../../../../项目管理/决策日志.md) / [D-064](../../../../项目管理/决策日志.md)
    的切分）：

    - **客观面**（``data``）：任务原话、``agent_run_id``、各步的工具 / 执行目标 / 执行留痕 /
      结果状态 / 是否截断 / 留痕指针、被拦下未执行的那一步、上界与调用次数。载荷与标识属数据，
      整串复检会误伤，故标 ``data``。
    - **说明面**（``generated``）：终止原因的**标签**与**说明**、闸门给出的原因、每步的中性说明、
      模型在结束轮给出的文本、表头列名。这些是系统与模型的措辞，须被回环边界的校验门看到
      （[01 §12](../../../../docs/技术架构-v2/01-平台共享契约.md) 的 ``generated`` 槽）。

    终止原因不在标签表内即 ``validation_failed``——**描述件不臆造标签**（同本模块
    「降级不静默」的取向）。
    """
    termination = getattr(view, "termination", None)
    label = AGENT_TERMINATION_LABELS.get(termination) if isinstance(termination, str) else None
    if label is None:
        return ResultEnvelope.validation_failed(
            f"未知的循环终止原因 {termination!r}（描述件不臆造标签，05 §10）"
        )
    steps = tuple(getattr(view, "steps", ()) or ())
    pending = getattr(view, "pending", None)
    return _envelope(
        component_type="table",
        title=AGENT_RUN_TITLE,
        slots={
            "task": getattr(view, "task", ""),
            "agent_run_id": agent_run_id,
            "termination": label,
            "termination_reason": getattr(view, "reason", "") or "",
            "at_bound": bool(getattr(view, "at_bound", False)),
            "gate_reason": getattr(view, "gate_reason", "") or "",
            "columns": [dict(column) for column in AGENT_RUN_COLUMNS],
            "rows": [_agent_step_row(index, step) for index, step in enumerate(steps, 1)],
            "step_notes": [
                {"index": index, "note": step.envelope.reason or ""}
                for index, step in enumerate(steps, 1)
                if step.envelope.reason
            ],
            "pending": (
                {"tool_name": pending.name, "arguments": dict(pending.arguments)}
                if pending is not None else None
            ),
            "answer": getattr(view, "answer", "") or "",
            "llm_calls": int(getattr(view, "llm_calls", 0)),
            "max_steps": int(getattr(view, "max_steps", 0)),
            "max_llm_calls": int(getattr(view, "max_llm_calls", 0)),
        },
        text_kinds={
            "task": "data",
            "agent_run_id": "data",
            "termination": "generated",
            "termination_reason": "generated",
            "at_bound": "data",
            "gate_reason": "generated",
            "columns": "generated",
            "rows": "data",
            "step_notes": "generated",
            "pending": "data",
            "answer": "generated",
            "llm_calls": "data",
            "max_steps": "data",
            "max_llm_calls": "data",
        },
        as_of=now,
    )


def _agent_step_row(index: int, step: Any) -> dict[str, Any]:
    """一步 → 表行（客观字段；结果**照实**标状态，不把失败呈现为成功）。"""
    return {
        "index": index,
        "tool_name": step.tool_name,
        "skill_id": step.skill_id,
        "status": step.envelope.status,
        "truncated": bool(step.truncated),
        "log_ref": step.log_ref,
    }


# ───────────────────────── context_card（05 §2） ─────────────────────────


def describe_context_card(card: ContextCard, *, now: datetime | None = None) -> ResultEnvelope:
    """上下文卡片 → ``context_card`` 描述（[05 §2](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

    六段**逐段携带 `state`**（``ok`` / ``empty`` / ``unavailable``），段名 / 原因 /
    生产方在 ``labels``（生成文案槽），段内容在 ``sections``（数据槽）——两处按
    ``key`` 并联。非 ``ok`` 段的原因**必须**留在描述里（§2：不静默留空）。
    """
    sections: list[dict[str, Any]] = []
    labels: dict[str, dict[str, str]] = {}
    for section in card.sections:
        entry: dict[str, Any] = {"key": section.key, "state": section.state}
        if section.items:
            entry["items"] = list(section.items)
        if section.tags:
            entry["tags"] = [
                {"label": tag.label, "kind": tag.kind, "source": tag.source_node_id}
                for tag in section.tags
            ]
        if section.total is not None:
            entry["total"] = section.total
        if section.limit is not None:
            entry["limit"] = section.limit
        sections.append(entry)

        label = {"title": section.title}
        if section.reason:
            label["reason"] = section.reason
        if section.producer:
            label["producer"] = section.producer
        labels[section.key] = label

    return _envelope(
        component_type="context_card",
        title=card.title,
        slots={
            "sections": sections,
            "labels": labels,
            "greeting": card.greeting,
            "empty_hint": card.empty_hint,
            "is_empty": card.is_empty,
            "targets": {
                "graph": _json(card.graph_target),
                "onboarding": (
                    _json(card.onboarding_target) if card.onboarding_target is not None else None
                ),
            },
        },
        text_kinds={
            "sections": "data",
            "labels": "generated",
            "greeting": "generated",
            "empty_hint": "generated",
            "is_empty": "data",
            "targets": "data",
            # targets.query.topic 是用户话题、tags.label / items 是记忆本体——一律数据（D-053）
        },
        as_of=card.as_of if card.as_of is not None else now,
    )


# ───────────────────────── config_draft_card（05 §5） ─────────────────────────


def describe_draft(
    draft: ConfigDraft | PanelView, *, now: datetime | None = None
) -> ResultEnvelope:
    """配置草稿 / 参数面板视图 → ``config_draft_card`` 描述（[05 §5](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

    两个分支**共用**同一组件类型（§5 的双通道是一个组件的两个形态），以 ``mode`` 区分：

    - ``ConfigDraft`` → ``mode="draft"``：`summary` 是理解摘要（**数据**，由确认卡条目
      派生，见 [`l3.config.draft`](../config/draft.py) 的模块文档——**不整串复检**），
      `questions` 是未澄清项的问句与来源标签（生成文案）。
    - ``PanelView`` → ``mode="panel"``：`panels` 是双通道视图的逐参数表单描述
      （生成文案，携 :data:`ORIGIN_LABELS` 的来源标签），**草稿态不落盘**。

    两个分支都**只渲染、不落值**：配置生效归 [01 §7](../../../../docs/技术架构-v2/01-平台共享契约.md)
    登记面端口（[D-061](../../../../项目管理/决策日志.md)）。
    """
    if isinstance(draft, PanelView):
        return _draft_envelope(
            target=draft.target,
            mode="panel",
            summary="",
            values=dict(draft.values),
            defaults={param.name: param.default for param in draft.params},
            panels=[
                {
                    "name": param.name,
                    "description": param.chat_text,
                    "origin": param.origin,
                    "origin_label": ORIGIN_LABELS.get(param.origin, ""),
                    "field": _json(param.panel_field),
                }
                for param in draft.params
            ],
            questions=[],
            now=now,
        )
    if isinstance(draft, ConfigDraft):
        return _draft_envelope(
            target=draft.target,
            mode="draft",
            summary=draft.understanding_summary,
            values=dict(draft.parameter_draft),
            defaults={
                question.param: question.default
                for question in draft.open_questions
                if question.default is not None
            },
            panels=[],
            questions=[
                {
                    "param": question.param,
                    "prompt": question.prompt,
                    "source": question.source,
                    "source_label": OPEN_QUESTION_LABELS[question.source],
                }
                for question in draft.open_questions
            ],
            now=now,
        )
    return ResultEnvelope.validation_failed(
        "config_draft_card 只描述 ConfigDraft（草稿）或 PanelView（参数面板视图），"
        f"得到 {type(draft).__name__}（05 §5）"
    )


def _draft_envelope(
    *,
    target: str,
    mode: str,
    summary: str,
    values: dict[str, Any],
    defaults: dict[str, Any],
    panels: list[dict[str, Any]],
    questions: list[dict[str, Any]],
    now: datetime | None,
) -> ResultEnvelope:
    return _envelope(
        component_type="config_draft_card",
        title=DRAFT_TITLES[mode],
        slots={
            "target": target,
            "mode": mode,
            "summary": summary,
            "values": values,
            "defaults": defaults,
            "panels": panels,
            "questions": questions,
        },
        text_kinds={
            "target": "data",
            "mode": "data",
            "summary": "data",
            "values": "data",
            "defaults": "data",
            "panels": "generated",
            "questions": "generated",
        },
        as_of=now if now is not None else _now(),
    )


DRAFT_TITLES: dict[str, str] = {
    "draft": "配置草稿",
    "panel": "参数面板",
}


# ───────────────────────── conflict_adjudication_card（05 §9） ─────────────────────────


def describe_adjudication(
    card: ConflictAdjudication, *, now: datetime | None = None
) -> ResultEnvelope:
    """冲突裁决卡 → ``conflict_adjudication_card`` 描述（[05 §9](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

    **两方并列、不合并**（[铁律 4](../../../../项目管理/工程宪法.md)）：``sides`` 是两方
    记忆本体的**原样**载荷（数据槽），``side_labels`` 按**同一下标**给出各自的标签
    （生成文案）——渲染件因此只能并列呈现，合并需要额外拼接、不在描述里。
    方向未判定时 ``stance_label`` 携 :data:`~st_agent.l3.conflict.adjudication.STANCE_UNDECIDED_LABEL`
    （**显式标注**，不静默留空）；两方内容**不过 §6**（[D-053](../../../../项目管理/决策日志.md)），
    而抬头 / 问句 / 动作标签 / 方向候选**逐条过**（上游构造期已过，边界门再复检一次）。
    """
    sides: list[dict[str, Any]] = []
    side_labels: list[str] = []
    if card.existing is not None:
        sides.append(_side_slot(card.existing))
        side_labels.append(SIDE_EXISTING_LABEL)
    sides.append(_side_slot(card.proposed))
    side_labels.append(SIDE_PROPOSED_LABEL)

    return _envelope(
        component_type="conflict_adjudication_card",
        title=card.header,
        slots={
            "sides": sides,
            "side_labels": side_labels,
            "question": card.question,
            "actions": [
                {"decision": action.decision, "label": action.label} for action in card.actions
            ],
            "directions": list(card.directions),
            "stance_label": STANCE_UNDECIDED_LABEL if card.stance_undecided else "",
            "stance_undecided": card.stance_undecided,
            "trace_id": card.trace_id,
        },
        text_kinds={
            "sides": "data",
            "side_labels": "generated",
            "question": "generated",
            "actions": "generated",
            "directions": "generated",
            "stance_label": "generated",
            "stance_undecided": "data",
            "trace_id": "data",
        },
        as_of=now if now is not None else _now(),
    )


def _side_slot(side: ConflictSide) -> dict[str, Any]:
    """冲突一方 → 槽值（``fields`` 是记忆本体的原样承载，属数据展示）。"""
    return {
        "node_id": side.node_id,
        "dimension": side.dimension,
        "fields": dict(side.fields),
    }


# ───────────────────────── permission_approval_card（01 §10 / 03 §5.1 / 09 §3） ─────────────────────────


def describe_approval(view: CapabilityApprovalView, *, now: datetime | None = None) -> ResultEnvelope:
    """审批面视图 → ``permission_approval_card`` 描述（[01 §10](../../../../docs/技术架构-v2/01-平台共享契约.md)）。

    **逐条、不合并**（GWT-1）：``items`` 是每条权限的声明形态与批准态（数据槽，
    含 `permission` / `state` / `panel_field`——控件只给形态、不代用户表态）；
    中性措辞与状态标签（`description` / `chat_text`）走**同一下标**的 ``labels``
    （生成文案槽）——两处并联，[01 §12](../../../../docs/技术架构-v2/01-平台共享契约.md) 的分槽切分（[D-064](../../../../项目管理/决策日志.md)）。

    抬头只说「这是谁的权限申请」（来源标签，生成文案），**不含任何建议**——
    [01 §10](../../../../docs/技术架构-v2/01-平台共享契约.md) 只要求「这个能力想做什么」，
    批与不批是用户的决定。
    """
    return _envelope(
        component_type="permission_approval_card",
        title=f"{view.source_label}权限申请",
        slots={
            "key": view.key,
            "source": view.source,
            "items": [
                {
                    "permission": item.permission,
                    "state": item.state,
                    "decided_at": (
                        item.decided_at.isoformat() if item.decided_at is not None else None
                    ),
                    "panel_field": item.panel_field,
                }
                for item in view.items
            ],
            "labels": {
                item.permission: {
                    "description": item.description,
                    "chat_text": item.chat_text,
                }
                for item in view.items
            },
        },
        text_kinds={
            "key": "data",
            "source": "data",
            # permission 声明原文是注册期数据（不是 L3 生成文案），随 items 归数据槽
            "items": "data",
            "labels": "generated",
        },
        as_of=now if now is not None else _now(),
    )


# ───────────────────────── divergence_map（06 §5–§6） ─────────────────────────


def describe_divergence_map(view: Any, *, now: datetime | None = None) -> ResultEnvelope:
    """分歧图视图 → ``divergence_map`` 描述（[06 §5](../../../../docs/技术架构-v2/06-L4-多视角推理.md)）。

    **输入是鸭子类型**：视图由 L4 的投影件产出（矩阵行需全部参与视角的立场，只有 L4 有），
    而 [铁律 7](../../../../项目管理/工程宪法.md) 禁 L3 import L4（`LAYER_ORDER` 为 `l3 < l4`），
    故本件按**属性取值**消费——同 :func:`describe_trace` 收 `Trace` 之外的鸭子面一个道理
    （任务 `A1`）。

    **两视图均实现**（[06 §5](../../../../docs/技术架构-v2/06-L4-多视角推理.md)）：

    - `matrix`（主视图）：视角 × 结论，逐行含 `stance` / `confidence` / `trace_id`——
      后者即**追问接口**的取值面，渲染方据此调 :func:`describe_trace` 展开该视角完整链
      （复用 [05 §7](../../../../docs/技术架构-v2/05-L3-对话主入口.md)，本件**不自造**链渲染）
    - `network`（辅视图）：证据节点 + 引用边

    **分槽按来源**（[D-064](../../../../项目管理/决策日志.md)）：Mini Debate 段把**结构化对照字段**
    放 `conflicts`（数据）、把 `reasons` 与重评估 `note` 放 `conflict_texts`（生成文案，按
    `key` 并联）——同槽混装会让生成文案躲开回环边界那一次 §6 复检（[D-053](../../../../项目管理/决策日志.md)）。
    一致提示 `unanimity_notice` 与降级标注 `notes` 同为生成文案槽。
    """
    if view is None:
        return ResultEnvelope.unavailable(
            DIVERGENCE_ABSENT_REASON, last_updated_at=now if now is not None else _now()
        )
    try:
        slots, text_kinds, as_of = _divergence_slots(view)
    except (AttributeError, TypeError, KeyError) as exc:
        return ResultEnvelope.validation_failed(
            "divergence_map 描述收到的视图形状不合（须为 L4 视图投影的鸭子面，06 §5）："
            f"{type(view).__name__} —— {exc}"
        )
    if not slots["matrix"]:
        return ResultEnvelope.empty(DIVERGENCE_EMPTY_REASON, as_of=as_of)
    return _envelope(
        component_type="divergence_map",
        title=DIVERGENCE_TITLE,
        slots=slots, text_kinds=text_kinds, as_of=as_of,
    )


def _divergence_slots(view: Any) -> tuple[dict[str, Any], dict[str, str], datetime | None]:
    """视图 → (槽值, 分栏, as_of)。属性缺失即抛，由调用方转 `validation_failed`（不吞错）。"""
    conflicts: list[dict[str, Any]] = []
    texts: dict[str, dict[str, list[str]]] = {}
    for entry in view.conflicts:
        analysis = entry.analysis
        conflicts.append({
            "key": entry.key,
            "topic": analysis.topic,
            "sides": [_json(side) for side in analysis.sides],
            "shared_refs": list(analysis.shared_refs),
            "premise_related": analysis.premise_related,
            "shared_skills": list(analysis.shared_skills),
            # 重评估的**结构化**字段入数据槽；其 `note` 是生成文案，另槽承载
            "reviews": [
                {"ref": review.ref, "validity": review.validity,
                 "freshness": review.freshness, "weight": review.weight}
                for review in analysis.reviews
            ],
        })
        texts[entry.key] = {
            "reasons": list(analysis.reasons),
            "review_notes": [r.note for r in analysis.reviews if r.note],
        }

    matrix = [
        {"lens_id": row.lens_id, "name": row.name, "stance": row.stance,
         "confidence": row.confidence, "trace_id": row.trace_id}
        for row in view.rows
    ]
    uniform_stance = getattr(view, "uniform_stance", None)
    return (
        {
            "topic": view.topic,
            "map_id": view.map_id,
            "matrix": matrix,
            "agreements": [_json(a) for a in view.agreements],
            "disagreements": [_json(d) for d in view.disagreements],
            "blind_spots": [_json(b) for b in view.blind_spots],
            "network": _json(view.evidence_network),
            "conflicts": conflicts,
            "conflict_texts": texts,
            "notes": list(view.notes),
            "unanimous": bool(view.unanimous),
            # 未一致时 `uniform_stance` 为 None——槽值原样承载（JSON 的 null），不改成空串
            "uniform_stance": uniform_stance,
            "unanimity_notice": view.unanimity_notice,
            "stance_labels": dict(STANCE_LABELS),
        },
        {
            "topic": "data",
            "map_id": "data",
            "matrix": "data",
            "agreements": "data",
            "disagreements": "data",
            "blind_spots": "data",
            "network": "data",
            "conflicts": "data",
            "conflict_texts": "generated",
            "notes": "generated",
            "unanimous": "data",
            "uniform_stance": "data",
            "unanimity_notice": "generated",
            "stance_labels": "generated",
        },
        getattr(view, "as_of", None),
    )
