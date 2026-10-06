"""L5 渠道适配器（[07 §3](../../../docs/技术架构-v2/07-L5-主动触达.md)）。

四类渠道共用一套协议（`deliver` / `health` / `degrade`，见 :class:`ChannelAdapter`）：

| 渠道 | 类型 | 离线行为 | 工作站 |
|---|---|---|---|
| `desktop` | 本地 | 断网可用 | **注入的原生通知端口** |
| `tts` | 本地 | 断网可用（本地合成） | **注入的本地合成端口** |
| `email` | 云端（用户自带 SMTP） | 标注「离线，暂无法发送」，恢复后补发 | 经 [02 §6](../../../docs/技术架构-v2/02-L0-本地优先基座.md) 网关 |
| `im_webhook` | 云端（用户自配） | 同上 | 经网关 |

三条边界（[D-082](../../../项目管理/决策日志.md)）：

- **本地渠道经注入的原生能力端口**——L5 是层，**不得** import 表现层
  （[`test_ui_is_client_only`](../../../tests/test_layering.py) 机器断言）；
  端口未注入即 :meth:`ChannelAdapter.health` 报不可用并**点名缺口**，
  **不假装能投递**。本仓无 TTS 合成面 ⇒ 该渠道在接线前恒不可用。
- **云端渠道经网关**以 `channel_delivery` 发出——载荷（含凭据）只进**按次 sender 闭包**，
  网关全程只见字节数（02 §6 的零明文纪律）；凭据明文只经
  [`CredentialVault.use`](../../l0/secrets/vault.py) 取一次。
- **离线**由 [`EgressGateway.online`](../../l0/net/gateway.py) 一个源头判定（02 §7）；
  云端渠道离线时返回 `unavailable` + `pending_reconnect=True`——**补发执行归
  [`delivery`](delivery.py)**（本模块只如实标注，不改状态）。

本模块只到「把一条已备好的载荷投到某个渠道」为止：**升级链的时间维度、`delivery_id`
留痕与日报路由归 [`delivery`](delivery.py)**。:class:`ChannelDispatcher` 的「降级到下一渠道」
是**同一次投递内的即时失败转移**（渠道坏了换下一个），与时序升级不是一回事。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Annotated, Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l5.errors import ChannelNotWiredError, ChannelValidationError
from st_agent.l5.signal import SignalLevel

__all__ = [
    "CHANNEL_DELIVERY_KIND",
    "CHANNEL_KINDS",
    "CLOUD_CHANNELS",
    "LOCAL_CHANNELS",
    "OFFLINE_NOTE",
    "ChannelAdapter",
    "ChannelDispatch",
    "ChannelDispatcher",
    "ChannelHealth",
    "ChannelKind",
    "ChannelPayload",
    "ChannelResult",
    "ChannelTransport",
    "DesktopChannel",
    "EmailChannel",
    "ImWebhookChannel",
    "TtsChannel",
]

ChannelKind = Literal["desktop", "tts", "email", "im_webhook"]
"""四类渠道（07 §3 的渠道清单）。"""

CHANNEL_KINDS: tuple[ChannelKind, ...] = ("desktop", "tts", "email", "im_webhook")
"""渠道清单的机器可读副本（07 §3 增删渠道时同步此表）。"""

LOCAL_CHANNELS: tuple[ChannelKind, ...] = ("desktop", "tts")
"""本地渠道——断网可用（02 §7 的 `full` 档）。"""

CLOUD_CHANNELS: tuple[ChannelKind, ...] = ("email", "im_webhook")
"""云端渠道——经网关发出、离线时走 `pending_reconnect`（02 §7 的 `none` 档）。"""

CHANNEL_DELIVERY_KIND = "channel_delivery"
"""云端渠道在网关的出网类目（02 §6；网关白名单早已含它）。"""

OFFLINE_NOTE = "离线，暂无法发送（网络恢复后按开关补发）"
"""云端渠道离线时的中性标注（[story-07](../../../docs/PRD-v2-Agent/story-07-ambient-delivery.md) 原话口径）。"""

_TTS_NOT_WIRED = (
    "TTS 渠道未接线：本仓无本地合成面——本地合成端口由组合根注入后才能投递"
)
_DESKTOP_NOT_WIRED = (
    "桌面通知渠道未接线：原生通知端口由组合根从表现层的原生通知入口注入"
    "（层不得 import 表现层，见 tests/test_layering.py）"
)
_TRANSPORT_NOT_WIRED = (
    "云端传输端口未注入：真实 SMTP / Webhook 传输由组合根提供"
)


class ChannelPayload(BaseModel):
    """一条待投递的载荷（由 [`delivery`](delivery.py) 备好，渠道只消费它）。

    文案（`title` / `body`）已由投递面过 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md)
    执行点 2 的校验，渠道**不再改文案**（渠道是通道，不是文案产出方）。
    """

    model_config = ConfigDict(frozen=True)

    signal_id: Annotated[str, Field(min_length=3, max_length=128)]
    """01 §1 `signal_id`（本条载荷源自哪条信号）。"""
    level: SignalLevel
    title: Annotated[str, Field(min_length=1)]
    body: Annotated[str, Field(min_length=1)]
    trace_id: Annotated[str, Field(min_length=3, max_length=128)]
    """关联推理链（01 §11：投递须可追溯）。"""
    evidence_refs: tuple[str, ...] = ()


class ChannelHealth(BaseModel):
    """一次渠道健康检查的结论（显式三态：可用 / 离线 / 未接线）。"""

    model_config = ConfigDict(frozen=True)

    channel: ChannelKind
    available: bool
    offline_level: Literal["full", "degraded", "none"]
    """02 §7 的离线档：本地渠道 `full`、云端渠道 `none`。"""
    reason: str = ""
    """不可用的原因（`available=True` 时为空）。"""


class ChannelResult(BaseModel):
    """一次渠道投递的结论（**三态显式**，不静默降级）。"""

    model_config = ConfigDict(frozen=True)

    channel: ChannelKind
    status: Literal["ok", "unavailable", "failed"]
    detail: str = ""
    pending_reconnect: bool = False
    """离线拦截 ⇒ 待网络恢复后按开关补发（02 §7）。"""

    @property
    def delivered(self) -> bool:
        return self.status == "ok"


@runtime_checkable
class ChannelAdapter(Protocol):
    """渠道适配器的统一协议（07 §3）。

    - :meth:`deliver` 尝试投递，返回显式三态结论；
    - :meth:`health` 报当前可用性（含 02 §7 的离线档与不可用原因）；
    - :meth:`degrade` 产出「本渠道此刻不能投递」的**显式降级结论**——它是载体不是动作：
      真正的「换下一个渠道」由 :class:`ChannelDispatcher` 按链序执行。
    """

    channel: ChannelKind

    def deliver(self, payload: ChannelPayload) -> ChannelResult: ...

    def health(self) -> ChannelHealth: ...

    def degrade(self, reason: str) -> ChannelResult: ...


#: 云端传输端口（按次 sender 的载荷载体）：``(target_host, message) -> bytes_in``，
#: 失败抛 [`EgressError`](../l0/net/errors.py)——网关把它的异常映射成信封分支。
ChannelTransport = Callable[[str, bytes], int]


def _not_wired(channel: ChannelKind, note: str) -> ChannelResult:
    return ChannelResult(channel=channel, status="unavailable", detail=note)


# ───────────────────────── 本地渠道（断网可用） ─────────────────────────


class DesktopChannel:
    """桌面通知渠道（07 §3）——投递经**注入的原生通知端口**。

    :param notify: 原生通知端口 ``(title, body) -> None``（鸭子类型；组合根从
        表现层的原生通知入口取实现）。缺省 ``None`` ⇒ 未接线，`health()` 报不可用。
    """

    channel: ChannelKind = "desktop"

    def __init__(self, notify: Callable[[str, str], None] | None = None) -> None:
        self._notify = notify

    def deliver(self, payload: ChannelPayload) -> ChannelResult:
        if self._notify is None:
            return _not_wired(self.channel, _DESKTOP_NOT_WIRED)
        try:
            self._notify(payload.title, payload.body)
        except Exception as exc:  # 端口抛错即显式失败，不静默当作已送达
            return ChannelResult(
                channel=self.channel, status="failed",
                detail=f"原生通知端口投递失败：{exc}",
            )
        return ChannelResult(channel=self.channel, status="ok")

    def health(self) -> ChannelHealth:
        wired = self._notify is not None
        return ChannelHealth(
            channel=self.channel, available=wired, offline_level="full",
            reason="" if wired else _DESKTOP_NOT_WIRED,
        )

    def degrade(self, reason: str) -> ChannelResult:
        return _not_wired(self.channel, reason)


class TtsChannel:
    """TTS 语音朗读渠道（07 §3）——投递经**注入的本地合成端口**。

    :param synthesize: 本地合成端口 ``(text) -> None``。缺省 ``None`` ⇒ 未接线
        （**本仓无本地合成面**），`health()` 报不可用并点名缺口——不假装能本地合成。
    """

    channel: ChannelKind = "tts"

    def __init__(self, synthesize: Callable[[str], None] | None = None) -> None:
        self._synthesize = synthesize

    def deliver(self, payload: ChannelPayload) -> ChannelResult:
        if self._synthesize is None:
            return _not_wired(self.channel, _TTS_NOT_WIRED)
        try:
            self._synthesize(f"{payload.title}。{payload.body}")
        except Exception as exc:
            return ChannelResult(
                channel=self.channel, status="failed",
                detail=f"本地合成端口投递失败：{exc}",
            )
        return ChannelResult(channel=self.channel, status="ok")

    def health(self) -> ChannelHealth:
        wired = self._synthesize is not None
        return ChannelHealth(
            channel=self.channel, available=wired, offline_level="full",
            reason="" if wired else _TTS_NOT_WIRED,
        )

    def degrade(self, reason: str) -> ChannelResult:
        return _not_wired(self.channel, reason)


# ───────────────────────── 云端渠道（经网关） ─────────────────────────


class _CloudChannel:
    """云端渠道的共同部分：离线判定 → 取凭据 → 经网关以 `channel_delivery` 发出。

    :param gateway: [`EgressGateway`](../../l0/net/gateway.py)（唯一出网出口与唯一审计点）
    :param host: 目标主机（审计的 `target_host`；**不含凭据**）
    :param transport: 云端传输端口（真实 SMTP / Webhook 实现）；缺省 ``None`` ⇒ 未接线
    :param vault: [`CredentialVault`](../../l0/secrets/vault.py)；与 `credential_id` 同给
    :param credential_id: 凭据标识（明文只在本模块的 :meth:`deliver` 一跳动用）
    :param initiator: 审计的发起方（系统组件名）
    :param purpose: 审计的人可读目的说明
    """

    channel: ChannelKind = "email"
    _purpose = "触达渠道投递"

    def __init__(
        self,
        gateway: Any,
        *,
        host: str,
        transport: ChannelTransport | None = None,
        vault: Any = None,
        credential_id: str | None = None,
        initiator: str = "",
        purpose: str = "",
    ) -> None:
        self._gateway = gateway
        self._host = host
        self._transport = transport
        self._vault = vault
        self._credential_id = credential_id
        self._initiator = initiator or f"l5:{self.channel}"
        self._purpose = purpose or self._purpose

    # 子类实现：把载荷 + 凭据渲染成这一跳要发出去的字节
    def _render(self, payload: ChannelPayload, credential: str | None) -> bytes:
        raise NotImplementedError

    def deliver(self, payload: ChannelPayload) -> ChannelResult:
        if not self._gateway.online:
            return ChannelResult(
                channel=self.channel, status="unavailable",
                detail=OFFLINE_NOTE, pending_reconnect=True,
            )
        if self._transport is None:
            return _not_wired(self.channel, _TRANSPORT_NOT_WIRED)
        credential: str | None = None
        if self._vault is not None and self._credential_id is not None:
            credential = self._vault.use(
                self._credential_id, self._initiator, self._purpose,
            )
        message = self._render(payload, credential)
        envelope = self._gateway.execute(
            CHANNEL_DELIVERY_KIND, self._host,
            initiator=self._initiator, purpose=self._purpose,
            bytes_out=len(message),
            sender=_sender_for(self._transport, message),
        )
        return self._from_envelope(envelope)

    def _from_envelope(self, envelope: ResultEnvelope) -> ChannelResult:
        if envelope.status == "ok":
            return ChannelResult(channel=self.channel, status="ok")
        detail = envelope.reason or "投递未成功（网关未给原因）"
        if envelope.status == "unavailable":
            return ChannelResult(
                channel=self.channel, status="unavailable", detail=detail,
                pending_reconnect=True,
            )
        return ChannelResult(channel=self.channel, status="failed", detail=detail)

    def health(self) -> ChannelHealth:
        if not self._gateway.online:
            return ChannelHealth(
                channel=self.channel, available=False, offline_level="none",
                reason=OFFLINE_NOTE,
            )
        wired = self._transport is not None
        return ChannelHealth(
            channel=self.channel, available=wired, offline_level="none",
            reason="" if wired else _TRANSPORT_NOT_WIRED,
        )

    def degrade(self, reason: str) -> ChannelResult:
        return ChannelResult(channel=self.channel, status="unavailable", detail=reason)


def _sender_for(transport: ChannelTransport, message: bytes):
    """把「载荷 + 传输端口」包成网关认的**按次 sender**（02 §6）。

    网关签名只收字节数，故 `message`（含凭据）只存在于本闭包内。
    """

    def _sender(_kind: str, target_host: str, _timeout_ms: int):
        bytes_in = transport(target_host, message)
        return len(message), bytes_in, ()

    return _sender


class EmailChannel(_CloudChannel):
    """邮件渠道（用户自带 SMTP，07 §3）。

    :param recipient: 收件地址（用户配置；**不含凭据**）
    :param sender_address: 发件地址（用户配置）
    """

    channel: ChannelKind = "email"
    _purpose = "触达渠道投递（邮件）"

    def __init__(self, gateway: Any, *, host: str, recipient: str = "",
                 sender_address: str = "", **kw: Any) -> None:
        super().__init__(gateway, host=host, **kw)
        self._recipient = recipient
        self._sender_address = sender_address

    def _render(self, payload: ChannelPayload, credential: str | None) -> bytes:
        """渲染一封最小 RFC 822 形态的邮件（正文即推送文案；凭据在 `Auth` 行）。"""
        lines = [
            f"From: {self._sender_address}",
            f"To: {self._recipient}",
            f"Subject: {payload.title}",
            f"X-ST-Signal-Id: {payload.signal_id}",
            f"X-ST-Trace-Id: {payload.trace_id}",
        ]
        if credential is not None:
            lines.append(f"X-ST-Auth: {credential}")
        lines.append("")
        lines.append(payload.body)
        return "\r\n".join(lines).encode("utf-8")


class ImWebhookChannel(_CloudChannel):
    """IM Webhook 渠道（微信服务号 / 飞书 / 钉钉 / Telegram 等，用户自配，07 §3）。

    :param webhook_id: 用户在渠道配置里给这条 Webhook 起的标识（进审计的 `purpose`）
    """

    channel: ChannelKind = "im_webhook"
    _purpose = "触达渠道投递（IM Webhook）"

    def __init__(self, gateway: Any, *, host: str, webhook_id: str = "",
                 **kw: Any) -> None:
        super().__init__(gateway, host=host, **kw)
        self._webhook_id = webhook_id

    def _render(self, payload: ChannelPayload, credential: str | None) -> bytes:
        """渲染 Webhook 的 JSON 载荷（消息体与溯源字段；凭据在独立字段）。"""
        body: dict[str, Any] = {
            "signal_id": payload.signal_id,
            "level": payload.level,
            "title": payload.title,
            "text": payload.body,
            "trace_id": payload.trace_id,
            "evidence_refs": list(payload.evidence_refs),
        }
        if self._webhook_id:
            body["webhook_id"] = self._webhook_id
        if credential is not None:
            body["token"] = credential
        return json.dumps(body, ensure_ascii=False).encode("utf-8")


# ───────────────────────── 按链序投递（即时降级） ─────────────────────────


class ChannelDispatch(BaseModel):
    """一次「按链投递」的完整结果（逐级尝试 + 最终结论）。"""

    model_config = ConfigDict(frozen=True)

    chain: tuple[str, ...]
    """本次尝试的渠道链（配置里的有序链原样带上）。"""
    attempts: tuple[ChannelResult, ...]
    """逐级尝试的结论（含失败与未接线——**每一步都有据可查**）。"""
    delivered: ChannelResult | None = None
    """成功的那一步（链尽仍失败即 ``None``——显式失败，不静默丢弃）。"""

    @property
    def ok(self) -> bool:
        return self.delivered is not None

    @property
    def pending_reconnect(self) -> bool:
        """本次是否有「离线待补发」的一步（云端渠道离线）。"""
        return any(a.pending_reconnect for a in self.attempts)

    @property
    def degraded_channels(self) -> tuple[str, ...]:
        """本次被跳过的渠道（降级留痕；未接线与失败都计入）。"""
        return tuple(a.channel for a in self.attempts if not a.delivered)

    def detail(self) -> str:
        """逐级原因的中性拼接（供留痕与面板展示）。"""
        return " | ".join(f"{a.channel}:{a.status}:{a.detail}" for a in self.attempts)


class ChannelDispatcher:
    """按渠道链投递 + **即时降级**（07 §3「渠道失效 → 自动降级到下一渠道」）。

    :param adapters: 已接线的适配器（渠道 → 适配器）；链里出现未登记的渠道时该步
        记一条显式「未接线」结论并**继续往下一级**（降级不等于静默丢弃）。

    本类**不含时钟**：它一次调用走完一条链（时序升级归 [`delivery`](delivery.py)）。
    """

    def __init__(self, adapters: Mapping[str, ChannelAdapter] | None = None) -> None:
        self._adapters: dict[str, ChannelAdapter] = dict(adapters or {})

    @property
    def adapters(self) -> Mapping[str, ChannelAdapter]:
        return dict(self._adapters)

    def register(self, adapter: ChannelAdapter) -> None:
        """接线一个渠道适配器（同渠道重复接线即覆盖）。"""
        kind = _as_channel(adapter.channel)
        self._adapters[kind] = adapter

    def adapter(self, kind: str) -> ChannelAdapter:
        """取某渠道的适配器（未接线 → :class:`ChannelNotWiredError` 点名缺口）。"""
        channel = _as_channel(kind)
        adapter = self._adapters.get(channel)
        if adapter is None:
            raise ChannelNotWiredError(
                f"渠道 {channel!r} 未接线（已接线：{sorted(self._adapters)}）"
            )
        return adapter

    def health(self, kind: str) -> ChannelHealth:
        """某渠道的当前可用性（未接线也给出可读结论，不抛）。"""
        channel = _as_channel(kind)
        adapter = self._adapters.get(channel)
        if adapter is None:
            return ChannelHealth(
                channel=channel, available=False,
                offline_level="full" if channel in LOCAL_CHANNELS else "none",
                reason=f"渠道 {channel!r} 未接线（无适配器）",
            )
        return adapter.health()

    def health_map(self) -> tuple[ChannelHealth, ...]:
        """全部四类渠道的健康快照（按 07 §3 的清单顺序）。"""
        return tuple(self.health(kind) for kind in CHANNEL_KINDS)

    def deliver(self, chain: Sequence[str], payload: ChannelPayload) -> ChannelDispatch:
        """按链序投递：成功即停；失败 / 不可用 / 未接线则**降级到下一级**。

        链为空即 :class:`ChannelValidationError`（没有可投递的渠道不是「静默成功」）。
        """
        if not chain:
            raise ChannelValidationError("渠道链为空——没有可投递的渠道")
        attempts: list[ChannelResult] = []
        delivered: ChannelResult | None = None
        for kind in chain:
            channel = _as_channel(kind)
            adapter = self._adapters.get(channel)
            if adapter is None:
                attempts.append(_not_wired(
                    channel, f"渠道 {channel!r} 未接线（无适配器）",
                ))
                continue
            result = adapter.deliver(payload)
            attempts.append(result)
            if result.delivered:
                delivered = result
                break
        return ChannelDispatch(
            chain=tuple(chain), attempts=tuple(attempts), delivered=delivered,
        )


def _as_channel(kind: str) -> ChannelKind:
    if kind not in CHANNEL_KINDS:
        raise ChannelValidationError(
            f"未知渠道 {kind!r}；合法渠道 = {list(CHANNEL_KINDS)}（07 §3）"
        )
    return kind  # type: ignore[return-value]
