"""受控自主的查证循环实现（M5）。"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.trace import Trace
from st_agent.l3.errors import (
    AgentRunNotFoundError,
    AgentRuntimeValidationError,
    SessionNotFoundError,
    SessionValidationError,
)
from st_agent.l3.runtime import (
    ActionGate,
    ActiveRunRegistry,
    AgentRunReport,
    LoopBounds,
    NativeToolCallProtocol,
    NestedRunRef,
    RunAlreadyActiveError,
    RunEventLog,
    ResumeOutcome,
    agent_log_ref_of,
    conclude_agent_run,
    investigate_family,
    load_agent_run,
    new_agent_run_id,
    read_run_events,
    resume_agent_run,
    run_agent_loop,
)


M5_ABSENT_TASK_REASON = (
    "investigate 去向缺少任务描述（05 §3.2：无 target Skill 的意图，其任务经确认卡取值 "
    "`task` 或 `target` 承载）；未运行自主查证循环（不臆造一个任务去跑）"
)
"""`investigate` 去向缺任务文本时的**显式**失败原因（不静默跑一个空任务）。"""

NO_SESSION_NOTE = "未携带会话标识：本次按无历史的单次查证运行（05 §4）"
"""无会话时的**显式标注**（[05 §4](../../docs/技术架构-v2/05-L3-对话主入口.md)）——不臆造会话、
也不静默当「有会话但历史为空」：两者对模型是可区分的两种上下文。"""

SESSION_INVALID_NOTE = "会话标识无法读取（会话不存在或记录损坏）：本次按无历史的单次查证运行"
"""会话标识给了但读不出时的**显式标注**——不伪造历史，也不让一次可解释的降级变成崩溃。"""

CONTEXT_ABSENT_NOTE = "会话 / 记忆读面未装配：本次按无历史的单次查证运行"
"""带会话但组合根未注入读面时的**显式标注**（装配缺口如实呈现，不静默当空历史）。"""


@dataclass(frozen=True)
class InvestigationOutcome:
    """`investigate` 去向的**载荷形态**（总线只认 `.envelope`；[05 §4] 的鸭子面）。

    除信封外另携两件**装配级**产物，供表现层与集成套件按属性取值（总线不看它们）：
    ``report``（[`AgentRunReport`]，含 `agent_run_id` 与留痕记录）与 ``trace``（本次循环
    的完整链，`explain` 去向据此逐步展开）。

    ``report`` / ``trace`` 在**未运行**的终止（缺任务文本）时为 ``None``——那时确实没有
    留痕与链，不拿空壳冒充。
    """

    envelope: ResultEnvelope
    report: AgentRunReport | None = None
    trace: Trace | None = None
    session_id: str | None = None
    """本次循环所属会话；``None`` 即无历史的单次查证（唯与 ``context_note`` 一起看才不歧义）。"""
    context_note: str = ""
    """上下文形态的显式标注（无会话 / 无历史为空等）——[05 §4] 要求「产出中标注」。"""

    @property
    def agent_run_id(self) -> str:
        """本次循环的留痕标识（未运行时为空串）。"""
        return "" if self.report is None else self.report.agent_run_id


class _AgentInvestigator:
    """`investigate` 去向的**循环端口**（[05 §4] 的鸭子面：``investigate(confirmation, …)``）。

    把一张已确认的意图卡收敛为**一次循环执行**，三件都在既有面上完成、本类**不重造**：

    - **任务**取确认卡的取值（`values["task"]`）或 `target`；两者皆空即 `validation_failed`
      （[05 §3.2] 的「无 target Skill 的意图」——任务由该两处之一承载，不臆造）；
    - **执行** = [`run_agent_loop`]（真 L1 流水线：参数 / 权限 / 沙箱 / 留痕全沿用）；闸门是
      [`ActionGate`]，判据取**注入的** L6 运行期授权面（`:data:`AGENT_DECISION_METHOD`）；双上界
      取 `LoopBounds`（[01 §7] 登记项，[`T-AGT-007`]）；
    - **收口** = [`conclude_agent_run`]（铸 `agent_run_id` → 落留痕 → 装证据包信封）。

    缺省不注入授权面时 [`ActionGate`] 一律 `denied`（fail-closed，[05 §10]）——本类不因此
    替它放行；端点不支持工具调用时循环自己回 `endpoint_unavailable`（[02 §4] 能力诚实性）。
    """

    def __init__(
        self,
        *,
        store: Any,
        runner: Any,
        skills: Any,
        bounds: Any,
        protocol: Any,
        endpoint_id: str,
        authorization: Any = None,
        sessions: Any = None,
        reader: Any = None,
        permissions: Any = None,
        nested_recorder: Any = None,
        runs: ActiveRunRegistry | None = None,
    ) -> None:
        self._store = store
        self._runner = runner
        self._skills = skills
        self._bounds = bounds
        self._protocol = protocol
        self._endpoint_id = endpoint_id
        self._authorization = authorization
        self._sessions = sessions
        self._reader = reader
        self._permissions = permissions
        self._runs = runs if runs is not None else ActiveRunRegistry()

        if nested_recorder is None:
            def _record(outcome: Any) -> NestedRunRef:
                report = conclude_agent_run(self._store, outcome)
                return NestedRunRef(
                    agent_run_id=report.agent_run_id,
                    log_ref=agent_log_ref_of(report.agent_run_id),
                )

            nested_recorder = _record
        self._nested_recorder = nested_recorder

    @property
    def runs(self) -> ActiveRunRegistry:
        """活动 run 注册表（进程内瞬态；供运行控制面 / 集成套件按 id 取控制句柄）。"""
        return self._runs

    def investigate(
        self,
        confirmation: Any,
        *,
        values: Mapping[str, Any] | None = None,
        now: Any = None,
        session_id: str | None = None,
        agent_run_id: str | None = None,
    ) -> InvestigationOutcome:
        """跑一次自主查证循环并把产出装成信封（[05 §10]；载荷见 :class:`InvestigationOutcome`）。

        :param agent_run_id: **开跑前**由调用方预铸的标识（后台运行的调用方据此立即寻址，
        [D-154] B1）；缺省仍自行铸。预铸标识照常走注册表互斥，不另开口子。
        """
        merged = {
            **dict(getattr(confirmation, "values", {}) or {}),
            **dict(values or {}),
        }
        session = (session_id or "").strip() or None
        task = str(merged.get("task") or getattr(confirmation, "target", "") or "").strip()
        if not task:
            _history, _memory, note = self._context_inputs(session, task)
            return InvestigationOutcome(
                envelope=ResultEnvelope.validation_failed(M5_ABSENT_TASK_REASON),
                session_id=session,
                context_note=note,
            )
        run_id = str(agent_run_id or "").strip() or new_agent_run_id()
        try:
            self._runs.register(run_id, session)
        except AgentRuntimeValidationError as exc:
            return InvestigationOutcome(
                envelope=ResultEnvelope.validation_failed(str(exc)),
                session_id=session,
            )
        try:
            history, memory, note = self._context_inputs(session, task)
            events = RunEventLog(self._store, run_id)
            budget_chars = self._bounds.budget_chars()
            outcome = run_agent_loop(
                task=task,
                endpoint_id=self._endpoint_id,
                protocol=self._protocol,
                runner=self._runner,
                tools=self._skills.tool_catalog(),
                gate=ActionGate(self._authorization),
                approved_permissions=self._permissions or (),
                max_steps=self._bounds.steps(),
                max_llm_calls=self._bounds.llm_calls(),
                budget_chars=budget_chars,
                session_id=session,
                history=history,
                memory=memory,
                parallel_safe=True,
                nested_recorder=self._nested_recorder,
                emit=events.emit,
            )
            outcome = outcome.model_copy(update={"context_note": note})
            report = conclude_agent_run(
                self._store, outcome, agent_run_id=run_id, now=now,
            )
        finally:
            self._runs.finish(run_id)
        return InvestigationOutcome(
            envelope=report.envelope, report=report, trace=outcome.trace,
            session_id=session, context_note=note,
        )

    def resume(
        self,
        agent_run_id: str,
        *,
        decision: str,
        answers: Any = None,
        now: Any = None,
        session_id: str | None = None,
        new_run_id: str | None = None,
    ) -> ResumeOutcome:
        """接回一次交还（[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md) 的续跑）。

        先按留痕取回任务与上下文（任务取自**旧留痕**，不要求调用方重述），再把注入的
        执行面 / 闸门 / 判据面 / 双上界原样交给 :func:`resume_agent_run`——续跑与首次运行
        **同一条流水线**，不另造执行路径。留痕取不到、或 `decision` 不认识时回显式信封，
        不抛裸异常到对话面。

        :param new_run_id: 续跑这次新开跑的标识（后台运行由调用方预铸，[D-154] B1）；
        缺省仍自行铸。
        """
        session = (session_id or "").strip() or None
        try:
            record = load_agent_run(self._store, str(agent_run_id or ""))
        except (AgentRunNotFoundError, AgentRuntimeValidationError) as exc:
            return ResumeOutcome(envelope=ResultEnvelope.validation_failed(str(exc)))
        run_id = str(new_run_id or "").strip() or new_agent_run_id()
        try:
            self._runs.register(run_id, session)
        except AgentRuntimeValidationError as exc:
            return ResumeOutcome(envelope=ResultEnvelope.validation_failed(str(exc)))
        try:
            history, memory, note = self._context_inputs(session, record.task)
            events = RunEventLog(self._store, run_id)
            result = resume_agent_run(
                self._store,
                record.agent_run_id,
                decision=decision,  # type: ignore[arg-type]
                answers=answers,
                endpoint_id=self._endpoint_id,
                protocol=self._protocol,
                runner=self._runner,
                tools=self._skills.tool_catalog(),
                gate=ActionGate(self._authorization),
                approved_permissions=self._permissions or (),
                max_steps=self._bounds.steps(),
                max_llm_calls=self._bounds.llm_calls(),
                budget_chars=self._bounds.budget_chars(),
                session_id=session,
                history=history,
                memory=memory,
                parallel_safe=True,
                nested_recorder=self._nested_recorder,
                now=now,
                new_run_id=run_id,
                emit=events.emit,
            )
        except AgentRuntimeValidationError as exc:
            return ResumeOutcome(envelope=ResultEnvelope.validation_failed(str(exc)))
        finally:
            self._runs.finish(run_id)
        if note:
            result = result.model_copy(update={"note": _join(result.note, note)})
        return result

    # ───────────────────── 运行控制面的只读口（T-AGT-023.1） ─────────────────────

    def new_run_id(self) -> str:
        """预铸一个运行标识（调用方在开跑前寻址用；与循环内铸号同源）。"""
        return new_agent_run_id()

    def is_active(self, agent_run_id: str) -> bool:
        """该运行是否仍在活动注册表内（结束后为 ``False``，属正常形态）。"""
        return self._runs.get(str(agent_run_id or "")) is not None

    def wait_started(self, agent_run_id: str, timeout: float = 5.0) -> bool:
        """有界等待一次预铸的运行**可寻址**（已登记或已有事件日志）。"""
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            if self.is_active(agent_run_id) or self.has_event_log(agent_run_id):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)

    def events(self, agent_run_id: str, *, after_seq: int = 0) -> tuple[Any, ...]:
        """按序回放一次运行的事件（缺日志即 ``AgentRunNotFoundError``）。"""
        return read_run_events(self._store, agent_run_id, after_seq=after_seq)

    def has_event_log(self, agent_run_id: str) -> bool:
        """该运行是否已有事件日志（崩溃后回放的判据；不抛、不猜）。"""
        try:
            read_run_events(self._store, agent_run_id)
        except (AgentRunNotFoundError, AgentRuntimeValidationError):
            return False
        return True

    def _context_inputs(self, session_id: str | None, task: str) -> tuple[Any, Any, str]:
        """取近端历史与记忆切片（[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)）。

        只并入 ``assemble_context`` 的**两个产出**（``messages`` / ``memory.slices``），不把
        会话上下文当工作上下文（调研报告 §8.4）。三种降级**各有显式标注**——无会话、
        会话读不出、读面未装配：都返回空上下文，但 ``context_note`` 不同，叫作方不能
        把「没有」与「没注入」混为一谈。
        """
        if session_id is None:
            return (), (), NO_SESSION_NOTE
        if self._sessions is None or self._reader is None:
            return (), (), CONTEXT_ABSENT_NOTE
        try:
            context = self._sessions.assemble_context(
                session_id, self._reader, topic=task,
                token_budget=self._bounds.budget_chars(),
            )
        except (SessionNotFoundError, SessionValidationError):
            return (), (), SESSION_INVALID_NOTE
        return tuple(context.messages), tuple(context.memory.slices), ""


def _join(*parts: str) -> str:
    return "；".join(part for part in parts if part)
