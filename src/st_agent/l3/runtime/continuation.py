"""待确认与待输入步骤的一键续跑（T-AGT-011.3；[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

**轻形态**（[D-137](../../../项目管理/决策日志.md) D-4）：不给循环加挂起 / 恢复状态机——续跑是
**新一轮循环承接前序证据**：

- **前序步骤**由 ``execution_log`` 的 ``skill_run`` 记录重建（:mod:`st_agent.l1.runner.models`），
  **不把载荷复制进** ``agent_run`` 留痕（不造第二真相源）；
- **前序不计入**新循环的步数 / LLM 调用上界（否则长循环续跑处处触界）；
- ``needs_confirmation`` 的**确认步先执行**再继续；``needs_user_input`` 的**答案**作为
  「用户补充」进工作上下文；
- 续跑产**新** ``agent_run_id`` 并以 ``resumed_from`` 指回旧留痕（``trace_id`` 也是新链）；
- **依据过期**（留痕写入起 30 分钟，实现口径）时显式提示并允许 ``rerun``（原任务从头跑）。

本模块只做**装配与判定**：执行仍走 :func:`~st_agent.l3.runtime.loop.run_agent_loop`（真 L1 流水线），
收口仍走 :func:`~st_agent.l3.runtime.report.conclude_agent_run`。三种拒绝（留痕不可续跑 /
步骤重建失败 / 依据过期）都回**显式信封**，不抛裸异常、不静默重跑。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from st_agent.contracts.identifiers import TraceId
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.trace import Trace
from st_agent.l1.runner.models import SkillRun
from st_agent.l3.errors import AgentRunNotFoundError, AgentRuntimeValidationError
from st_agent.l3.runtime.context import (
    DEFAULT_WORK_CONTEXT_BUDGET,
    RESULT_TRUNCATE_CHARS,
    WorkStep,
    new_work_step,
)
from st_agent.l3.runtime.loop import (
    ASK_USER_TOOL,
    DEFAULT_MAX_LLM_CALLS,
    DEFAULT_MAX_STEPS,
    PLAN_UPDATE_TOOL,
    ApprovedPermissions,
    LoopOutcome,
    resolve_approved,
    run_agent_loop,
)
from st_agent.l3.runtime.protocol import (
    DEFAULT_TURN_INITIATOR,
    DEFAULT_TURN_PURPOSE,
)
from st_agent.l3.runtime.report import (
    AgentRunRecord,
    AgentRunReport,
    conclude_agent_run,
    load_agent_run,
)

__all__ = [
    "CONTINUATION_TTL",
    "RESUMABLE_TERMINATIONS",
    "ResumeDecision",
    "ResumeOutcome",
    "load_continuation",
    "resume_agent_run",
]

CONTINUATION_TTL = timedelta(minutes=30)
"""续跑依据的有效期（自留痕写入时刻起；实现口径，可经参数覆盖）。

