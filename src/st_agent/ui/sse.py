"""SSE 帧编码与运行事件续推（[T-AGT-023.1]；[05 §10](../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

本模块只做**格式转换与续推节奏**：事件从门面的只读运行面（
:meth:`~st_agent.composition.facades.chat.DialogFacade.run_events`）取，活动态取
:meth:`~st_agent.composition.facades.chat.DialogFacade.is_active`。帧形态按 SSE 规范：

- ``id: <seq>`` —— ``Last-Event-ID`` 续传的依据（事件日志的单调序号）；
- ``event: <11 类之一>`` —— 事件类型；
- ``data: <RunEvent JSON>`` —— 单行 JSON（模型载荷不换行）；
- 心跳 ``: ping``；结束 ``event: stream_end``（``reason`` = ``finished`` / ``inactive``）。

**不猜不挂**：日志在续推中消失按 ``inactive`` 收束；没有 ``run_finished`` 且 run 已不在
注册表（崩溃 / 进程结束）时回放完已有事件后同样以 ``inactive`` 收束——不挂空连接。
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

__all__ = ["DEFAULT_HEARTBEAT_SECONDS", "DEFAULT_POLL_SECONDS", "frames"]

DEFAULT_POLL_SECONDS = 0.2
"""事件日志的轮询间隔（单 run 事件量十到数十条；本机回环、单用户）。"""

DEFAULT_HEARTBEAT_SECONDS = 15.0
"""心跳注释的发送间隔（保持中间代理 / 浏览器连接活性）。"""


def _end(reason: str) -> str:
    return f"event: stream_end\ndata: {json.dumps({'reason': reason}, ensure_ascii=False)}\n\n"


def frames(
    face: Any,
    agent_run_id: str,
    *,
    after_seq: int = 0,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
) -> Iterator[str]:
    """按 ``after_seq`` 续推一次运行的事件，直到 ``run_finished`` 或运行不再活动。"""
    last = max(0, int(after_seq))
    since_heartbeat = 0.0
    while True:
        try:
            events = face.run_events(agent_run_id, after_seq=last)
        except KeyError:
            # 已登记但事件日志尚未落第一行（开跑与建仓之间有极短窗口）：继续等，
            # 不在活动 run 上误报「不活动」。
            if not face.is_active(agent_run_id):
                yield _end("inactive")
                return
            time.sleep(poll_seconds)
            since_heartbeat += poll_seconds
            if since_heartbeat >= heartbeat_seconds:
                since_heartbeat = 0.0
                yield ": ping\n\n"
            continue
        for event in events:
            last = int(event.seq)
            yield f"id: {event.seq}\nevent: {event.event}\ndata: {event.model_dump_json()}\n\n"
            if event.event == "run_finished":
                yield _end("finished")
                return
        if not face.is_active(agent_run_id):
            yield _end("inactive")
            return
        time.sleep(poll_seconds)
        since_heartbeat += poll_seconds
        if since_heartbeat >= heartbeat_seconds:
            since_heartbeat = 0.0
            yield ": ping\n\n"
