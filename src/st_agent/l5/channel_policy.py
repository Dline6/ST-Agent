"""L5 渠道偏好与升级参数（[07 §3–§4](../../../docs/技术架构-v2/07-L5-主动触达.md)）。

每级信号一份 :class:`ChannelPolicy`——**有序渠道链** + **每级未读等待时长**：

- 链的构成即 [07 §4](../../../docs/技术架构-v2/07-L5-主动触达.md) 升级链的**渠道序**（07 §3
  「渠道偏好可配：每种信号级别对应哪些渠道」）；
- `waits_minutes[i]` 是**第 i 级到第 i+1 级之间**的未读等待——故 `len(waits) == len(chain) - 1`；
  最后一级之后没有再等待（链尽即结算，见 [`delivery`](delivery.py)）。

配置经 [01 §7](../../../docs/技术架构-v2/01-平台共享契约.md) 注册（见
:mod:`channel_registry`）：四个条目——三个级别的偏好取值是**对象**（链 + 等待）⇒
按 01 §7 **不在统一标量落值面内**，写面归本类的 `set_policy`；补发开关
`resend-on-reconnect` 是**布尔标量**，正合「逐条目一次落值」，经门面 `apply` 委托
:meth:`ChannelPolicies.set_resend_on_reconnect`（先例＝L5 预算的 `current-mode`）。

缺省取向「少而准」（[story-07](../../../docs/PRD-v2-Agent/story-07-ambient-delivery.md)）：
`emergency` 走完整链（桌面 → 邮件 → IM），`important` 收窄一级，`routine` 只占最轻的一级
且**不带等待**（不升级）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from st_agent.contracts.errors import ContractViolation
from st_agent.contracts.registry_types import (
    ChangePolicy,
    ChangeRecord,
    ConfigEntry,
    PanelField,
)
from st_agent.l5.channels import CHANNEL_KINDS, ChannelKind
from st_agent.l5.channel_store import ChannelPolicyStore, CONFIG_PREFIX
from st_agent.l5.errors import ChannelValidationError
from st_agent.l5.signal import SignalLevel

__all__ = [
    "DEFAULT_POLICIES",
    "LEVELS",
    "POLICY_DISPLAY_NAMES",
    "RESEND_CONFIG_ID",
    "ChannelPolicies",
    "ChannelPolicy",
    "policy_config_id",
]

LEVELS: tuple[SignalLevel, ...] = ("emergency", "important", "routine")
"""三级信号（07 §1）——渠道偏好逐级一份。"""

RESEND_CONFIG_ID = "channel-delivery.resend-on-reconnect"
"""补发开关的 `config_id`（01 §7 的**可写标量**条目）。"""

#: 缺省偏好：`level -> (有序渠道链, 每级未读等待分钟)`。取自 Story 的例（见模块 docstring）。
DEFAULT_POLICIES: Mapping[SignalLevel, tuple[tuple[ChannelKind, ...], tuple[int, ...]]] = {
    "emergency": (("desktop", "email", "im_webhook"), (5, 15)),
    "important": (("desktop", "email"), (10,)),
    "routine": (("desktop",), ()),
}

_DEFAULT_RESEND = True
"""缺省开启补发（story-07「网络恢复后自动补发（用户可关闭补发）」）。"""

POLICY_DISPLAY_NAMES: Mapping[str, str] = {
    **{f"channel-delivery.{level}": f"渠道偏好（{level}）" for level in LEVELS},
    RESEND_CONFIG_ID: "网络恢复后补发云端渠道",
}


def policy_config_id(level: str) -> str:
    """某级别的偏好条目 `config_id`（点分语义：`channel-delivery.<级别>`）。"""
    return f"{CONFIG_PREFIX}{level}"


class ChannelPolicy(BaseModel):
    """一级信号的渠道偏好（有序链 + 每级未读等待）。"""

    model_config = ConfigDict(frozen=True)

    level: SignalLevel
    channels: tuple[ChannelKind, ...]
    """**有序**渠道链（非空、无重复）——升级链的渠道序（07 §4）。"""
    waits_minutes: tuple[int, ...]
    """第 i 级到第 i+1 级之间的未读等待（分钟）；长度恒为 ``len(channels) - 1``。"""
    source: Literal["configured", "default"] = "default"
    """本份取值来自用户配置还是内置缺省（数据面可分；渲染归表现层）。"""

    @model_validator(mode="after")
    def _shape(self) -> "ChannelPolicy":
        # 值对象层抛 ContractViolation（由 pydantic 包装为 ValidationError）——
        # 面层（set_policy / 落盘解析）另行以 ChannelValidationError 携字段名分流
        # （分工同 signal.py 的「两处形态校验」口径）。
        _validate_chain(self.channels, self.waits_minutes, error=ContractViolation)
        return self

    def next_channel(self, step: int) -> ChannelKind | None:
        """第 ``step`` 级（0 起）的渠道；越界即 ``None``（链尽）。"""
        return self.channels[step] if 0 <= step < len(self.channels) else None

    def wait_after(self, step: int) -> int | None:
        """第 ``step`` 级之后的未读等待分钟；无下一级即 ``None``。"""
        return self.waits_minutes[step] if 0 <= step < len(self.waits_minutes) else None

    def as_payload(self) -> dict[str, Any]:
        """落盘 / 门面读面用的 JSON 形态（与 :func:`_policy_from_payload` 互逆）。"""
        return {"channels": list(self.channels), "waits_minutes": list(self.waits_minutes)}


def _validate_chain(
    channels: Sequence[str],
    waits: Sequence[Any],
    *,
    error: type[Exception] = ChannelValidationError,
) -> None:
    """链与等待的形态校验（逐类点名）。

    `error` 是失败词汇的注入点：**值对象层**（:meth:`ChannelPolicy._shape`）用
    :class:`~st_agent.contracts.errors.ContractViolation`（由 pydantic 包装为
    ``ValidationError``），**面层**（`set_policy` / 落盘解析）用
    :class:`ChannelValidationError`——同一判据、两种词汇（同 `signal.py` 的分工）。
    """
    if not channels:
        raise error("渠道链不得为空——至少一个渠道（07 §3）")
    unknown = [c for c in channels if c not in CHANNEL_KINDS]
    if unknown:
        raise error(f"未知渠道 {unknown}；合法渠道 = {list(CHANNEL_KINDS)}（07 §3）")
    if len(set(channels)) != len(channels):
        raise error(f"同一渠道不得在链内出现两次：{list(channels)}")
    expected = len(channels) - 1
    if len(waits) != expected:
        raise error(
            f"未读等待须与链长匹配（{len(channels)} 个渠道 ⇒ {expected} 段等待），"
            f"收到 {len(waits)} 段"
        )
    for index, wait in enumerate(waits):
        if not isinstance(wait, int) or isinstance(wait, bool) or wait <= 0:
            raise error(f"第 {index + 1} 段未读等待须为正整数分钟，收到 {wait!r}")


def _policy_from_payload(value: Any, level: SignalLevel) -> ChannelPolicy:
    """把落盘 / 面板给的映射读成 :class:`ChannelPolicy`（非法即点名拒）。"""
    if not isinstance(value, Mapping):
        raise ChannelValidationError(
            f"{level} 的渠道偏好须为映射（channels / waits_minutes），"
            f"收到 {type(value).__name__}"
        )
    for name in ("channels", "waits_minutes"):
        if name not in value:
            raise ChannelValidationError(f"{level} 的渠道偏好缺字段：{name}")
    channels = value["channels"]
    waits = value["waits_minutes"]
    if not isinstance(channels, Sequence) or isinstance(channels, (str, bytes)):
        raise ChannelValidationError(f"{level}.channels 须为列表，收到 {type(channels).__name__}")
    if not isinstance(waits, Sequence) or isinstance(waits, (str, bytes)):
        raise ChannelValidationError(f"{level}.waits_minutes 须为列表，收到 {type(waits).__name__}")
    _validate_chain(list(channels), list(waits))
    try:
        return ChannelPolicy(
            level=level, channels=tuple(channels), waits_minutes=tuple(waits),
        )
    except ValidationError as exc:
        raise ChannelValidationError(f"{level} 的渠道偏好不合约：{exc}") from exc


class ChannelPolicies:
    """渠道偏好门面（07 §3 / §4）。

    :param store: ``Store`` 句柄；缺省 ``None`` ＝**纯内存态**（读面照常，但不落盘、不留痕）
    :param policies: 内置缺省（缺省 :data:`DEFAULT_POLICIES`；供测试与产品口径调整）
    :param resend_default: 补发开关的内置缺省（缺省开启）
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    def __init__(
        self,
        *,
        store: Any = None,
        policies: Mapping[str, Any] | None = None,
        resend_default: bool | None = None,
        now: Any = None,
    ) -> None:
        self._store = ChannelPolicyStore(store, now=now) if store is not None else None
        self._builtin = _builtin_policies(policies)
        self._resend_default = _DEFAULT_RESEND if resend_default is None else bool(resend_default)
        self._resend = self._resend_default

    # ───────────────────────── 读面 ─────────────────────────

    def get(self, level: str) -> ChannelPolicy:
        """取某级别的偏好（落盘优先；取不到即内置缺省）。

        条目**损坏**时回落内置缺省（投递不停摆），损坏仍有 :meth:`entry` 可显式暴露。
        """
        _require_level(level)
        if self._store is not None:
            raw = self._store.current(policy_config_id(level), None)
            if raw is not None:
                try:
                    return _policy_from_payload(raw, level).model_copy(
                        update={"source": "configured"}
                    )
                except ChannelValidationError:
                    pass
        fallback = self._builtin[level]
        return ChannelPolicy(
            level=level, channels=fallback[0], waits_minutes=fallback[1], source="default",
        )

    def map(self) -> tuple[ChannelPolicy, ...]:
        """三级偏好齐备的快照（按 :data:`LEVELS` 顺序；每级带来源标注）。"""
        return tuple(self.get(level) for level in LEVELS)

    @property
    def resend_on_reconnect(self) -> bool:
        """网络恢复后是否补发云端渠道（用户可关）。"""
        if self._store is None:
            return self._resend
        value = self._store.current(RESEND_CONFIG_ID, self._resend)
        return bool(value) if isinstance(value, bool) else self._resend

    def changes(self) -> tuple[ChangeRecord, ...]:
        """全部变更留痕（无 ``Store`` → 空集）。"""
        return () if self._store is None else self._store.changes()

    def entries(self) -> tuple[ConfigEntry, ...]:
        """本层登记进 01 §7 的四个条目（含当前取值）。"""
        return (
            *(self._entry(policy_config_id(level), self.get(level).as_payload())
              for level in LEVELS),
            self._entry(RESEND_CONFIG_ID, self.resend_on_reconnect),
        )

    def entry(self, config_id: str) -> ConfigEntry | None:
        """按 `config_id` 取条目登记形态；不属本层 → ``None``；**损坏 → 校验错**。"""
        if not config_id.startswith(CONFIG_PREFIX):
            return None
        if self._store is not None:
            stored = self._store.entry(config_id)
            if stored is not None:
                return stored
        for entry in self.entries():
            if entry.config_id == config_id:
                return entry
        return None

    # ───────────────────────── 写面（01 §7：对象取值的写面归本类） ─────────────────────────

    def set_policy(
        self,
        level: str,
        channels: Sequence[str],
        waits_minutes: Sequence[int] | None = None,
    ) -> ChannelPolicy:
        """设置某级别的渠道偏好（越界即拒、不落盘不留痕）。

        :param channels: 有序渠道链（非空、无重复、取值须在 :data:`CHANNEL_KINDS` 内）
        :param waits_minutes: 每级未读等待；缺省 `None` ⇒ 沿用当前取值的等待段
            （**仅当链长不变时才合法**，否则须显式给出）
        :raises ChannelValidationError: 任一项越界（逐类点名）
        """
        _require_level(level)
        current = self.get(level)
        waits = current.waits_minutes if waits_minutes is None else tuple(waits_minutes)
        _validate_chain(list(channels), waits)   # 面层先拒：失败词汇是 ChannelValidationError
        target = ChannelPolicy(
            level=level, channels=tuple(channels), waits_minutes=tuple(waits),
        )
        return self._write_policy(level, target, previous=current)

    def set_resend_on_reconnect(self, enabled: Any, *, trace_ref: str | None = None) -> Any:
        """开关「网络恢复后补发云端渠道」（`apply` 与自有面共用这一处）。"""
        if not isinstance(enabled, bool):
            raise ChannelValidationError(f"补发开关须为布尔，收到 {enabled!r}")
        if self._store is None:
            self._resend = enabled
            return None
        entry = self._entry(RESEND_CONFIG_ID, enabled)
        return self._store.set(
            entry, previous=self.resend_on_reconnect, trace_ref=trace_ref,
        )

    # ───────────────────────── 内部 ─────────────────────────

    def _write_policy(
        self, level: str, target: ChannelPolicy, *, previous: ChannelPolicy,
    ) -> ChannelPolicy:
        if self._store is None:
            self._builtin[level] = (target.channels, target.waits_minutes)
            return target
        entry = self._entry(policy_config_id(level), target.as_payload())
        self._store.set(entry, previous=previous.as_payload())
        return self.get(level)

    def _entry(self, config_id: str, default: Any) -> ConfigEntry:
        return ConfigEntry(
            config_id=config_id,
            display_name=POLICY_DISPLAY_NAMES[config_id],
            value_schema=_value_schema(config_id),
            default=default,
            description_for_chat=_chat_description(config_id),
            panel_form_spec=_panel_form(config_id),
            scope="global",
            change_policy=ChangePolicy(requires_confirmation=True),
        )


