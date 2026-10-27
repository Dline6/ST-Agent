"""受控自主的查证循环实现（M5）。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.trace import Trace
from st_agent.l3.runtime import (
    ActionGate,
    AgentRunReport,
    LoopBounds,
    NativeToolCallProtocol,
    conclude_agent_run,
    investigate_family,
    run_agent_loop,
)


M5_ABSENT_TASK_REASON = (
    "investigate 去向缺少任务描述（05 §3.2：无 target Skill 的意图，其任务经确认卡取值 "
    "`task` 或 `target` 承载）；未运行自主查证循环（不臆造一个任务去跑）"
)
"""`investigate` 去向缺任务文本时的**显式**失败原因（不静默跑一个空任务）。"""


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
    ) -> None:
        self._store = store
        self._runner = runner
        self._skills = skills
        self._bounds = bounds
        self._protocol = protocol
        self._endpoint_id = endpoint_id
        self._authorization = authorization

    def investigate(
        self, confirmation: Any, *, values: Mapping[str, Any] | None = None, now: Any = None
    ) -> InvestigationOutcome:
        """跑一次自主查证循环并把产出装成信封（[05 §10]；载荷见 :class:`InvestigationOutcome`）。"""
        merged = {
            **dict(getattr(confirmation, "values", {}) or {}),
            **dict(values or {}),
        }
        task = str(merged.get("task") or getattr(confirmation, "target", "") or "").strip()
        if not task:
            return InvestigationOutcome(
                envelope=ResultEnvelope.validation_failed(M5_ABSENT_TASK_REASON)
            )
        outcome = run_agent_loop(
            task=task,
            endpoint_id=self._endpoint_id,
            protocol=self._protocol,
            runner=self._runner,
            tools=self._skills.tool_catalog(),
            gate=ActionGate(self._authorization),
            max_steps=self._bounds.steps(),
            max_llm_calls=self._bounds.llm_calls(),
        )
        report = conclude_agent_run(self._store, outcome, now=now)
        return InvestigationOutcome(
            envelope=report.envelope, report=report, trace=outcome.trace
        )
