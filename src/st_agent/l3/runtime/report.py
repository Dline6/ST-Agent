"""产出接入、留痕与失败语义（T-AGT-004.3；[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

循环跑完（或终止）之后的三条线，**每条都不另造**：

1. **产出＝结构化证据包**——循环交出 ``ResultEnvelope``，载荷是 [01 §12](../../../../docs/技术架构-v2/01-平台共享契约.md)
   的 **UI 描述**（``table``：各步的工具 / 执行目标 / 执行留痕 / 结果状态 / 留痕指针），
   由**既有**描述件 :func:`~st_agent.l3.render.describe.describe_agent_run` 产出、走
   **既有**组件注册表呈现——**不存在第二条渲染路径**（GWT-2）。模型的自由文本**不直接进
   ``data``**，只作渲染方的数据源之一（``answer`` 槽，逐槽标注 ``text_kinds``，GWT-1）。
   ``ok`` 信封另附 ``evidence_refs``（``kind="skill_run_id"``）：执行成功的各步即引用清单。
2. **留痕**——落 ``agent_run_id``（[01 §1](../../../../docs/技术架构-v2/01-平台共享契约.md)），
   记**步数 / 终止原因 / 上界**（[D-090](../../../项目管理/决策日志.md) ⑥）；落点沿用
   ``execution_log`` 分区（``agent-run/<agent_run_id>.json``，**不新开分区**——存储布局不构成
   契约，[01 §7](../../../../docs/技术架构-v2/01-平台共享契约.md) 的同时口径）。
3. **失败与降级**——端点不支持工具调用 / 不可用 → ``unavailable`` + **中性降级告知**、
   **fail-closed**（GWT-6）：本模块**从不**改跑 ``query`` 的确定性路径——那会让用户把
   「没跑成自主查证」误读为「这就是自主查证的结果」（[D-090](../../../项目管理/决策日志.md) ⑨）。
   单个工具失败**不终止**循环（[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)），
   该步在产出与留痕里**照实标注为失败**，**不呈现为成功、不静默略过**（GWT-7）。

**中性边界**：产出载荷**无** ``verdict`` / ``recommendation`` / ``advice`` 类字段
（[铁律 4](../../../项目管理/工程宪法.md)：多视角分歧并列、不给统一建议），证据**并列**呈现；
模型的中间推理（为何选下一步）已由循环丢弃（``loop`` 的口径），**不落盘、不出站**（GWT-4 / GWT-5）。
最终交付物的生成文案过 [01 §6](../../../../docs/技术架构-v2/01-平台共享契约.md) 执行点 2 的复检
落在**回环服务边界**（既有 ``ui/neutrality_gate.py``，[D-064](../../../项目管理/决策日志.md)），
本模块不另造一次（也不因此免检——``generated`` 槽逐槽标对才检得到）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from st_agent.contracts.identifiers import AgentRunId
from st_agent.contracts.result_envelope import EvidenceRef, ResultEnvelope
from st_agent.l3.errors import AgentRunNotFoundError, AgentRuntimeValidationError
from st_agent.l3.render.describe import describe_agent_run
from st_agent.l3.runtime.loop import LoopOutcome, TerminationReason

__all__ = [
    "AGENT_RUN_PREFIX",
    "AGENT_UNAVAILABLE_NOTICE",
    "AgentRunRecord",
    "AgentRunReport",
    "AgentRunStepRef",
    "agent_log_ref_of",
    "build_agent_output",
    "conclude_agent_run",
    "load_agent_run",
    "new_agent_run_id",
    "record_agent_run",
]

AGENT_RUN_PREFIX = "agent-run/"
"""循环留痕在 ``execution_log`` 分区内的路径前缀（与 ``skill-run/`` 同构、**不新开分区**）。"""

AGENT_UNAVAILABLE_NOTICE = (
    "本次未运行自主查证：所选端点未声明支持工具调用（或不可用），"
    "未执行任何能力调用；可在端点设置中改用支持工具调用的端点后重试。"
)
"""端点侧降级告知（GWT-6）。

