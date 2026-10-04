"""出网审计网关（02 §6；全平台唯一出网出口）。

布局（只经 ``Store`` 读写，不直连文件系统）：
- 审计记录 → ``execution_log`` 分区 ``net/<kind>/<起µs>-<止µs>-<rand>.jsonl``
  （**分段日志**：一段装多条，整文件落盘；断网可查历史 = 读本地分区，GWT-2）
- 能力声明 → ``config`` 分区 ``net-capability/<capability_id>.json``
  （02 §7 离线分级，GWT-3）
- 审计开关 → ``config`` 分区 ``net-audit/enabled.json``（01 §7 条目，见
  :mod:`st_agent.l0.net.audit_config`）

执行语义：
- ``execute``：调用方注入 ``sender``（真实发包实现），网关负责发起前
  校验 → 计时 → 落审计（**开启时**）→ 返回 ``ResultEnvelope``（成功 ``ok``
  data 为 ``bytes_in`` 计数；失败按 GWT-5 映射分支）
- ``stream``：流式形态（供 LLM 适配器）：``chunk* → done`` 或抛 ``Egress*``
  异常；审计事件无论成败先行落盘
- **审计为可选项、默认关**（02 §6，D-073）：关着时请求照常发起、计时、映射
  结果、离线拦截，只是不留痕；开关的真相源是配置注册表条目，构造参数只是
  覆盖入口，``set_audit`` 为进程内热切换口
- 离线拦截：``set_online(False)`` 后任何 ``execute`` / ``stream`` 直接记
  ``unavailable``（pending_reconnect=True），不触碰 ``sender``（GWT-4）
- 审计纪律：``NetworkEvent`` 只含计数与目的说明；``purpose``/``detail``
  构造期拒绝内容/凭据字段名；调用方不得把 prompt/response/key 传给网关——
  网关签名只有字节数（GWT-4/5）。**分段只改落盘组织**：记录内容与读接口不变

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
from st_agent.l0.net.audit_config import get_audit_policy
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
    "AUDIT_SEGMENT_SIZE",
    "CAPABILITY_PREFIX",
    "NET_AUDIT_DISABLED_REF",
    "NET_LOG_PREFIX",
    "EgressGateway",
    "Sender",
    "SenderResult",
]

NET_LOG_PREFIX = "net/"
"""``execution_log`` 分区内出网审计记录的目录前缀。"""

CAPABILITY_PREFIX = "net-capability/"
"""``config`` 分区内离线能力声明的目录前缀。"""

SEGMENT_SUFFIX = ".jsonl"
"""分段落盘的后缀（一段多行、每行一条 ``NetworkEvent``）。"""

LEGACY_SUFFIX = ".json"
"""逐条落盘的旧后缀（本机制落地前的记录；查询侧照样可读，B4）。"""

AUDIT_SEGMENT_SIZE = 1000
"""一段装多少条审计记录（GWT-4 ①：`put` 次数 ＝ ⌈N/段⌉ 量级）。

段粒度同时是**未落盘窗口**：进程被强杀时缓冲中的记录会丢，故失败 / 取消的
记录一律**即时成段**（它们的 ``log_ref`` 必须真实可指），关审计与查询前也
先冲刷（B4：关的是「以后记不记」，不是「已经记的删不删」）。
"""

NET_AUDIT_DISABLED_REF = "net-audit:disabled"
"""审计关闭时失败信封的 ``log_ref`` 哨兵。

