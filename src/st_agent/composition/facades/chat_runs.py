"""对话门面的**运行控制面**（[`T-AGT-023.1`](../../../项目管理/tasks/T-AGT-023.1-SSE端点与断线重连.md) /
[`T-AGT-027`](../../../项目管理/tasks/T-AGT-027-插话.md)）。

`DialogFacade` 的一组分面方法：活动 run 的只读寻址（SSE / 等待开跑 / 事件回放）、安全停止
与运行中插话。它们只经 `self._bus` 的鸭子端口 `investigations` 取用循环，会话落盘仍走
`self._sessions`——门面本体只保留编排，故本面单独成文件（体量守卫要求按面拆）。

Mixin 依赖宿主提供三件：``_bus``（须持 ``investigations`` 端口）、``_sessions``（会话存储）
与 ``_now``（时钟）。缺省不注入循环端口时各只读口按 ``False`` / ``ValueError`` fail-closed
（不伪造一次运行或一条事件）。
"""

from __future__ import annotations

from typing import Any

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l3.errors import SessionNotFoundError, SessionValidationError

__all__ = ["RunControlMixin"]


class RunControlMixin:
    """`DialogFacade` 的运行控制面（宿主提供 ``_bus`` / ``_sessions`` / ``_now``）。"""

    _bus: Any
    _sessions: Any
    _now: Any

    # ───────────────────── 运行控制面的只读口（T-AGT-023.1） ─────────────────────

    @property
    def run_available(self) -> bool:
        """循环端口是否装配（旧根为 ``False`` ⇒ SSE / 后台派发显式 ``unavailable``）。"""
        return self._run_port is not None

    @property
    def _run_port(self) -> Any:
        return getattr(self._bus, "investigations", None)

    def new_run_id(self) -> str:
        """预铸一个运行标识（后台派发在开跑前须先能寻址）。"""
        port = self._run_port
        if port is None:
            raise ValueError("未注入自主查证循环端口，无法启动后台运行（05 §10）")
        return port.new_run_id()

    def is_active(self, agent_run_id: str) -> bool:
        port = self._run_port
        return bool(port is not None and port.is_active(str(agent_run_id or "")))

    def wait_started(self, agent_run_id: str, timeout: float = 5.0) -> bool:
        port = self._run_port
        if port is None:
            return False
        return bool(port.wait_started(str(agent_run_id or ""), timeout=timeout))

    def run_events(self, agent_run_id: str, *, after_seq: int = 0) -> tuple[Any, ...]:
        port = self._run_port
        if port is None:
            raise ValueError("未注入自主查证循环端口，无法读取运行事件（05 §10）")
        return tuple(port.events(str(agent_run_id or ""), after_seq=after_seq))

    def has_event_log(self, agent_run_id: str) -> bool:
        port = self._run_port
        return bool(port is not None and port.has_event_log(str(agent_run_id or "")))

    def stop_run(self, agent_run_id: str) -> bool:
        """请求安全停止一次在册运行（[D-154] B6；协作式，不在册即 ``False``）。"""
        port = self._run_port
        if port is None:
            raise ValueError("未注入自主查证循环端口，无法停止运行（05 §10）")
        return bool(port.stop(str(agent_run_id or "")))

    def steer_run(
        self,
        agent_run_id: str,
        text: str,
        *,
        session_id: Any = None,
    ) -> ResultEnvelope:
        """提交一条**运行中插话**（[05 §10] / [`T-AGT-027`]）。

        消息**受理即落会话**（`steer=true`），再投进活动 run 的收件箱；运行已结束
        （不在册）时仍落为普通用户消息并回 ``delivered=false``——**不丢、不报错**。
        会话标识优先取活动 run 自身的归属（防止前端携带过期会话）；活动 run 无会话归属
        时显式拒绝（不把消息塞进别的会话）。
        """
        run_id = str(agent_run_id or "").strip()
        raw = str(text or "").strip()
        if not run_id:
            return ResultEnvelope.validation_failed(
                "缺少运行标识（需 /api/chat/run/<id>/steer）",
            )
        if not raw:
            return ResultEnvelope.validation_failed("插话内容不得为空（05 §10）")
        port = self._run_port
        steer = getattr(port, "steer", None) if port is not None else None
        if not callable(steer):
            return ResultEnvelope.unavailable(
                "未注入自主查证循环端口的插话口，运行中插话不可用（装配归组合根）",
                last_updated_at=self._now(),
            )
        run_session = getattr(port, "run_session", None)
        active_sid = run_session(run_id) if callable(run_session) else None
        if self.is_active(run_id) and not active_sid:
            return ResultEnvelope.validation_failed(
                "该运行未绑定会话，插话无法落盘（05 §10）",
            )
        sid = str(active_sid or session_id or "").strip()
        if not sid:
            return ResultEnvelope.validation_failed(
                "运行已结束且未携会话标识，插话无处归属（需 session_id）",
            )
        try:
            self._sessions.load(sid)
            self._sessions.append(sid, raw, now=self._now(), steer=True)
        except SessionNotFoundError:
            return ResultEnvelope.validation_failed(f"会话不存在：{sid}")
        except SessionValidationError as exc:
            return ResultEnvelope.failed(f"会话记录损坏，插话无法保存：{exc}")
        delivered = bool(steer(run_id, raw))
        note = (
            "已送达运行中任务，将在下一个步骤边界生效"
            if delivered else
            "运行已结束，插话已作为普通消息保留"
        )
        return ResultEnvelope.ok(
            {
                "agent_run_id": run_id,
                "session_id": sid,
                "delivered": delivered,
                "note": note,
            },
            as_of=self._now(),
        )
