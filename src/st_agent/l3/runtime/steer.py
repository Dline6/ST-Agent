"""运行中插话的收件箱（[`T-AGT-027.1`](../../../项目管理/tasks/T-AGT-027.1-steer-inbox与上下文注入.md)；
[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

插话只在**步骤边界**接受：端点把消息投进活动 run 的收件箱，循环在每轮 LLM 调用前排空，
按到达顺序并入下一轮工作上下文（带「运行中插入」标记）。收件箱是**进程内瞬态**——
它只服务「运行中的控制面」，落盘与会话归属由 `SessionStore` 的 `steer` 标记承担。

两条口径：

- **线程安全**：端点线程写、循环线程读；同一把锁保护列表，排空即取走（不重复注入）。
- **不丢**：与「模型给出结束」竞态时仍留在收件箱的消息，由循环以 ``delivered=False``
  收口；端点已在受理时把消息落会话，故它作为下一条普通消息保留（[05 §10]）。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from st_agent.l3.errors import AgentRuntimeValidationError

__all__ = [
    "SteerInbox",
    "SteerMessage",
]


def _now() -> datetime:
    """用户本地时区当前时刻（与留痕 ``recorded_at`` 同一口径）。"""
    return datetime.now().astimezone()


class SteerMessage(BaseModel):
    """一条运行中插话（文本 + 接收时刻 + 排空结果）。

    ``delivered`` 为假表示它**没有**进入本次运行的模型上下文：运行在排空前已结束，
    消息转为下一条普通消息（仍已在会话里）。``after_steps`` 是排空时本次运行**已执行**
    的步数——留痕据此还原「插话是在第几步之后到达的」。
    """

    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1)
    received_at: datetime
    delivered: bool = True
    after_steps: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _shape(self) -> "SteerMessage":
        if self.received_at.tzinfo is None:
            raise AgentRuntimeValidationError(
                "插话的接收时刻必须带时区语义（01 §8：内部传输带时区）"
            )
        if not self.text.strip():
            raise AgentRuntimeValidationError("插话文本不得为空白")
        return self


class SteerInbox:
    """活动 run 的线程安全收件箱（进程内瞬态，不落盘）。"""

    def __init__(self, *, now: Callable[[], datetime] | None = None) -> None:
        self._lock = threading.Lock()
        self._items: list[SteerMessage] = []
        self._now = now or _now

    def add(self, text: str, *, now: datetime | None = None) -> SteerMessage:
        """投递一条插话；空白文本显式拒绝（不静默丢、也不吞成空消息）。"""
        raw = str(text or "").strip()
        if not raw:
            raise AgentRuntimeValidationError("插话内容不得为空（05 §10）")
        message = SteerMessage(
            text=raw, received_at=now if now is not None else self._now(),
        )
        with self._lock:
            self._items.append(message)
        return message

    def drain(self) -> tuple[SteerMessage, ...]:
        """取走当前全部待投递消息（按到达顺序；排空后收件箱清空）。"""
        with self._lock:
            items = tuple(self._items)
            self._items.clear()
            return items

    @property
    def pending(self) -> int:
        """当前待投递条数（只读视图；供端点回执与测试）。"""
        with self._lock:
            return len(self._items)