超期不静默重放旧证据——交还改为「显式提示 + 允许重跑」（GWT-3）。
"""

RESUMABLE_TERMINATIONS: tuple[str, ...] = ("needs_confirmation", "needs_user_input")
"""可续跑的终止原因（两种交还）。"""

ResumeDecision = Literal["approve", "answer", "rerun", "cancel"]

_CONTROL_TOOLS: tuple[str, ...] = (PLAN_UPDATE_TOOL, ASK_USER_TOOL)
"""控制原语（计划 / 提问）——它们不经 L1 执行面，故不能按工具重放。"""


class ResumeOutcome(BaseModel):
    """续跑的一次产出（信封 + 新留痕 + 过期标记 + 说明）。"""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    envelope: ResultEnvelope
    report: AgentRunReport | None = None
    """本次续跑的新留痕（取消 / 拒绝 / 过期时 ``None``——确实没有跑）。"""
    source_run_id: str = ""
    """本次续跑所承接的旧 ``agent_run_id``（拒绝 / 过期时也给出，供表现层重跑）。"""
    stale: bool = False
    """依据是否已过期（过期时信封显式提示，且``report`` 为 ``None``）。"""
    note: str = ""

    @property
    def agent_run_id(self) -> str:
        """本次续跑的新 ``agent_run_id``（未运行时为空串）。"""
        return "" if self.report is None else self.report.agent_run_id


def load_continuation(store: Any, agent_run_id: str) -> AgentRunRecord:
    """取回一条**可续跑**的留痕；终止原因不可续跑即显式拒绝（不返回半成品）。"""
    record = load_agent_run(store, agent_run_id)
    if record.termination not in RESUMABLE_TERMINATIONS:
        raise AgentRuntimeValidationError(
            f"留痕 {record.agent_run_id!r} 的终止原因是 {record.termination!r}，"
            f"不是可续跑状态（应为 {RESUMABLE_TERMINATIONS}）"
        )
    return record


def resume_agent_run(
    store: Any,
    agent_run_id: str,
    *,
    decision: ResumeDecision,
    answers: Mapping[str, Any] | None = None,
    endpoint_id: str,
    protocol: Any,
    runner: Any,
    tools: Sequence[Any],
    gate: Any = None,
    approved_permissions: ApprovedPermissions = (),
    initiator: str = DEFAULT_TURN_INITIATOR,
    purpose: str = DEFAULT_TURN_PURPOSE,
    max_steps: int = DEFAULT_MAX_STEPS,
    max_llm_calls: int = DEFAULT_MAX_LLM_CALLS,
    budget_chars: int = DEFAULT_WORK_CONTEXT_BUDGET,
    max_result_chars: int = RESULT_TRUNCATE_CHARS,
    session_id: str | None = None,
    history: Sequence[Any] = (),
    memory: Sequence[Any] = (),
    parallel_safe: bool = False,
    allow_sub_agents: bool = True,
    nested_recorder: Any = None,
    now: datetime | None = None,
    ttl: timedelta = CONTINUATION_TTL,
) -> ResumeOutcome:
    """按用户的选择续跑一次交还（[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

    :param decision: ``approve``（确认待确认步）/ ``answer``（提交问项答案）/
        ``rerun``（原任务重跑、不带前序）/ ``cancel``（放弃续跑）
    :param answers: ``answer`` 时的答案（一问一参的 ``parameter → 值``）
    :param now: 当前时刻（过期判定用；缺省取本地时区当前时刻）
    :param ttl: 续跑依据有效期（缺省 :data:`CONTINUATION_TTL`）
    """
    if decision not in ("approve", "answer", "rerun", "cancel"):
        raise AgentRuntimeValidationError(f"未知的续跑选择 {decision!r}")
    moment = now if now is not None else datetime.now().astimezone()
    try:
        record = load_agent_run(store, agent_run_id)
    except (AgentRunNotFoundError, AgentRuntimeValidationError) as exc:
        return ResumeOutcome(envelope=ResultEnvelope.validation_failed(str(exc)))

    if decision == "cancel":
        return ResumeOutcome(
            envelope=ResultEnvelope.empty(
                f"已放弃续跑（留痕 {record.agent_run_id}）；"
                f"原任务与已收集的 {len(record.steps)} 步证据保持不变"
            ),
            source_run_id=record.agent_run_id,
            note="用户放弃续跑",
        )

    stale = _is_stale(record, moment, ttl)
    if stale and decision != "rerun":
        return ResumeOutcome(
            stale=True,
            source_run_id=record.agent_run_id,
            envelope=ResultEnvelope.empty(
                f"续跑依据已过期：留痕写入于 {record.recorded_at.isoformat()}，"
                f"距现在超过 {_ttl_text(ttl)}；旧证据可能已不代表当前状态，"
                "未自动沿用——可选择「重跑」从头执行该任务"
            ),
            note="续跑依据已过期",
        )

    # 终止原因与选择的匹配（不默默把 rerun 当 approve 用）
    if decision != "rerun" and record.termination not in RESUMABLE_TERMINATIONS:
        return ResumeOutcome(
            envelope=ResultEnvelope.validation_failed(
                f"留痕 {record.agent_run_id!r} 的终止原因是 {record.termination!r}，"
                "不可续跑（仅待确认 / 待用户输入可续跑）"
            ),
        )
    if decision == "approve" and record.termination != "needs_confirmation":
        return ResumeOutcome(
            envelope=ResultEnvelope.validation_failed(
                "当前交还不是「待确认」，没有可确认的待执行步；"
                "待用户输入请选择提交答案或重跑"
            ),
        )
    if decision == "answer" and record.termination != "needs_user_input":
        return ResumeOutcome(
            envelope=ResultEnvelope.validation_failed(
                "当前交还不是「待用户输入」，没有可作答的问项；"
                "待确认请选择确认并继续或重跑"
            ),
        )
    if decision == "approve" and (
        record.pending is None or record.pending.tool_name in _CONTROL_TOOLS
    ):
        reason = (
            "待确认的那一步是控制原语（计划 / 提问），续跑不支持按工具重放；"
            "请调整运行期授权口径后选择重跑"
            if record.pending is not None and record.pending.tool_name in _CONTROL_TOOLS
            else "当前交还没有待确认的那一步（留痕不完整），无法确认续跑"
        )
        return ResumeOutcome(envelope=ResultEnvelope.validation_failed(reason))

    prior_steps: tuple[WorkStep, ...] = ()
    initial_trace: Trace | None = None
    if decision != "rerun":
        try:
            prior_steps = _rebuild_steps(store, record)
        except (AgentRunNotFoundError, AgentRuntimeValidationError, ValueError) as exc:
            return ResumeOutcome(
                envelope=ResultEnvelope.failed(
                    f"续跑前序证据重建失败：{exc}"
                ),
            )

    plan = record.plan if decision != "rerun" else ()
    plan_updated = record.plan_updated if decision != "rerun" else False
    user_inputs = dict(answers or {}) if decision == "answer" else {}

    if decision == "approve":
        try:
            prior_steps, initial_trace = _execute_pending(
                record, prior_steps, runner=runner, tools=tuple(tools),
                approved_permissions=approved_permissions,
                initiator=initiator, purpose=purpose,
            )
        except AgentRuntimeValidationError as exc:
            return ResumeOutcome(
                envelope=ResultEnvelope.validation_failed(str(exc)),
                source_run_id=record.agent_run_id,
            )

    outcome = run_agent_loop(
        task=record.task,
        endpoint_id=endpoint_id,
        protocol=protocol,
        runner=runner,
        tools=tuple(tools),
        gate=gate,
        approved_permissions=approved_permissions,
        initiator=initiator,
        purpose=purpose,
        max_steps=max_steps,
        max_llm_calls=max_llm_calls,
        budget_chars=budget_chars,
        max_result_chars=max_result_chars,
        trace=initial_trace,
        session_id=session_id,
        history=history,
        memory=memory,
        prior_steps=prior_steps,
        plan=plan,
        plan_updated=plan_updated,
        user_inputs=user_inputs,
        parallel_safe=parallel_safe,
        allow_sub_agents=allow_sub_agents,
        nested_recorder=nested_recorder,
        parent_trace_id=record.parent_trace_id,
        prior_sub_agents=tuple(record.sub_agents),
    )
    if decision == "answer":
        outcome = outcome.model_copy(update={
            "context_note": _join_note(
                outcome.context_note,
                f"续跑：已带上用户对 {len(user_inputs)} 个问项的回答",
            ),
        })
    report = conclude_agent_run(
        store, outcome, now=moment, resumed_from=record.agent_run_id,
    )
    return ResumeOutcome(
        envelope=report.envelope,
        report=report,
        source_run_id=record.agent_run_id,
        stale=stale,
        note=_join_note(record.ask_note, f"已续跑自 {record.agent_run_id}"),
    )


# ───────────────────────── 内部 ─────────────────────────


def _is_stale(record: AgentRunRecord, moment: datetime, ttl: timedelta) -> bool:
    """留痕是否已过期（同一口径的时区比较；无时区的记录按本地时区补齐）。"""
    recorded = record.recorded_at
    if recorded.tzinfo is None:
        recorded = recorded.astimezone()
    return (moment - recorded) > ttl


def _ttl_text(ttl: timedelta) -> str:
    total = int(ttl.total_seconds())
    if total % 60 == 0:
        return f"{total // 60} 分钟"
    return f"{total} 秒"


def _rebuild_steps(store: Any, record: AgentRunRecord) -> tuple[WorkStep, ...]:
    """按留痕里的步骤引用重建前序 ``WorkStep``（载荷取自 ``skill_run`` 记录）。"""
    steps: list[WorkStep] = []
    for ref in record.steps:
        try:
            raw = store.get("execution_log", ref.log_ref)
        except Exception as exc:  # noqa: BLE001 —— 取不到即重建失败，显式上抛
            raise AgentRunNotFoundError(
                f"前序步骤的留痕 {ref.log_ref} 取不到：{exc}"
            ) from exc
        try:
            run = SkillRun.model_validate_json(raw)
        except Exception as exc:  # noqa: BLE001 —— 形态坏同样是重建失败
            raise AgentRuntimeValidationError(
                f"前序步骤的留痕 {ref.log_ref} 形态坏：{exc}"
            ) from exc
        arguments = {**dict(run.params), **dict(run.inputs)}
        steps.append(new_work_step(
            tool_name=ref.tool_name,
            skill_id=ref.skill_id,
            arguments=arguments,
            skill_run_id=ref.skill_run_id,
            envelope=run.envelope,
        ))
    return tuple(steps)


def _execute_pending(
    record: AgentRunRecord,
    prior_steps: tuple[WorkStep, ...],
    *,
    runner: Any,
    tools: Sequence[Any],
    approved_permissions: ApprovedPermissions,
    initiator: str,
    purpose: str,
) -> tuple[tuple[WorkStep, ...], Trace]:
    """把待确认的那一步**先执行**（用户已确认），返回 ``(前序步骤 + 该步, 链起点)``。"""
    pending = record.pending
    if pending is None:  # 调用方已挡过，这里再兜一层
        return prior_steps, Trace(trace_id=TraceId.generate())
    # 该工具当前的执行目标（能力升版后取当下的版本，不把旧版本号冻进留痕）
    from st_agent.l1.skills.tool_catalog import split_arguments  # 局部导入：保持顶层依赖精简
    entry = next((item for item in tools if item.name == pending.tool_name), None)
    if entry is None:
        raise AgentRuntimeValidationError(
            f"待确认的工具 {pending.tool_name!r} 已不在当前工具目录内"
            "（能力被移除或改名），无法按原样续跑；可选择重跑"
        )
    values, object_inputs = split_arguments(entry, dict(pending.arguments))
    run = runner.run(
        entry.skill_id,
        values,
        inputs=object_inputs,
        trace=Trace(trace_id=TraceId.generate()),
        approved_permissions=resolve_approved(
            approved_permissions, entry.skill_id
        ),
        initiator=initiator,
        purpose=purpose,
    )
    step = new_work_step(
        tool_name=pending.tool_name,
        skill_id=entry.skill_id,
        arguments=dict(pending.arguments),
        skill_run_id=run.skill_run_id,
        envelope=run.envelope,
    )
    return (*prior_steps, step), run.trace


def _join_note(*parts: str) -> str:
    return "；".join(part for part in parts if part)
