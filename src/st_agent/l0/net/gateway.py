"""出网审计网关（02 §6；全平台唯一出网出口）。

布局（只经 ``Store`` 读写，不直连文件系统）：
- 审计记录 → ``execution_log`` 分区 ``net/<kind>/<ts>-<rand>.json``
  （整文件加密落盘；断网可查历史 = 读本地加密分区，GWT-2）
- 能力声明 → ``config`` 分区 ``net-capability/<capability_id>.json``
  （02 §7 离线分级，GWT-3）

执行语义：
- ``execute``：调用方注入 ``sender``（真实发包实现），网关负责发起前
  校验 → 计时 → 落审计 → 返回 ``ResultEnvelope``（成功 ``ok`` data 为
  ``bytes_in`` 计数；失败按 GWT-5 映射分支）
- ``stream``：流式形态（供 LLM 适配器）：``chunk* → done`` 或抛 ``Egress*``
  异常；审计事件无论成败先行落盘
- 离线拦截：``set_online(False)`` 后任何 ``execute`` / ``stream`` 直接记
  ``unavailable``（pending_reconnect=True），不触碰 ``sender``（GWT-4）
- 审计纪律：``NetworkEvent`` 只含计数与目的说明；``purpose``/``detail``
  构造期拒绝内容/凭据字段名；调用方不得把 prompt/response/key 传给网关——
  网关签名只有字节数（GWT-4/5）

``NetworkRequestLogged`` 事件按 01 §11 由查询侧按需组装 ``PlatformEvent``
（payload 只含记录路径与计数），本模块不直接发事件。
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
from st_agent.l0.net.errors import (
    CapabilityExistsError,
    CapabilityNotFoundError,
    EgressCancelledError,
    EgressError,
    EgressTimeoutError,
    EgressUnavailableError,
    NetValidationError,
)
from st_agent.l0.net.models import (
    CapabilityRecord,
    NetworkEvent,
    NetworkRequestKind,
    OfflineReport,
    check_capability_id,
)

__all__ = [
    "CAPABILITY_PREFIX",
    "NET_LOG_PREFIX",
    "EgressGateway",
    "Sender",
    "SenderResult",
]

NET_LOG_PREFIX = "net/"
"""``execution_log`` 分区内出网审计记录的目录前缀。"""

CAPABILITY_PREFIX = "net-capability/"
"""``config`` 分区内离线能力声明的目录前缀。"""

#: 发包实现协议：(kind, target_host, timeout_ms) -> (bytes_out, bytes_in, chunks?)
#: chunks 供流式形态消费；一次性形态忽略。抛 ``Egress*`` 异常上报失败。
SenderResult = tuple[int, int, Iterable[str]]
Sender = Callable[[NetworkRequestKind, str, int], SenderResult]


def _now() -> datetime:
    """用户本地时区当前时刻（01 §8：时间统一用用户本地时区存储与展示）。"""
    return datetime.now().astimezone()


def _checked_event(**fields: Any) -> NetworkEvent:
    try:
        return NetworkEvent(**fields)
    except ValidationError as exc:
        raise NetValidationError(f"审计记录非法：{exc}") from exc


def _checked_capability(**fields: Any) -> CapabilityRecord:
    try:
        return CapabilityRecord(**fields)
    except ValidationError as exc:
        raise NetValidationError(f"能力声明非法：{exc}") from exc


class EgressGateway:
    """出网审计网关（02 §6 门面；全平台唯一出网出口）。

    :param store: ``Store`` 句柄（审计记 ``execution_log``，声明记 ``config``）
    :param sender: 发包实现（缺省为 None → 调用即 ``unavailable``，测试与
        离线路径经此判定；在线真实发包由调用方注入）
    :param online: 初始在线状态（默认在线；断网时 ``set_online(False)``）
    """

    def __init__(
        self,
        store,
        sender: Sender | None = None,
        *,
        online: bool = True,
    ) -> None:
        self._store = store
        self._sender = sender
        self._online = online

    # ───────────────────────── 在线状态 ─────────────────────────

    @property
    def online(self) -> bool:
        return self._online

    def set_online(self, online: bool) -> None:
        """切换在线/离线状态（离线横幅的数据来源之一；02 §7）。"""
        self._online = bool(online)

    # ───────────────────────── 一次性执行（GWT-1/2/5） ────────────────────

    def execute(
        self,
        kind: str,
        target_host: str,
        *,
        initiator: str = "",
        purpose: str = "",
        bytes_out: int = 0,
        timeout_ms: int = 60_000,
        trace_id: str | None = None,
        cancel: threading.Event | None = None,
        sender: Sender | None = None,
    ) -> ResultEnvelope:
        """经网关执行一次出网请求，返回 ``ResultEnvelope``。

        成功 ``ok``（data 为 ``{"bytes_in": n}`` 计数）；目标不可达/离线拦截/
        无发包实现 → ``unavailable``；超时/取消/发送失败 → ``failed``（带
        ``log_ref`` 指向审计记录）。审计事件无论成败必记一条。

        :param sender: 按次发包实现（缺省用构造时 ``sender``；T-L0-005 数据源
            同步经此注入按操作分发的抓取闭包——网关仍是唯一出口与唯一审计点）
        """
        started = time.monotonic()
        elapsed_ms = lambda: int((time.monotonic() - started) * 1000)
        try:
            req_kind = self._check_request(kind, target_host, initiator, purpose,
                                           bytes_out, timeout_ms)
        except NetValidationError as exc:
            # 发起前校验失败：不产生审计记录（无出网事实），直接显式化
            return ResultEnvelope.validation_failed(str(exc))
        if cancel is not None and cancel.is_set():
            relpath = self._log(req_kind, target_host, initiator, purpose,
                                bytes_out, 0, "cancelled", "调用开始前已取消")
            return ResultEnvelope.failed("调用开始前已取消", log_ref=relpath)
        if not self._online:
            relpath = self._log(req_kind, target_host, initiator, purpose,
                                bytes_out, 0, "unavailable",
                                "离线模式：请求已拦截，未发出（网络恢复后按需重发）")
            return ResultEnvelope.unavailable(
                "离线模式：请求未发出（pending_reconnect）",
                last_updated_at=_now(), as_of=_now(),
            )
        active_sender = sender if sender is not None else self._sender
        if active_sender is None:
            relpath = self._log(req_kind, target_host, initiator, purpose,
                                bytes_out, 0, "unavailable",
                                f"{target_host} 无可用发包实现")
            _ = relpath
            now = _now()
            return ResultEnvelope.unavailable(
                f"{target_host} 无可用发包实现", last_updated_at=now, as_of=now,
            )
        try:
            sent_out, got_in, _ = active_sender(req_kind, target_host, timeout_ms)
        except EgressUnavailableError as exc:
            relpath = self._log(req_kind, target_host, initiator, purpose,
                                bytes_out, 0, "unavailable", str(exc) or "目标不可达")
            now = _now()
            return ResultEnvelope.unavailable(
                str(exc) or "目标不可达", last_updated_at=now, as_of=now,
            )
        except (EgressTimeoutError, EgressCancelledError, EgressError) as exc:
            relpath = self._log(req_kind, target_host, initiator, purpose,
                                bytes_out, 0,
                                "cancelled" if isinstance(exc, EgressCancelledError)
                                else "failed",
                                str(exc) or "发送失败")
            return ResultEnvelope.failed(str(exc) or "发送失败", log_ref=relpath)
        self._log(req_kind, target_host, initiator, purpose,
                  bytes_out, got_in, "ok", "")
        _ = (trace_id, elapsed_ms, sent_out)
        return ResultEnvelope.ok({"bytes_in": got_in})

    # ───────────────────────── 流式执行（供 LLM 适配器，GWT-4） ────────────

    def stream(
        self,
        kind: str,
        target_host: str,
        *,
        initiator: str = "",
        purpose: str = "",
        bytes_out: int = 0,
        timeout_ms: int = 60_000,
        cancel: threading.Event | None = None,
    ) -> Iterator[str]:
        """经网关执行流式出网，产出文本块；失败抛 ``Egress*`` 异常。

        审计事件无论成败先行落盘（成功 ``ok`` / 离线 ``unavailable`` /
        超时 ``failed`` / 取消 ``cancelled``）。发起前校验失败抛
        ``NetValidationError``（无出网事实，不记审计）。
        """
        req_kind = self._check_request(kind, target_host, initiator, purpose,
                                       bytes_out, timeout_ms)
        if cancel is not None and cancel.is_set():
            self._log(req_kind, target_host, initiator, purpose,
                      bytes_out, 0, "cancelled", "调用开始前已取消")
            raise EgressCancelledError("调用开始前已取消")
        if not self._online:
            self._log(req_kind, target_host, initiator, purpose,
                      bytes_out, 0, "unavailable",
                      "离线模式：请求已拦截，未发出（网络恢复后按需重发）")
            raise EgressUnavailableError("离线模式：请求未发出（pending_reconnect）")
        if self._sender is None:
            self._log(req_kind, target_host, initiator, purpose,
                      bytes_out, 0, "unavailable", f"{target_host} 无可用发包实现")
            raise EgressUnavailableError(f"{target_host} 无可用发包实现")
        started = time.monotonic()
        elapsed_ms = lambda: int((time.monotonic() - started) * 1000)
        bytes_in = 0
        try:
            _, _, chunks = self._sender(req_kind, target_host, timeout_ms)
            for piece in chunks:
                if cancel is not None and cancel.is_set():
                    self._log(req_kind, target_host, initiator, purpose,
                              bytes_out, bytes_in, "cancelled", "调用中被取消")
                    raise EgressCancelledError("调用中被取消")
                if elapsed_ms() > timeout_ms:
                    self._log(req_kind, target_host, initiator, purpose,
                              bytes_out, bytes_in, "failed",
                              f"调用超时（>{timeout_ms}ms）")
                    raise EgressTimeoutError(f"调用超时（>{timeout_ms}ms）")
                bytes_in += len(piece.encode("utf-8"))
                yield piece
        except EgressError:
            raise
        except Exception as exc:  # 发包实现抛裸异常 → 包成 failed 审计
            self._log(req_kind, target_host, initiator, purpose,
                      bytes_out, bytes_in, "failed", f"发送失败：{exc}")
            raise EgressError(f"发送失败：{exc}") from exc
        self._log(req_kind, target_host, initiator, purpose,
                  bytes_out, bytes_in, "ok", "")

    # ───────────────────────── 查询与导出（GWT-2） ─────────────────────────

    def query(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
        *,
        kind: str | None = None,
        initiator: str | None = None,
        status: str | None = None,
    ) -> tuple[NetworkEvent, ...]:
        """按条件查审计记录（时戳升序；``since``/``until`` 须带时区）。

        不传时间边界即查全部（断网可查历史 = 读本地加密分区）。
        """
        for bound in (since, until):
            if bound is not None and bound.tzinfo is None:
                raise NetValidationError("查询边界时间必须带时区语义")
        entries = [
            self._parse_event(
                json.loads(self._store.get("execution_log", name).decode("utf-8"))
            )
            for name in self._store.list_files("execution_log")
            if name.startswith(NET_LOG_PREFIX) and name.endswith(".json")
        ]
        entries.sort(key=lambda e: e.timestamp)
        return tuple(
            e for e in entries
            if (since is None or e.timestamp >= since)
            and (until is None or e.timestamp <= until)
            and (kind is None or e.kind == kind)
            and (initiator is None or e.initiator == initiator)
            and (status is None or e.status == status)
        )

    def export_report(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> dict:
        """导出网络活动报告（面板展示与导出的数据形态；只含计数，无明文）。"""
        events = self.query(since, until)
        return {
            "generated_at": _now().isoformat(),
            "count": len(events),
            "events": [e.model_dump(mode="json") for e in events],
        }

    def pending_reconnect(self) -> tuple[NetworkEvent, ...]:
        """待补发查询（离线拦截的记录；补发执行由 L5 按需消费，假设 B）。"""
        return self.query(status="unavailable")

    # ───────────────────────── 离线能力分级（GWT-3） ───────────────────────

    def register_capability(
        self,
        capability_id: str,
        display_name: str,
        offline_level: str,
        degrade_note: str = "",
    ) -> CapabilityRecord:
        """登记一项平台能力的离线声明（已存在 → ``CapabilityExistsError``）。"""
        record = _checked_capability(
            capability_id=check_capability_id(capability_id),
            display_name=display_name,
            offline_level=offline_level,
            degrade_note=degrade_note,
        )
        path = self._capability_path(record.capability_id)
        if path in self._store.list_files("config"):
            raise CapabilityExistsError(
                f"能力 {record.capability_id!r} 已登记；更新请用 update_capability"
            )
        self._store.put("config", path, record.model_dump_json().encode("utf-8"))
        return record

    def update_capability(
        self,
        capability_id: str,
        *,
        display_name: str | None = None,
        offline_level: str | None = None,
        degrade_note: str | None = None,
    ) -> CapabilityRecord:
        """更新能力声明（整体替换；不存在 → ``CapabilityNotFoundError``）。"""
        old = self.get_capability(capability_id)
        record = _checked_capability(
            capability_id=old.capability_id,
            display_name=display_name if display_name is not None else old.display_name,
            offline_level=offline_level if offline_level is not None else old.offline_level,
            degrade_note=degrade_note if degrade_note is not None else old.degrade_note,
        )
        self._store.put(
            "config", self._capability_path(capability_id),
            record.model_dump_json().encode("utf-8"),
        )
        return record

    def remove_capability(self, capability_id: str) -> None:
        """删除能力声明（不存在 → ``CapabilityNotFoundError``）。"""
        check_capability_id(capability_id)
        try:
            self._store.delete("config", self._capability_path(capability_id))
        except KeyError as exc:
            raise CapabilityNotFoundError(
                f"能力 {capability_id!r} 未登记"
            ) from exc

    def get_capability(self, capability_id: str) -> CapabilityRecord:
        """读取单项能力声明（不存在 → ``CapabilityNotFoundError``）。"""
        check_capability_id(capability_id)
        try:
            raw = self._store.get("config", self._capability_path(capability_id))
        except KeyError as exc:
            raise CapabilityNotFoundError(
                f"能力 {capability_id!r} 未登记"
            ) from exc
        try:
            return _checked_capability(**json.loads(raw.decode("utf-8")))
        except (ValueError, UnicodeDecodeError) as exc:
            raise CapabilityNotFoundError(
                f"能力 {capability_id!r} 记录损坏无法解析"
            ) from exc

    def list_capabilities(self) -> tuple[CapabilityRecord, ...]:
        """列出全部能力声明（按标识排序）。"""
        records = [
            self.get_capability(name[len(CAPABILITY_PREFIX):-len(".json")])
            for name in self._store.list_files("config")
            if name.startswith(CAPABILITY_PREFIX) and name.endswith(".json")
        ]
        return tuple(sorted(records, key=lambda r: r.capability_id))

    def offline_report(self) -> OfflineReport:
        """离线清单查询（02 §7 横幅三类清单；各按标识排序）。"""
        available, limited, unavailable = [], [], []
        for record in self.list_capabilities():
            if record.offline_level == "full":
                available.append(record)
            elif record.offline_level == "degraded":
                limited.append(record)
            else:
                unavailable.append(record)
        key = lambda r: r.capability_id
        return OfflineReport(
            available=tuple(sorted(available, key=key)),
            limited=tuple(sorted(limited, key=key)),
            unavailable=tuple(sorted(unavailable, key=key)),
            generated_at=_now(),
        )

    # ───────────────────────── LLM 传输适配器（GWT-4） ─────────────────────

    def llm_transport(self, provider_hosts: dict[str, str] | None = None):
        """生成 ``LlmClient`` 可用的 ``transport`` callable（兑现 T-L0-003 遗留）。

        签名 ``(endpoint, prompt, key, timeout_ms) -> Iterable[str]``：按端点
        ``provider`` 查 ``provider_hosts`` 得目标主机，经 ``stream`` 发出；
        prompt 的字节数记 ``bytes_out``（内容本身不进网关、不进审计）。
        ``Egress*`` 异常翻译为 ``LlmClient`` 可接的 ``Transport*`` 异常
        （T-L0-003 ``transport`` 签名只认该体系）。
        未登记的 provider → ``TransportUnavailableError``（上层映射为信封）。
        """
        hosts = dict(provider_hosts or {})

        def _transport(endpoint, prompt: str, key: str | None,
                       timeout_ms: int) -> Iterable[str]:
            from st_agent.l0.llm.client import (
                TransportCancelledError,
                TransportError,
                TransportTimeoutError,
                TransportUnavailableError,
            )
            host = hosts.get(endpoint.provider)
            if host is None:
                raise TransportUnavailableError(
                    f"提供方 {endpoint.provider!r} 未登记目标主机"
                )
            _ = key  # Key 只透传给 sender（如 TLS/鉴权头由 sender 组装），网关不记录
            purpose = f"LLM 调用（端点 {endpoint.endpoint_id}，提供方 {endpoint.provider}）"
            try:
                yield from self.stream(
                    "llm_call", host,
                    initiator=f"llm-endpoint:{endpoint.endpoint_id}",
                    purpose=purpose,
                    bytes_out=len(prompt.encode("utf-8")),
                    timeout_ms=timeout_ms,
                )
            except EgressUnavailableError as exc:
                raise TransportUnavailableError(str(exc)) from exc
            except EgressTimeoutError as exc:
                raise TransportTimeoutError(str(exc)) from exc
            except EgressCancelledError as exc:
                raise TransportCancelledError(str(exc)) from exc
            except EgressError as exc:
                raise TransportError(str(exc)) from exc

        return _transport

    # ───────────────────────── 内部工具 ─────────────────────────

    @staticmethod
    def _check_request(
        kind: str,
        target_host: str,
        initiator: str,
        purpose: str,
        bytes_out: int,
        timeout_ms: int,
    ) -> NetworkRequestKind:
        valid = ("data_fetch", "llm_call", "channel_delivery", "remote_mcp",
                 "index_browse")
        if kind not in valid:
            raise NetValidationError(f"非法出网类型 {kind!r}；合法类型 = {list(valid)}")
        if not initiator or not initiator.strip():
            raise NetValidationError("initiator 必填（发起方：系统组件 / skill_id / mcp_server_id）")
        if not purpose or not purpose.strip():
            raise NetValidationError("purpose 必填（人可读目的说明）")
        if not isinstance(bytes_out, int) or bytes_out < 0:
            raise NetValidationError("bytes_out 须为非负整数（只记字节数，不传内容）")
        if not isinstance(timeout_ms, int) or timeout_ms <= 0:
            raise NetValidationError("timeout_ms 须为正整数")
        _checked_event(timestamp=_now(), initiator=initiator.strip(),
                       kind=kind, target_host=target_host,
                       purpose=purpose.strip(), bytes_out=bytes_out,
                       bytes_in=0)
        return kind  # type: ignore[return-value]

    def _log(
        self,
        kind: NetworkRequestKind,
        target_host: str,
        initiator: str,
        purpose: str,
        bytes_out: int,
        bytes_in: int,
        status: str,
        detail: str,
    ) -> str:
        """写一条审计记录（只含计数；返回分区内相对路径 = 信封 log_ref）。"""
        event = _checked_event(
            timestamp=_now(), initiator=initiator, kind=kind,
            target_host=target_host, purpose=purpose,
            bytes_out=bytes_out, bytes_in=bytes_in,
            status=status, detail=detail,
        )
        stamp = event.timestamp
        fname = f"{int(stamp.timestamp() * 1_000_000):020d}-{os.urandom(4).hex()}.json"
        relpath = f"{NET_LOG_PREFIX}{kind}/{fname}"
        self._store.put("execution_log", relpath,
                        event.model_dump_json().encode("utf-8"))
        return relpath

    @staticmethod
    def _parse_event(payload: dict) -> NetworkEvent:
        try:
            return NetworkEvent(**payload)
        except ValidationError as exc:
            raise NetValidationError(f"审计记录损坏无法解析：{exc}") from exc

    @staticmethod
    def _capability_path(capability_id: str) -> str:
        check_capability_id(capability_id)
        return f"{CAPABILITY_PREFIX}{capability_id}.json"