def _builtin_policies(
    overrides: Mapping[str, Any] | None,
) -> dict[SignalLevel, tuple[tuple[ChannelKind, ...], tuple[int, ...]]]:
    """内置缺省（`overrides` 按 `level -> (channels, waits)` 覆写；形态同样校验）。"""
    builtin = {level: (tuple(chain), tuple(waits)) for level, (chain, waits) in DEFAULT_POLICIES.items()}
    for level, value in (overrides or {}).items():
        _require_level(level)
        chain, waits = value
        _validate_chain(list(chain), list(waits))
        builtin[level] = (tuple(chain), tuple(waits))
    return builtin


def _require_level(level: str) -> None:
    if level not in LEVELS:
        raise ChannelValidationError(
            f"未知信号级别 {level!r}；合法级别 = {list(LEVELS)}（07 §1）"
        )


def _value_schema(config_id: str) -> dict[str, object]:
    if config_id == RESEND_CONFIG_ID:
        return {"type": "boolean"}
    return {
        "type": "object",
        "properties": {
            "channels": {"type": "array", "items": {"enum": list(CHANNEL_KINDS)}},
            "waits_minutes": {"type": "array", "items": {"type": "integer", "minimum": 1}},
        },
    }


def _chat_description(config_id: str) -> str:
    if config_id == RESEND_CONFIG_ID:
        return "断网期间没发出去的云端推送，网络恢复后要不要自动补发（可随时关掉）"
    level = config_id.split(".", 1)[1]
    return (
        f"{level} 级别的信号按什么顺序走哪些渠道，以及每级之间等多久没读就升级"
        "（渠道顺序即升级顺序）"
    )


def _panel_form(config_id: str) -> PanelField:
    if config_id == RESEND_CONFIG_ID:
        return PanelField(
            widget="toggle", label=POLICY_DISPLAY_NAMES[config_id],
            help_text="关闭后，断网期间未发出的云端推送不会在恢复联网时自动补发",
        )
    return PanelField(
        widget="matrix", label=POLICY_DISPLAY_NAMES[config_id],
        help_text="按顺序排列的渠道链与每级未读等待；写入走渠道偏好自有 API",
    )
