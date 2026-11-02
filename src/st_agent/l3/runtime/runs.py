"""活动 run 注册表与开跑身份（T-AGT-022.1；[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md) / [D-154](../../../项目管理/决策日志.md) B1 / B9）。

循环**开跑前**铸 ``agent_run_id`` 并登记进进程内注册表，收口留痕沿用同一标识；注册表
持有活动 run 的**控制句柄**（身份 + 取消标记），结束后移除。

**两条口径**：

- **进程内瞬态、不落盘**——[D-154](../../../项目管理/决策日志.md) B1 的注册表只为「运行中的
  控制面」（停止 / SSE / 插话）提供寻址；跨进程恢复不属本叶，故不加任何 ``Store`` 写。
- **一个会话同一时刻只有一个活动 run**——[D-154](../../../项目管理/决策日志.md) B9。互斥只在
  ``session_id`` 非空时生效：无会话的单次查证没有可冲突的会话键，照常各自运行。

本模块只管「谁在跑」，**不跑循环、不落事件**：循环体在 :mod:`.loop`，事件日志在
:mod:`.events`。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from st_agent.l3.errors import AgentRuntimeValidationError
from st_agent.l3.runtime.steer import SteerInbox, SteerMessage

__all__ = [
    "ActiveRun",
    "ActiveRunRegistry",
    "RunAlreadyActiveError",
    "SteerInbox",
    "SteerMessage",
]


def _now() -> datetime:
    """用户本地时区当前时刻（与留痕的 ``recorded_at`` 同一口径）。"""
    return datetime.now().astimezone()


def _session_key(session_id: str | None) -> str | None:
    """空白串按 ``None`` 处理——与派发总线的会话口径一致（不臆造会话）。"""
    value = (session_id or "").strip()
    return value or None


class RunAlreadyActiveError(AgentRuntimeValidationError):
    """同一会话已有活动 run 时的显式拒绝（[D-154](../../../项目管理/决策日志.md) B9）。

    继承 ``AgentRuntimeValidationError``：派发总线把它按 ``validation_failed`` 呈现，
    不穿透成调用方侧的一次崩溃。
    """


@dataclass
class ActiveRun:
    """活动 run 的**控制句柄**（[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

    承载身份与**取消事件**：停止请求经 :meth:`request_cancel` 置位，循环在 LLM 调用与
    动作边界读取同一事件（[`T-AGT-024.1`](../../../项目管理/tasks/T-AGT-024.1-取消透传与cancelled终止.md)）。
    事件在 `register` 时创建，控制面与后台线程**共享同一对象**——不另造第二来源。
    """

    agent_run_id: str
    session_id: str | None = None
    started_at: datetime = field(default_factory=_now)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    steer_inbox: SteerInbox = field(default_factory=SteerInbox)
    """运行中插话的收件箱（[`T-AGT-027.1`](../../../项目管理/tasks/T-AGT-027.1-steer-inbox与上下文注入.md)）。
    端点线程写入、循环线程在步骤边界排空；进程内瞬态，不落盘。"""

    def request_cancel(self) -> None:
        """置位取消事件（**协作式**：实际终止发生在下一个 LLM / 动作边界）。"""
        self.cancel_event.set()

    def request_steer(self, text: str, *, now: datetime | None = None) -> SteerMessage:
        """把一条插话投进收件箱（空白文本显式拒绝）。"""
        return self.steer_inbox.add(text, now=now)

    @property
    def cancel_requested(self) -> bool:
        """是否已请求取消（读事件状态；只读视图，不另存一份标记）。"""
        return self.cancel_event.is_set()


class ActiveRunRegistry:
    """活动 run 的进程内注册表（线程安全；后台 run 落地后直接复用）。

    ``register`` 在**同一把锁**里判同会话冲突与重复标识，故并发启动也只有一个能登记成功。
    """

    def __init__(self, *, now: Callable[[], datetime] | None = None) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, ActiveRun] = {}
        self._by_session: dict[str, str] = {}
        self._now = now or _now

    def register(self, agent_run_id: str, session_id: str | None = None) -> ActiveRun:
        """登记一次开跑；同会话已有活动 run / 标识重复即显式失败。"""
        if not isinstance(agent_run_id, str) or not agent_run_id.strip():
            raise AgentRuntimeValidationError("活动 run 需要非空的 agent_run_id")
        session = _session_key(session_id)
        with self._lock:
            if agent_run_id in self._runs:
                raise AgentRuntimeValidationError(
                    f"活动 run 标识重复：{agent_run_id!r}（标识必须唯一）"
                )
            if session is not None and session in self._by_session:
                existing = self._by_session[session]
                raise RunAlreadyActiveError(
                    f"会话 {session!r} 已有活动 run {existing!r}："
                    "一个会话同一时刻只允许一个活动 run"
                    "（[D-154](../../../项目管理/决策日志.md) B9 / 05 §10），"
                    "请先停止当前运行再开新任务"
                )
            run = ActiveRun(
                agent_run_id=agent_run_id, session_id=session,
                started_at=self._now(),
            )
            self._runs[agent_run_id] = run
            if session is not None:
                self._by_session[session] = agent_run_id
            return run

    def get(self, agent_run_id: str) -> ActiveRun | None:
        """按 id 取控制句柄；不在册即 ``None``（结束后的正常形态，不抛）。"""
        with self._lock:
            return self._runs.get(agent_run_id)

    def active_for_session(self, session_id: str | None) -> ActiveRun | None:
        """取某会话的活动 run；无会话键或无活动 run 即 ``None``。"""
        session = _session_key(session_id)
        if session is None:
            return None
        with self._lock:
            agent_run_id = self._by_session.get(session)
            return None if agent_run_id is None else self._runs.get(agent_run_id)

    def finish(self, agent_run_id: str) -> bool:
        """移除一次运行（幂等）；返回本次是否真的移除了一个在册 run。"""
        with self._lock:
            run = self._runs.pop(agent_run_id, None)
            if run is None:
                return False
            if run.session_id is not None:
                self._by_session.pop(run.session_id, None)
            return True

    def active_ids(self) -> tuple[str, ...]:
        """当前活动 run 的标识（按登记顺序；供运行控制面与测试读面）。"""
        with self._lock:
            return tuple(self._runs)
