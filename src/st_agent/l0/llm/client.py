"""LLM 统一调用器（02 §4；协商 → 取 Key → 流式 → 用量）。

职责：
- 调用前能力协商（GWT-2）：需求不满足 → 单个 ``error`` 事件
  （``validation_failed``），不触碰传输层
- 云端端点 Key 唯一经 ``CredentialVault.use()`` 取用（GWT-4），只在内存
  短暂持有，调用结束即释放；prompt/response 明文永不落盘、不进用量
- 统一流式协议（GWT-3）：``chunk* → done`` 或单个 ``error``；中断/取消/
  超时显式上报为 ``error`` 事件
- 用量本地统计（GWT-4）：每次调用记一条 ``LlmUsageRecord``（仅计数）
  入 ``execution_log`` 分区；失败调用记 ``failed`` 并给出原因
- 失败一律走 ``ResultEnvelope``（GWT-5）：端点宕机 → ``unavailable``；
  超时/取消/其他执行失败 → ``failed``（带可查 ``log_ref``）；
  凭据缺失 → ``dependency_failed``；输入/档位非法 → ``validation_failed``

传输层说明：实际网络发送由 T-L0-004 出网审计网关注入（02 §6 一切出网
经统一网关）；本模块默认无传输实现（调用即 ``unavailable``），测试与
本地端点经 ``transport`` 参数注入。传输签名::

    transport(endpoint, prompt, key, timeout_ms) -> Iterable[str]

传输异常映射：``TransportUnavailableError`` → ``unavailable``；
``TransportTimeoutError`` / ``TransportError`` → ``failed``。
超时同时在块边界检查（``timeout_ms``）；传输内部长时间阻塞由传输实现
自行超时并抛 ``TransportTimeoutError``。
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.llm.errors import LlmNotFoundError, LlmValidationError
from st_agent.l0.llm.models import (
    CapabilityRequirement,
    LlmEndpoint,
    LlmUsageRecord,
    StreamEvent,
    check_endpoint_id,
    estimate_tokens,
)

__all__ = [
    "LLM_USAGE_PREFIX",
    "LlmClient",
    "TransportCancelledError",
    "TransportError",
    "TransportTimeoutError",
    "TransportUnavailableError",
]

LLM_USAGE_PREFIX = "llm-usage/"
"""``execution_log`` 分区内 LLM 用量记录的目录前缀。"""

#: 传输 callable 协议：(endpoint, prompt, key, timeout_ms) -> 文本块可迭代
Transport = Callable[[LlmEndpoint, str, str | None, int], Iterable[str]]


class TransportError(Exception):
    """传输层执行失败基类（映射为 ``failed`` 信封）。"""


class TransportUnavailableError(TransportError):
    """端点不可达/未配置传输（映射为 ``unavailable`` 信封）。"""


class TransportTimeoutError(TransportError):
    """传输层超时（映射为 ``failed`` 信封，原因注明超时）。"""


class TransportCancelledError(TransportError):
    """传输层被取消（映射为 ``failed`` 信封，原因注明取消）。"""


def _now() -> datetime:
    """用户本地时区当前时刻（01 §8：时间统一用用户本地时区存储与展示）。"""
    return datetime.now().astimezone()


class LlmClient:
    """LLM 统一调用器（02 §4 门面；明文只在内存短暂持有，不落盘）。

    :param store: ``Store`` 句柄（用量记 ``execution_log`` 分区）
    :param registry: ``EndpointRegistry``（端点配置来源）
    :param vault: ``CredentialVault``（云端 Key 唯一取用口）
    :param transport: 传输实现（缺省为 None → 调用即 ``unavailable``）
    """

    def __init__(self, store, registry, vault, transport: Transport | None = None) -> None:
        self._store = store
        self._registry = registry
        self._vault = vault
        self._transport = transport

    # ───────────────────────── 统一调用（GWT-2/3/4/5） ────────────────────

    def invoke(
        self,
        endpoint_id: str,
        prompt: str,
        *,
        initiator: str = "",
        purpose: str = "",
        requirement: CapabilityRequirement | dict | None = None,
        timeout_ms: int | None = None,
        cancel: threading.Event | None = None,
    ) -> Iterator[StreamEvent]:
        """发起一次 LLM 调用，返回统一流式事件迭代器。

        正常：``chunk* → done``（``done`` 携带用量）；失败：单个 ``error``
        事件（携带 ``ResultEnvelope``）。用量无论成败必记一条（失败为
        ``failed``）。prompt 为空、需求不满足、prompt 超上下文 → 直接
        ``validation_failed``，不触碰传输层。
        """
        check_endpoint_id(endpoint_id)
        endpoint = self._registry.get(endpoint_id)
        if not isinstance(prompt, str) or not prompt:
            yield self._error_event(
                endpoint, ResultEnvelope.validation_failed("prompt 不得为空"),
                initiator=initiator, purpose=purpose,
                prompt_tokens=0, duration_ms=0,
            )
            return
        if not initiator or not purpose:
            yield self._error_event(
                endpoint,
                ResultEnvelope.validation_failed("initiator 与 purpose 必填（用量归属）"),
                initiator=initiator or "unknown", purpose=purpose or "unknown",
                prompt_tokens=estimate_tokens(prompt), duration_ms=0,
            )
            return
        req = self._coerce_requirement(requirement)
        ok, reason = self._registry.negotiate(endpoint_id, req)
        if not ok:
            yield self._error_event(
                endpoint, ResultEnvelope.validation_failed(reason),
                initiator=initiator, purpose=purpose,
                prompt_tokens=estimate_tokens(prompt), duration_ms=0,
            )
            return
        prompt_tokens = estimate_tokens(prompt)
        if prompt_tokens > endpoint.capability.max_context_tokens:
            yield self._error_event(
                endpoint,
                ResultEnvelope.validation_failed(
                    f"prompt 约 {prompt_tokens} tokens，超出端点 {endpoint_id!r} "
                    f"上下文上限 {endpoint.capability.max_context_tokens}"
                ),
                initiator=initiator, purpose=purpose,
                prompt_tokens=prompt_tokens, duration_ms=0,
            )
            return
        timeout = timeout_ms if timeout_ms is not None else endpoint.default_timeout_ms
        if timeout <= 0:
            yield self._error_event(
                endpoint, ResultEnvelope.validation_failed("timeout_ms 须为正数"),
                initiator=initiator, purpose=purpose,
                prompt_tokens=prompt_tokens, duration_ms=0,
            )
            return

        # 云端 Key：唯一取用口；缺失 → dependency_failed（凭据与调用解耦，GWT-5）
        key: str | None = None
        if endpoint.kind == "cloud":
            assert endpoint.credential_id is not None
            try:
                key = self._vault.use(
                    endpoint.credential_id, initiator=initiator, purpose=purpose
                )
            except KeyError as exc:
                yield self._error_event(
                    endpoint,
                    ResultEnvelope.dependency_failed(
                        f"端点 {endpoint_id!r} 的凭据 {endpoint.credential_id!r} "
                        "缺失或已吊销，无法调用"
                    ),
                    initiator=initiator, purpose=purpose,
                    prompt_tokens=prompt_tokens, duration_ms=0,
                )
                return

        try:
            yield from self._stream(
                endpoint, prompt, key, timeout, cancel,
                initiator=initiator, purpose=purpose, prompt_tokens=prompt_tokens,
            )
        finally:
            key = None  # 内存引用即时释放（GWT-4 零留存）
            del key

    # ───────────────────────── 用量查询（GWT-4） ─────────────────────────

    def query_usage(
        self,
        endpoint_id: str,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> tuple[LlmUsageRecord, ...]:
        """按端点查用量记录（时戳升序；``since``/``until`` 须带时区）。"""
        check_endpoint_id(endpoint_id)
        for bound in (since, until):
            if bound is not None and bound.tzinfo is None:
                raise LlmValidationError("查询边界时间必须带时区语义")
        prefix = f"{LLM_USAGE_PREFIX}{endpoint_id}/"
        entries = [
            self._parse_usage(
                json.loads(self._store.get("execution_log", name).decode("utf-8"))
            )
            for name in self._store.list_files("execution_log")
            if name.startswith(prefix) and name.endswith(".json")
        ]
        entries.sort(key=lambda e: e.timestamp)
        return tuple(
            e for e in entries
            if (since is None or e.timestamp >= since)
            and (until is None or e.timestamp <= until)
        )

    # ───────────────────────── 内部：流式执行 ─────────────────────────

    def _stream(
        self,
        endpoint: LlmEndpoint,
        prompt: str,
        key: str | None,
        timeout_ms: int,
        cancel: threading.Event | None,
        *,
        initiator: str,
        purpose: str,
        prompt_tokens: int,
    ) -> Iterator[StreamEvent]:
        started = time.monotonic()
        elapsed_ms = lambda: int((time.monotonic() - started) * 1000)
        if self._transport is None:
            now = _now()
            yield self._error_event(
                endpoint,
                ResultEnvelope.unavailable(
                    f"端点 {endpoint.endpoint_id!r} 无可用传输（等待 T-L0-004 "
                    "出网网关接入）",
                    last_updated_at=now, as_of=now,
                ),
                initiator=initiator, purpose=purpose,
                prompt_tokens=prompt_tokens, duration_ms=elapsed_ms(),
            )
            return
        if cancel is not None and cancel.is_set():
            yield self._fail(
                endpoint, "调用开始前已取消", initiator, purpose,
                prompt_tokens, 0, elapsed_ms(),
            )
            return
        chunks: list[str] = []
        try:
            stream = self._transport(endpoint, prompt, key, timeout_ms)
            for piece in stream:
                if cancel is not None and cancel.is_set():
                    yield self._fail(
                        endpoint, "调用中被取消", initiator, purpose,
                        prompt_tokens, estimate_tokens("".join(chunks)), elapsed_ms(),
                    )
                    return
                if elapsed_ms() > timeout_ms:
                    yield self._fail(
                        endpoint, f"调用超时（>{timeout_ms}ms）", initiator, purpose,
                        prompt_tokens, estimate_tokens("".join(chunks)), elapsed_ms(),
                    )
                    return
                chunks.append(piece)
                yield StreamEvent(kind="chunk", text=piece)
        except TransportUnavailableError as exc:
            now = _now()
            yield self._error_event(
                endpoint,
                ResultEnvelope.unavailable(str(exc), last_updated_at=now, as_of=now),
                initiator=initiator, purpose=purpose,
                prompt_tokens=prompt_tokens, duration_ms=elapsed_ms(),
            )
            return
        except (TransportTimeoutError, TransportCancelledError, TransportError) as exc:
            yield self._fail(
                endpoint, str(exc) or "传输失败", initiator, purpose,
                prompt_tokens, estimate_tokens("".join(chunks)), elapsed_ms(),
            )
            return
        completion_tokens = estimate_tokens("".join(chunks))
        usage, _ = self._write_usage(
            endpoint.endpoint_id, initiator=initiator, purpose=purpose,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            duration_ms=elapsed_ms(), status="ok",
        )
        yield StreamEvent(kind="done", usage=usage)

    # ───────────────────────── 内部：错误与用量 ─────────────────────────

    def _fail(
        self,
        endpoint: LlmEndpoint,
        reason: str,
        initiator: str,
        purpose: str,
        prompt_tokens: int,
        completion_tokens: int,
        duration_ms: int,
    ) -> StreamEvent:
        """构造 ``failed`` 错误事件（先记用量拿到 ``log_ref``，再包信封）。"""
        _, relpath = self._write_usage(
            endpoint.endpoint_id, initiator=initiator, purpose=purpose,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            duration_ms=duration_ms, status="failed", fail_reason=reason,
        )
        return StreamEvent(
            kind="error",
            error_envelope=ResultEnvelope.failed(reason, log_ref=relpath),
        )

    def _error_event(
        self,
        endpoint: LlmEndpoint,
        envelope: ResultEnvelope,
        *,
        initiator: str,
        purpose: str,
        prompt_tokens: int,
        duration_ms: int,
    ) -> StreamEvent:
        """构造非 ``failed`` 的错误事件（同步记一条 ``failed`` 用量供自查）。"""
        self._write_usage(
            endpoint.endpoint_id, initiator=initiator, purpose=purpose,
            prompt_tokens=prompt_tokens, completion_tokens=0,
            duration_ms=duration_ms, status="failed",
            fail_reason=envelope.reason or envelope.status,
        )
        return StreamEvent(kind="error", error_envelope=envelope)

    def _write_usage(
        self,
        endpoint_id: str,
        *,
        initiator: str,
        purpose: str,
        prompt_tokens: int,
        completion_tokens: int,
        duration_ms: int,
        status: str,
        fail_reason: str = "",
    ) -> tuple[LlmUsageRecord, str]:
        """写一条用量记录（只含计数；返回记录与分区内相对路径）。"""
        try:
            entry = LlmUsageRecord(
                endpoint_id=endpoint_id,
                timestamp=_now(),
                initiator=initiator,
                purpose=purpose,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                duration_ms=duration_ms,
                status=status,  # type: ignore[arg-type]
                fail_reason=fail_reason,
            )
        except ValidationError as exc:
            raise LlmValidationError(f"用量记录非法：{exc}") from exc
        stamp = entry.timestamp
        fname = f"{int(stamp.timestamp() * 1_000_000):020d}-{os.urandom(4).hex()}.json"
        relpath = f"{LLM_USAGE_PREFIX}{endpoint_id}/{fname}"
        self._store.put("execution_log", relpath, entry.model_dump_json().encode("utf-8"))
        return entry, relpath

    @staticmethod
    def _parse_usage(payload: dict[str, Any]) -> LlmUsageRecord:
        try:
            return LlmUsageRecord(**payload)
        except ValidationError as exc:
            raise LlmValidationError(f"用量记录损坏无法解析：{exc}") from exc

    @staticmethod
    def _coerce_requirement(
        requirement: CapabilityRequirement | dict | None,
    ) -> CapabilityRequirement:
        if requirement is None:
            return CapabilityRequirement()
        if isinstance(requirement, dict):
            try:
                return CapabilityRequirement(**requirement)
            except ValidationError as exc:
                raise LlmValidationError(f"能力需求非法：{exc}") from exc
        return requirement