与 [`:data:`LLM_DEGRADED_NOTICE <../dispatch/bus.py>`] **同口径**（中性、显式、给出可操作
去向）而**不同文案**：那条讲的是「端点暂不可用、可改本地推理」，本条讲的是「端点可用但不声明
工具调用」——原因不同，套用同一句会让用户按错误的处置去重试。
"""


def new_agent_run_id() -> str:
    """铸一个新的 ``agent_run_id``（[01 §1](../../../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    return AgentRunId.generate().value


def agent_log_ref_of(agent_run_id: str) -> str:
    """循环留痕的定位串（``agent-run/<agent_run_id>.json``）。"""
    return f"{AGENT_RUN_PREFIX}{agent_run_id}.json"


class AgentRunStepRef(BaseModel):
    """留痕里的一步**引用**（客观定位，载荷本身不复制进留痕）。

    ``skill_run_id`` 对应的完整记录由 L1 落在 ``execution_log`` 的 ``skill-run/`` 下——
    留痕只持指针，故**不产生第二份真相源**（审计时沿 ``log_ref`` 下钻）。
    """

    model_config = ConfigDict(frozen=True)

    tool_name: str = Field(min_length=1)
    skill_id: str = Field(min_length=3)
    skill_run_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    """该步的结果状态（[01 §5](../../../../docs/技术架构-v2/01-平台共享契约.md) 六态之一；
    失败步**照实**记 ``failed`` / ``validation_failed`` 等，不粉饰）。"""
    truncated: bool = False
    log_ref: str = Field(min_length=1)


class AgentRunRecord(BaseModel):
    """一次循环的留痕（[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)：步数 / 终止原因 / 上界）。

    与本次派发**共用 ``trace_id``**（[01 §1](../../../../docs/技术架构-v2/01-平台共享契约.md)）——
    故 `explain` 去向能据它找回那条只含动作步的链。
    """

    model_config = ConfigDict(frozen=True)

    agent_run_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    endpoint_id: str = Field(min_length=1)
    task: str = Field(min_length=1)
    termination: TerminationReason
    reason: str = ""
    at_bound: bool = False
    """是否因达到上界而终止（[D-090](../../../项目管理/决策日志.md) ⑥ 的如实标注）。"""
    steps: tuple[AgentRunStepRef, ...] = ()
    llm_calls: int = Field(default=0, ge=0)
    max_steps: int = Field(ge=1)
    """本次生效的**步数上界**（取值可注入，故记实际生效值而非缺省值）。"""
    max_llm_calls: int = Field(ge=1)
    """本次生效的 **LLM 调用次数上界**。"""
    at_bound_kind: str = ""
    """触界的那一条（``step_limit`` / ``llm_call_limit``；未触界为空）。"""
    recorded_at: datetime
    """本次留痕**写入**的时刻——**不伪造**运行起止时间：循环本体不持时钟，
    记一个没测过的 duration 比不记更糟。"""

    @property
    def steps_count(self) -> int:
        """步数（[D-090](../../../项目管理/决策日志.md) ⑥ 的三项之一）。"""
        return len(self.steps)


class AgentRunReport(BaseModel):
    """一次收口的完整交付（留痕 + 产出信封 + 标识），供派发方一次取走。"""

    model_config = ConfigDict(frozen=True)

    agent_run_id: str
    record: AgentRunRecord
    envelope: ResultEnvelope


def _now() -> datetime:
    """用户本地时区当前时刻（[01 §8](../../../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    return datetime.now().astimezone()


def record_agent_run(
    store: Any, outcome: LoopOutcome, *, agent_run_id: str, now: datetime | None = None,
) -> AgentRunRecord:
    """把一次循环落成留痕（``execution_log`` 分区；只写不读）。

    :param agent_run_id: 本次循环的标识（由 :func:`new_agent_run_id` 或调用方铸）
    """
    if not isinstance(agent_run_id, str) or not agent_run_id.strip():
        raise AgentRuntimeValidationError("留痕需要 agent_run_id（不得为空）")
    record = AgentRunRecord(
        agent_run_id=agent_run_id,
        trace_id=outcome.trace.trace_id.value,
        endpoint_id=outcome.endpoint_id,
        task=outcome.task,
        termination=outcome.termination,
        reason=outcome.reason,
        at_bound=outcome.at_bound,
        at_bound_kind=outcome.termination if outcome.at_bound else "",
        steps=tuple(
            AgentRunStepRef(
                tool_name=step.tool_name, skill_id=step.skill_id,
                skill_run_id=step.skill_run_id, status=step.envelope.status,
                truncated=step.truncated, log_ref=step.log_ref,
            )
            for step in outcome.steps
        ),
        llm_calls=outcome.llm_calls,
        max_steps=outcome.max_steps,
        max_llm_calls=outcome.max_llm_calls,
        recorded_at=now if now is not None else _now(),
    )
    store.put("execution_log", agent_log_ref_of(agent_run_id),
              record.model_dump_json().encode("utf-8"))
    return record


def load_agent_run(store: Any, agent_run_id: str) -> AgentRunRecord:
    """审计读面：按 ``agent_run_id`` 取回留痕（取不到即显式失败，不返回空壳）。"""
    name = agent_log_ref_of(agent_run_id)
    try:
        raw = store.get("execution_log", name)
    except Exception as exc:  # 存储侧寻址失败一律归为「取不到」，不吞成因
        raise AgentRunNotFoundError(f"取不到循环留痕 {name}：{exc}") from exc
    try:
        return AgentRunRecord.model_validate_json(raw)
    except Exception as exc:
        raise AgentRunNotFoundError(f"循环留痕 {name} 形态坏：{exc}") from exc


def build_agent_output(
    outcome: LoopOutcome, *, agent_run_id: str, now: datetime | None = None,
) -> ResultEnvelope:
    """把一次循环的产出装成信封（[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 的产出形态）。

    映射是**穷尽**的（一圈终止原因一条去向，无「其余」兜底）：

    | 终止原因 | 产出 |
    | --- | --- |
    | ``finished`` | ``ok``（证据包；无步亦可——模型直接作答是合法结果） |
    | ``step_limit`` / ``llm_call_limit`` / ``needs_confirmation`` / ``denied`` | 有步 → ``ok`` + 终止标注；无步 → ``empty`` + 原因 |
    | ``unknown_tool`` / ``gate_absent`` | 有步 → ``ok``；无步 → ``failed`` + 留痕指针 |
    | ``endpoint_unavailable`` | ``unavailable`` + 中性降级告知（fail-closed，GWT-6） |
    | ``llm_failed`` | 端点侧的信封**原样透出**（不重包、不改 status、不吞 reason） |
    | ``catalog_empty`` | ``empty`` + 原因 |

    ``ok`` 态另附 ``evidence_refs``（``kind="skill_run_id"``）——**仅成功步**入引用清单：
    失败步的证据价值是「这步失败了」，不该混进「本次结论依据」里。

    ``failed`` 分支的 ``log_ref`` 指向**留痕记录**——调用方须先落留痕再取产出
    （:func:`conclude_agent_run` 即按此序），否则指针会落空。
    """
    moment = now if now is not None else _now()
    log_ref = agent_log_ref_of(agent_run_id)
    if outcome.termination == "llm_failed" and outcome.failure is not None:
        return outcome.failure
    if not outcome.steps and not outcome.finished:
        return _without_steps(outcome, log_ref, moment)
    description = describe_agent_run(outcome, agent_run_id=agent_run_id, now=now)
    if description.status != "ok":
        return description  # 描述件自己判了不合 §12：原样透出它的 validation_failed
    return ResultEnvelope.ok(
        description.data, evidence_refs=_evidence_refs(outcome),
    )


def conclude_agent_run(
    store: Any, outcome: LoopOutcome, *, now: datetime | None = None,
) -> AgentRunReport:
    """收口一次循环：铸标识 → 落留痕 → 装产出（[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

    顺序有意如此：**先落留痕再装产出**——产出里的 ``log_ref`` 指向的那条记录因此必然已存在
    （「指针指向真实存在的记录」不靠事后补写）。
    """
    agent_run_id = new_agent_run_id()
    record = record_agent_run(store, outcome, agent_run_id=agent_run_id, now=now)
    envelope = build_agent_output(outcome, agent_run_id=agent_run_id, now=now)
    return AgentRunReport(agent_run_id=agent_run_id, record=record, envelope=envelope)


# ───────────────────────── 内部 ─────────────────────────


def _evidence_refs(outcome: LoopOutcome) -> tuple[EvidenceRef, ...]:
    """本次结论的引用清单（各**成功**步的 ``skill_run_id``，[01 §5](../../../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    return tuple(
        EvidenceRef(kind="skill_run_id", ref=step.skill_run_id)
        for step in outcome.steps
        if step.envelope.status == "ok"
    )


def _without_steps(
    outcome: LoopOutcome, log_ref: str, moment: datetime,
) -> ResultEnvelope:
    """一步未执行时的终态——**不**装一张空表冒充「已交付证据」。"""
    termination = outcome.termination
    reason = outcome.reason or "本次循环未执行任何动作"
    if termination == "endpoint_unavailable":
        # 该时刻不代表数据截止时间（同 DispatchBus 对未接入去向的写法）——如实写明，
        # 不冒充「最后更新时间 T」（本去向压根没有数据落地）
        return ResultEnvelope.unavailable(
            f"{reason}；{AGENT_UNAVAILABLE_NOTICE}"
            f"（记录时刻 {moment.isoformat()}，该时刻不代表数据截止时间）",
            last_updated_at=moment,
        )
    if termination in ("catalog_empty", "needs_confirmation", "denied",
                       "step_limit", "llm_call_limit"):
        return ResultEnvelope.empty(reason)
    return ResultEnvelope.failed(reason, log_ref=log_ref)