``ResultEnvelope.failed`` 要求非空 ``log_ref``（01 §5），而关审计时**没有**记录
可指向——故用一枚显式哨兵说明「本次请求无审计记录」，既不空着、也不指向不存在的
文件（02 §6「关闭时的口径：显式化，不静默」）。
"""

#: 发包实现协议：(kind, target_host, timeout_ms) -> (bytes_out, bytes_in, chunks?)
#: chunks 供流式形态消费；一次性形态忽略。抛 ``Egress*`` 异常上报失败。
SenderResult = tuple[int, int, Iterable[str]]
Sender = Callable[[NetworkRequestKind, str, int], SenderResult]

_NEEDS_REF = ("failed", "cancelled")
"""需要 ``log_ref`` 指向真实记录的终态（其余分支不消费该返回值）。"""


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


def _micros(stamp: datetime) -> int:
    """时戳 → 微秒整数（段名用它编码时间有序性，供 ``query`` 跳段）。"""
    return int(round(stamp.timestamp() * 1_000_000))


def segment_bounds(name: str) -> tuple[int, int] | None:
    """从段名解出 ``(起µs, 止µs)``；解不出（旧记录 / 异常名）→ ``None``。"""
    base = name.rsplit("/", 1)[-1]
    parts = base.split("-")
    if len(parts) < 2:
        return None
    try:
        start = int(parts[0])
        end = int(parts[1]) if len(parts) >= 3 else start
    except ValueError:
        return None
    return (start, end) if end >= start else (start, start)



def _put_segment(store, events: list[NetworkEvent]) -> str:
    """把一段记录写成**一个**文件，返回其分区内相对路径（GWT-4 ①）。

    `put` 次数 ＝ ⌈N/段⌉ 量级：现状「一请求一文件 + 每次重写整分区清单」是
    O(N) 次 `put`、每次重写清单（清单随 N 增长 ⇒ 天然 O(n²)），批量化后清单
    恒为 ⌈N/段⌉ 条。
    """
    ordered = sorted(events, key=lambda e: e.timestamp)
    kind = ordered[0].kind
    name = (
        f"{NET_LOG_PREFIX}{kind}/"
        f"{_micros(ordered[0].timestamp):020d}-{_micros(ordered[-1].timestamp):020d}"
        f"-{os.urandom(4).hex()}{SEGMENT_SUFFIX}"
    )
    payload = "".join(e.model_dump_json() + "\n" for e in ordered)
    store.put("execution_log", name, payload.encode("utf-8"))
    return name


def _segment_overlaps(name: str, since_us: int | None, until_us: int | None) -> bool:
    """段的时间区间与查询边界是否相交（不相交 ⇒ 整段跳过，GWT-4 ②）。

    解不出时间（旧记录 / 异常名）一律**不跳**——宁可多读一段，不可漏记录。
    边界留 1 微秒余量：时戳 → 微秒走浮点乘，余量抵掉舍入误差。
    """
    if since_us is None and until_us is None:
        return True
    bounds = segment_bounds(name)
    if bounds is None:
        return True
    start, end = bounds
    if since_us is not None and end < since_us - 1:
        return False
    if until_us is not None and start > until_us + 1:
        return False
    return True


class EgressGateway:
    """出网审计网关（02 §6 门面；全平台唯一出网出口）。

    :param store: ``Store`` 句柄（审计记 ``execution_log``，声明与开关记 ``config``）
    :param sender: 发包实现（缺省为 None → 调用即 ``unavailable``，测试与
        离线路径经此判定；在线真实发包由调用方注入）
    :param online: 初始在线状态（默认在线；断网时 ``set_online(False)``）
    :param audit: 审计开关**覆盖入口**（真相源是 01 §7 条目 `net-audit/enabled`，
        缺省＝关）；``None`` ⇒ 取配置注册表口径。变更请用 :meth:`set_audit`
    :param segment_size: 一段装多少条审计记录（缺省 :data:`AUDIT_SEGMENT_SIZE`）
    """

    def __init__(
        self,
        store,
        sender: Sender | None = None,
        *,
        online: bool = True,
        audit: bool | None = None,
        segment_size: int = AUDIT_SEGMENT_SIZE,
    ) -> None:
        self._store = store
        self._sender = sender
        self._online = online
        self._audit = (
            get_audit_policy(store).enabled if audit is None else bool(audit)
        )
        self._segment_size = max(1, int(segment_size))
        # 分段缓冲：段 → 待落盘的记录（GWT-4 ①；读写由 ``_log_lock`` 保护）
        self._pending: dict[str, list[NetworkEvent]] = {}
        self._log_lock = threading.Lock()

    # ───────────────────────── 审计开关（02 §6 / 01 §7，GWT-1/2） ─────────────────

    @property
    def audit_enabled(self) -> bool:
        """当前是否逐次留痕（缺省关；02 §6）。"""
        return self._audit

    def set_audit(self, enabled: bool | None) -> bool:
        """热切换审计（不需重启进程，GWT-2）。

        ``None`` ⇒ 重读配置注册表条目。**关之前先冲刷**缓冲——关的是「以后
        记不记」，不能把已经记下的记录随开关一起丢掉（B4）。
        """
        if enabled is None:
            enabled = get_audit_policy(self._store).enabled
        if not enabled:
            self.flush_audit()
        self._audit = bool(enabled)
        return self._audit

    def flush_audit(self) -> None:
        """把缓冲中的审计记录落成段（优雅收尾 / 读前冲刷；02 §6 段粒度即丢失窗口）。"""
        with self._log_lock:
            for kind in list(self._pending):
                self._flush_kind(kind)

    def _flush_kind(self, kind: str) -> None:
        events = self._pending.pop(kind, None)
        if events:
            _put_segment(self._store, events)

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
        sender: Sender | None = None,
    ) -> Iterator[str]:
        """经网关执行流式出网，产出文本块；失败抛 ``Egress*`` 异常。

        审计事件无论成败先行落盘（成功 ``ok`` / 离线 ``unavailable`` /
        超时 ``failed`` / 取消 ``cancelled``）。发起前校验失败抛
        ``NetValidationError``（无出网事实，不记审计）。

        :param sender: 按次发包实现（缺省用构造时 ``sender``）——与 ``execute``
            同款。一次请求的载荷只能在这一跳组装时（如 LLM 的 prompt 与凭据）
            经此注入：载荷由 sender 闭包承载，网关仍只见字节数（02 §6）。
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
        active_sender = sender if sender is not None else self._sender
        if active_sender is None:
            self._log(req_kind, target_host, initiator, purpose,
                      bytes_out, 0, "unavailable", f"{target_host} 无可用发包实现")
            raise EgressUnavailableError(f"{target_host} 无可用发包实现")
        started = time.monotonic()
        elapsed_ms = lambda: int((time.monotonic() - started) * 1000)
        bytes_in = 0
        try:
            _, _, chunks = active_sender(req_kind, target_host, timeout_ms)
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

        不传时间边界即查全部（断网可查历史 = 读本地分区）。**读接口形态与
        时戳升序口径不变**（GWT-4 ③：消费方零改动）；实现上借段的时间有序性
        **跳段**——带下界的查询不读全量历史（GWT-4 ②）。缓冲中的记录先冲刷，
        故「读己所写」成立。旧形态（一请求一文件）的记录照样可读（B4）。
        """
        for bound in (since, until):
            if bound is not None and bound.tzinfo is None:
                raise NetValidationError("查询边界时间必须带时区语义")
        self.flush_audit()
        since_us = _micros(since) if since is not None else None
        until_us = _micros(until) if until is not None else None
        entries: list[NetworkEvent] = []
        for name in self._store.list_files("execution_log"):
            if not name.startswith(NET_LOG_PREFIX):
                continue
            if not _segment_overlaps(name, since_us, until_us):
                continue
            entries.extend(self._read_records(name))
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

    def pending_reconnect(self) -> ResultEnvelope:
        """待补发查询（离线拦截的记录；补发执行由 L5 按需消费，假设 B）。

        **审计关闭时走显式 ``unavailable``**（GWT-3）：此时一条记录都没有，
        若返回空集会与「查过了、确实没有待补发」混为一谈——故不返回空，
        而是说清「审计未开启，无补发清单可查」（02 §6 关闭时的口径）。
        """
        if not self._audit:
            now = _now()
            return ResultEnvelope.unavailable(
                "审计未开启（02 §6）：无补发清单可查——先在设置中开启出网审计",
                last_updated_at=now, as_of=now,
            )
        return ResultEnvelope.ok({"events": self.query(status="unavailable")})

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

    def llm_transport(
        self,
        provider_hosts: dict[str, str] | None = None,
        *,
        sender_factory: Callable[[Any, str, str | None, int], Sender] | None = None,
    ):
        """生成 ``LlmClient`` 可用的 ``transport`` callable（兑现 T-L0-003 遗留）。

        签名 ``(endpoint, prompt, key, timeout_ms) -> Iterable[str]``：按端点
        ``provider`` 查 ``provider_hosts`` 得目标主机，经 ``stream`` 发出；
        prompt 的字节数记 ``bytes_out``（内容本身不进网关、不进审计）。
        ``Egress*`` 异常翻译为 ``LlmClient`` 可接的 ``Transport*`` 异常
        （T-L0-003 ``transport`` 签名只认该体系）。
        未登记的 provider → ``TransportUnavailableError``（上层映射为信封）。

        :param sender_factory: 真实发送器的**按次构造器**（02 §6 按次 sender）——
            ``(endpoint, prompt, key, timeout_ms) -> Sender``。缺省 ``None`` 时
            沿用网关构造时的 sender（其签名不含 prompt / key，故只适合替身与
            离线路径）；真实端点须经此注入，否则prompt 发不出去（只有字节数
            被记录）。工厂返回的 Sender 抛 ``Egress*`` 时按同款映射透出。
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
            purpose = f"LLM 调用（端点 {endpoint.endpoint_id}，提供方 {endpoint.provider}）"
            try:
                # 载荷（prompt / key）只进按次 sender 闭包，网关只见字节数
                per_call: Sender | None = (
                    sender_factory(endpoint, prompt, key, timeout_ms)
                    if sender_factory is not None else None
                )
                yield from self.stream(
                    "llm_call", host,
                    initiator=f"llm-endpoint:{endpoint.endpoint_id}",
                    purpose=purpose,
                    bytes_out=len(prompt.encode("utf-8")),
                    timeout_ms=timeout_ms,
                    sender=per_call,
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
        """记一条审计记录（只含计数）；**审计关闭时不落盘**，返回显式哨兵。

        需要 ``log_ref`` 的终态（失败 / 取消）**即时成段**，使信封指的路径真实
        存在；其余（成功 / 离线拦截）攒进缓冲，攒满一段或收尾时一次落盘。
        """
        if not self._audit:
            return NET_AUDIT_DISABLED_REF
        event = _checked_event(
            timestamp=_now(), initiator=initiator, kind=kind,
            target_host=target_host, purpose=purpose,
            bytes_out=bytes_out, bytes_in=bytes_in,
            status=status, detail=detail,
        )
        if status in _NEEDS_REF:
            return _put_segment(self._store, [event])
        with self._log_lock:
            bucket = self._pending.setdefault(kind, [])
            bucket.append(event)
            if len(bucket) >= self._segment_size:
                self._flush_kind(kind)
        return ""       # 缓冲中的记录尚无路径；调用方（ok / unavailable 分支）不消费

    @staticmethod
    def _parse_event(payload: dict) -> NetworkEvent:
        try:
            return NetworkEvent(**payload)
        except ValidationError as exc:
            raise NetValidationError(f"审计记录损坏无法解析：{exc}") from exc

    def _read_records(self, name: str) -> list[NetworkEvent]:
        """读一个审计文件里的全部记录（段 = 多行；旧形态 = 单条）。"""
        raw = self._store.get("execution_log", name).decode("utf-8")
        try:
            if name.endswith(SEGMENT_SUFFIX):
                return [self._parse_event(json.loads(line))
                        for line in raw.splitlines() if line.strip()]
            return [self._parse_event(json.loads(raw))]
        except (ValueError, UnicodeDecodeError) as exc:
            raise NetValidationError(f"审计记录 {name} 损坏无法解析：{exc}") from exc

    @staticmethod
    def _capability_path(capability_id: str) -> str:
        check_capability_id(capability_id)
        return f"{CAPABILITY_PREFIX}{capability_id}.json"
