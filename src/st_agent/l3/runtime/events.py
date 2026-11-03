"""运行事件模型与 JSONL 追加日志（T-AGT-022.2；[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md) / [01 §4](../../../../docs/技术架构-v2/01-平台共享契约.md) / [D-154](../../../项目管理/决策日志.md) B5）。

运行事件是**运行态的追加记录**，不是推理链步骤——[01 §4](../../../../docs/技术架构-v2/01-平台共享契约.md)
不为它新增 ``step_type``、也不并入 ``steps[]``。本模块交付三件：

- **枚举**：D-154 B5 的 11 类事件 + 2026-11-02 增补的插话投递事件 ``steer`` + 2026-11-03 增补的预算申请事件 ``budget_request`` 与子运行开始事件 ``sub_agent_started``（:data:`RUN_EVENT_TYPES`）；
- **追加写**：:class:`RunEventLog` 以单调序号把事件写进 ``execution_log`` 分区下的
  ``agent-run-events/<agent_run_id>.jsonl``（每行一条 :class:`RunEvent`）；
- **回放**：:func:`read_run_events` 按序取回（供 ``T-AGT-023.1`` 的 SSE 断线重连 /
  崩溃后回放）。

**可见边界**：事件载荷只承载客观字段（任务 / 动作 / 状态 / 计划 / 结果 / 用量），模型中间
推理与中间轮文本**不入流**——该红线由循环侧「只发客观字段」保证，本模块不另做过滤。

**中性边界**：事件类型名与载荷均为状态 / 动作描述，不含拟人化措辞（[铁律 2](../../../项目管理/工程宪法.md)）。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from st_agent.l3.errors import AgentRunNotFoundError, AgentRuntimeValidationError

__all__ = [
    "EVENT_LOG_PREFIX",
    "RUN_EVENT_TYPES",
    "RunEvent",
    "RunEventLog",
    "RunEventType",
    "event_log_ref_of",
    "read_run_events",
]

RunEventType = Literal[
    "run_started",
    "model_turn",
    "plan_updated",
    "step_started",
    "step_finished",
    "tool_search",
    "sub_agent_started",
    "sub_agent",
    "gate_pending",
    "ask_user",
    "budget_request",
    "steer",
    "text_delta",
    "run_finished",
]
"""运行事件枚举（[D-154](../../../项目管理/决策日志.md) B5 的 11 类 + ``steer`` + ``budget_request`` + ``sub_agent_started``，与 [05 §10] 同序）。"""

RUN_EVENT_TYPES: tuple[str, ...] = (
    "run_started",
    "model_turn",
    "plan_updated",
    "step_started",
    "step_finished",
    "tool_search",
    "sub_agent_started",
    "sub_agent",
    "gate_pending",
    "ask_user",
    "budget_request",
    "steer",
    "text_delta",
    "run_finished",
)
"""枚举的**运行期形态**（校验用；与 :data:`RunEventType` 同源，不另写第二份）。"""

EVENT_LOG_PREFIX = "agent-run-events/"
"""事件日志在 ``execution_log`` 分区内的路径前缀（与 ``agent-run/`` / ``skill-run/`` 同分区）。"""

_FIELD = "execution_log"


def event_log_ref_of(agent_run_id: str) -> str:
    """事件日志的定位串（``agent-run-events/<agent_run_id>.jsonl``）。"""
    return f"{EVENT_LOG_PREFIX}{agent_run_id}.jsonl"


def _now() -> datetime:
    """用户本地时区当前时刻（同留痕 ``recorded_at`` 的口径）。"""
    return datetime.now().astimezone()


class RunEvent(BaseModel):
    """一条运行事件（[01 §4](../../../../docs/技术架构-v2/01-平台共享契约.md) 的记录形态）。

    ``seq`` 是**单次 run 内**的单调序号（从 1 起），SSE 的 ``Last-Event-ID`` 按它续传；
    ``at`` 是事件写入时刻，不冒充业务时间。
    """

    model_config = ConfigDict(frozen=True)

    seq: int = Field(ge=1)
    event: RunEventType
    agent_run_id: str = Field(min_length=1)
    at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class RunEventLog:
    """一次运行的事件**追加写**句柄（[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

    建仓时读回既有日志的末条序号，故「崩溃后重开」继续编号，不重排、不复用序号。
    ``emit`` 的未知事件类型**显式失败且不写行**（GWT-3）——事件枚举是契约，不是建议。
    """

    def __init__(
        self, store: Any, agent_run_id: str, *,
        now: Any = None,
    ) -> None:
        if not isinstance(agent_run_id, str) or not agent_run_id.strip():
            raise AgentRuntimeValidationError("事件日志需要非空的 agent_run_id")
        self._store = store
        self._agent_run_id = agent_run_id
        self._ref = event_log_ref_of(agent_run_id)
        self._now = now or _now
        self._seq = self._last_seq()

    @property
    def ref(self) -> str:
        """事件日志在 ``execution_log`` 分区内的相对路径。"""
        return self._ref

    @property
    def agent_run_id(self) -> str:
        return self._agent_run_id

    def emit(self, event_type: str, payload: Mapping[str, Any] | None = None) -> RunEvent:
        """追加一条事件；未知类型 / 载荷不可序列化即显式失败（不静默丢）。"""
        if event_type not in RUN_EVENT_TYPES:
            raise AgentRuntimeValidationError(
                f"未知的运行事件类型 {event_type!r}；允许：{'、'.join(RUN_EVENT_TYPES)}"
            )
        self._seq += 1
        event = RunEvent(
            seq=self._seq,
            event=event_type,  # type: ignore[arg-type]
            agent_run_id=self._agent_run_id,
            at=self._now(),
            payload=dict(payload or {}),
        )
        line = event.model_dump_json()
        existing = self._read_bytes()
        self._store.put(_FIELD, self._ref, existing + line.encode("utf-8") + b"\n")
        return event

    def events(self, *, after_seq: int = 0) -> tuple[RunEvent, ...]:
        """本 run 的事件（可按 ``after_seq`` 续读）。"""
        return read_run_events(
            self._store, self._agent_run_id, after_seq=after_seq,
        )

    # ───────────────────────── 内部 ─────────────────────────

    def _read_bytes(self) -> bytes:
        try:
            return self._store.get(_FIELD, self._ref)
        except KeyError:
            return b""

    def _last_seq(self) -> int:
        raw = self._read_bytes()
        if not raw:
            return 0
        return _parse_events(raw, self._ref, self._agent_run_id)[-1].seq


def read_run_events(
    store: Any, agent_run_id: str, *, after_seq: int = 0,
) -> tuple[RunEvent, ...]:
    """按序回放一次运行的事件（[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

    :param after_seq: 只返回序号**大于**它的条目（SSE 断线重连的 ``Last-Event-ID``）。
    日志不存在 → :class:`AgentRunNotFoundError`；坏行 / 未知事件类型 / 序号不单调 →
    :class:`AgentRuntimeValidationError`——回放不猜、不跳过坏行。
    """
    if not isinstance(agent_run_id, str) or not agent_run_id.strip():
        raise AgentRuntimeValidationError("回放需要非空的 agent_run_id")
    if not isinstance(after_seq, int) or isinstance(after_seq, bool) or after_seq < 0:
        raise AgentRuntimeValidationError(f"after_seq 须为 ≥0 的整数，得到 {after_seq!r}")
    ref = event_log_ref_of(agent_run_id)
    try:
        raw = store.get(_FIELD, ref)
    except KeyError as exc:
        raise AgentRunNotFoundError(
            f"取不到运行事件日志 {ref}（该 run 可能未产生事件或记录已清除）"
        ) from exc
    events = _parse_events(raw, ref, agent_run_id)
    return tuple(event for event in events if event.seq > after_seq)


def _parse_events(raw: bytes, ref: str, agent_run_id: str) -> tuple[RunEvent, ...]:
    """解析 JSONL 并核序号**连续且单调**（一个坏行即整体失败，不部分返回）。"""
    if not isinstance(raw, (bytes, bytearray)):
        raise AgentRuntimeValidationError(f"运行事件日志 {ref} 不是字节流")
    text = bytes(raw).decode("utf-8")
    events: list[RunEvent] = []
    previous = 0
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = RunEvent.model_validate_json(line)
        except Exception as exc:
            raise AgentRuntimeValidationError(
                f"运行事件日志 {ref} 第 {lineno} 行形态坏：{exc}"
            ) from exc
        if event.agent_run_id != agent_run_id:
            raise AgentRuntimeValidationError(
                f"运行事件日志 {ref} 第 {lineno} 行的 agent_run_id 与文件不符"
            )
        if event.seq != previous + 1:
            raise AgentRuntimeValidationError(
                f"运行事件日志 {ref} 第 {lineno} 行序号 {event.seq} 不连续"
                f"（期望 {previous + 1}）——事件流的完整性不能靠猜"
            )
        previous = event.seq
        events.append(event)
    return tuple(events)
