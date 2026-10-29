"""对话主入口、首页、快捷指令与会话历史（[T-UI-012.1] / [T-UI-032] / [T-UI-033]）。"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from typing import Any
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.ui.envelope import envelope_payload
from st_agent.ui.registry import slot_gaps

from st_agent.ui.surfaces.base import (
    SurfaceBase,
    _LOG,
    _now,
)


def _chat_reply_text(payload: dict[str, Any]) -> str:
    """助手轮的**中性文本摘要**（`chat_history` 的搜索与 Markdown 导出用）。

    完整内容在 `payload` 里；此处只给一行可检索的类型描述，不把机器载荷当正文写进
    历史（铁律 2：不含第一人称 / 拟人化措辞）。
    """
    if payload.get("needs_confirmation"):
        return "意图确认卡"
    continuation = payload.get("agent_continuation")
    if isinstance(continuation, dict):
        if continuation.get("kind") == "user_input":
            count = len(continuation.get("questions") or [])
            return f"查证待用户输入（{count} 问）"
        return "查证待确认（可一键续跑）"
    clarification = payload.get("clarification")
    if isinstance(clarification, dict):
        questions = clarification.get("questions")
        if isinstance(questions, list) and questions:
            return f"澄清追问（{len(questions)} 项）"
    description = payload.get("description")
    if isinstance(description, dict) and description.get("status") == "ok":
        data = description.get("data")
        kind = data.get("component_type") if isinstance(data, dict) else None
        return f"结果：{kind}" if kind else "结果：结构化展示"
    status = str(payload.get("status") or "failed")
    if status == "ok":
        return "已返回结果"
    reason = str(payload.get("reason") or "").strip()
    return f"{status}：{reason}" if reason else status


class ChatSurface(SurfaceBase):
    """对话主入口、首页、快捷指令与会话历史（[T-UI-012.1] / [T-UI-032] / [T-UI-033]）。"""

    def api_chat(self, body: dict[str, Any]) -> dict[str, Any]:
        """对话端点：经注入的门面走一段反向流，出站点仍过**同一条**校验。

        门面的返回含 `reply`（`ResultEnvelope`，附六态渲染语义）、可选
        `description`（`UiDescription`，走 :meth:`api_description` 的中性化门与必填槽）与可选
        `clarification`（澄清轮：问项 / 方向候选 / 超预算默认项，[05 §3.2]，原样透传）。
        未注入门面 → `unavailable` + 点名（不伪造，同 `api_health` 之外的各 fail-closed 面）。

        **本方法不向上抛**（[00 §6] 失败显式化）：非法请求体（门面在构造契约对象时
        拒绝，属 `ValueError` / `KeyError` 族）转 `validation_failed`；其余未预期异常
        记 traceback 后转 `failed`（附可查 `log_ref`）——两端都回信封，连接不被关闭。
        """
        if self.chat is None:
            return envelope_payload(
                ResultEnvelope.unavailable(
                    "未接入对话门面，/api/chat 不可用（装配归组合根）",
                    last_updated_at=_now(),
                )
            )
        try:
            result = self.chat.turn(body)
        except (ValueError, KeyError) as exc:
            # 请求体非法 → 门面构造契约对象时拒绝：pydantic 包装的 ValidationError 与 L3 各
            # *ValidationError 均属 ValueError；未知 session_id 属 SessionNotFoundError(KeyError)。
            _LOG.info("对话请求体被门面拒绝（%s）：%s", type(exc).__name__, exc)
            return envelope_payload(
                ResultEnvelope.validation_failed(
                    "对话请求体不合法，无法受理"
                    "（需非空 text；action=dispatch 需带待确认的 session_id）"
                )
            )
        except Exception as exc:  # noqa: BLE001 —— 真正的编程/内部错误：不吞，落日志 + 显式 failed
            log_ref = f"ui/chat-{uuid.uuid4().hex[:12]}"
            _LOG.exception("对话门面未预期异常（log_ref=%s）：%s", log_ref, exc)
            return envelope_payload(
                ResultEnvelope.failed("对话处理未预期失败，详见服务端日志", log_ref=log_ref)
            )
        reply = result.get("reply")
        payload = envelope_payload(reply) if isinstance(reply, ResultEnvelope) else {
            "status": "failed", "reason": "对话门面返回非法结构", "render": {},
        }
        payload["session_id"] = result.get("session_id")
        payload["needs_confirmation"] = bool(result.get("needs_confirmation"))
        payload["clarification"] = result.get("clarification")
        payload["agent_continuation"] = result.get("agent_continuation")
        description = result.get("description")
        if description is not None:
            described = envelope_payload(ResultEnvelope.ok(description))
            gaps = slot_gaps(description)
            verdict = self.neutrality_gate.check(description)
            if gaps or not verdict.passed:
                payload["description"] = envelope_payload(
                    ResultEnvelope.validation_failed(
                        "、".join(gaps) if gaps else verdict.reason
                    )
                )
            else:
                payload["description"] = described
        else:
            payload["description"] = None
        # 各视角推理链（[`T-UI-016.3`]）：`analyze` 去向的结论卡据此展开「推理链」面板——
        # 逐条与 `description` 同一道门（缺链条目 `description` 给 `None`，渲染面显式告知）。
        payload["traces"] = self._chat_traces(result.get("traces"))
        self._record_chat_reply(payload)
        return payload

    def _record_chat_reply(self, payload: dict[str, Any]) -> None:
        """把助手轮写入会话（[05 §1] 历史读面；实战由 `T-L3-008` 交付）。

        落盘失败**不影响**本次回复的出站（回复已经成形）；失败落日志、不伪造成功。
        会话标识缺席（请求被拒、未落会话）时跳过——没有可归属的会话。
        """
        session_id = payload.get("session_id")
        recorder = getattr(self.chat, "record_reply", None)
        if not session_id or not callable(recorder):
            return
        try:
            recorder(session_id, text=_chat_reply_text(payload), payload=payload)
        except Exception as exc:  # noqa: BLE001 —— 落盘失败不吞掉已成形回复
            _LOG.exception("助手轮落盘失败（session=%s）：%s", session_id, exc)

    def api_chat_sessions(self) -> dict[str, Any]:
        """会话历史列表（[05 §1] 历史读面）：按更新时间倒序的会话摘要。

        未注入对话门面、或门面未持 `list_sessions` 取数口 ⇒ `unavailable` + 点名
        （不伪造空列表，同 :meth:`api_chat_context` 的 fail-closed 口径）。
        """
        face = self.chat
        if face is None or not hasattr(face, "list_sessions"):
            return envelope_payload(
                ResultEnvelope.unavailable(
                    "未接入会话历史面：对话门面或 list_sessions 取数口缺席"
                    "（装配归组合根）",
                    last_updated_at=_now(),
                )
            )
        return self._call_face(
            "chat-sessions", face, "会话历史面", lambda f: f.list_sessions(),
        )

    def api_chat_session(self, session_id: str) -> dict[str, Any]:
        """读一个会话的消息（[05 §1] 历史读面）。未知标识走 `validation_failed`。"""
        face = self.chat
        if face is None or not hasattr(face, "read_session"):
            return envelope_payload(
                ResultEnvelope.unavailable(
                    "未接入会话读取面：对话门面或 read_session 取数口缺席"
                    "（装配归组合根）",
                    last_updated_at=_now(),
                )
            )
        return self._call_face(
            "chat-session", face, "会话读取面",
            lambda f: f.read_session(str(session_id or "")),
        )

    def _chat_traces(self, raw: Any) -> list[dict[str, Any]]:
        """对话返程里的各视角链 → 出站载荷（逐条过必填槽 + 中性化门）。"""
        out: list[dict[str, Any]] = []
        for entry in tuple(raw or ()):
            if not isinstance(entry, Mapping):
                continue
            description = entry.get("description")
            if description is None:
                described: Any = None
            else:
                try:
                    described = envelope_payload(
                        ResultEnvelope.ok(self._gate_description(description))
                    )
                except ValueError as exc:
                    described = envelope_payload(ResultEnvelope.validation_failed(str(exc)))
            out.append({
                "lens_id": entry.get("lens_id"),
                "trace_id": entry.get("trace_id"),
                "description": described,
            })
        return out

    def api_chat_context(self) -> dict[str, Any]:
        """首页上下文卡片（[05 §2]）：经注入的对话门面取卡片描述（`context_card`）。

        门面缺席、或其未持 `context_card` 取数口 ⇒ `unavailable` + 点名（不伪造一张卡片）。
        卡片六段与非 ok 段的「原因 + 生产方」由 L3 的 `describe_context_card` 给出，表现层
        只转发（[01 §5] 六态不混用）。
        """
        face = self.chat
        if face is None or not hasattr(face, "context_card"):
            return envelope_payload(
                ResultEnvelope.unavailable(
                    "未接入上下文卡片面：对话门面或 context_card 取数口缺席"
                    "（装配归组合根）",
                    last_updated_at=_now(),
                )
            )
        return self._call_face(
            "chat-context", face, "上下文卡片面", lambda f: f.context_card(),
        )

    def api_chat_commands(self) -> dict[str, Any]:
        """快捷指令清单（[05 §8]）：经注入的指令注册表取条目（名称 / 说明 / 意图 / 来源）。

        注册表未接 ⇒ `unavailable` + 点名（不摆假清单、不前端硬编码）。指令名与说明在
        注册时已过中性门（[01 §6] 执行点 1），故此处只转发、不复检。
        """
        return self._call_face(
            "chat-commands", self.commands, "快捷指令注册表",
            lambda face: self._commands_envelope(face),
        )

    def _commands_envelope(self, registry: Any) -> ResultEnvelope:
        commands = tuple(registry.list())
        if not commands:
            return ResultEnvelope.empty("快捷指令注册表为空（无可用指令）")
        return ResultEnvelope.ok(
            [
                {
                    "name": command.name,
                    "description": command.description,
                    "intent": command.intent,
                    "source": command.source,
                }
                for command in commands
            ]
        )
