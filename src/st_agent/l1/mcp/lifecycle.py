"""MCP Server 生命周期（T-L1-002.3；03 §5.3 状态机与降级 + §5.2 禁用联动）。

五件事：**五态状态机 + 转移留痕** / **崩溃提示与可配次数的自动重连** /
**重连耗尽后的降级候选并列陈列** / **禁用中止进行中调用** /
**重连策略经 01 §7 注册且改动留 ``change_id``**。

五态与合法转移（03 §5.3）：

- ``connected`` —— 会话可用
- ``disconnected`` —— 无会话（基态）
- ``reconnecting`` —— 断开后自动重试中
- ``permission_pending`` —— 声明权限未全部批准（由 ``.1`` 权限账本触发）
- ``disabled`` —— 用户禁用

**降级只并列、不建议**（宪法铁律 4）：``degrade_candidates`` 返回平台内置
Skill 的候选列表（``source != "mcp-mapped"``，每条附自身可用性），
**不自动切换、不合并、不给统一建议**；是否换用由上层决定。

布局（只经 ``Store`` 读写，不直连文件系统）：

- 当前状态 → ``config`` 分区 ``mcp-lifecycle/<server_id>.json``
- 转移留痕 → ``execution_log`` 分区 ``mcp-lifecycle/transition/<ts>-<rand>.json``
- 崩溃提示 → ``execution_log`` 分区 ``mcp-lifecycle/notice/<ts>-<rand>.json``
- 重连策略 → ``config`` 分区 ``mcp-hub/reconnect-*.json``（01 §7 ``ConfigEntry``）
- 策略变更留痕 → ``execution_log`` 分区 ``mcp-hub-change/<change_id>.json``

崩溃提示取**本层的结构化提示类型**（``McpHubNotice``），不扩 01 §11 事件清单
（任务假设 A1：§11 是跨层上行事件清单、``PlatformEvent.event`` 为闭合字面量，
M0 无 L3 消费方；扩事件须走工作流第 ⑦ 步）。
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from st_agent.contracts.capability_types import OfflineLevel, SkillDescriptor, SkillSource
from st_agent.contracts.registry_types import (
    ChangePolicy,
    ChangeRecord,
    ConfigEntry,
    PanelField,
)
from st_agent.l1.mcp.errors import (
    McpError,
    McpStateError,
    McpStateTransitionError,
    McpValidationError,
)
from st_agent.l1.mcp.ids import check_server_id
from st_agent.l1.mcp.registry import McpServerRegistry
from st_agent.l1.skills.registry import SkillRegistry

__all__ = [
    "ALLOWED_TRANSITIONS",
    "DEFAULT_INTERVAL_MS",
    "DEFAULT_MAX_RETRIES",
    "HUB_CHANGE_PREFIX",
    "HUB_CONFIG_PREFIX",
    "HUB_STATE_PREFIX",
    "INTERVAL_CONFIG_ID",
    "MAX_RETRIES_CONFIG_ID",
    "NOTICE_PREFIX",
    "TRANSITION_PREFIX",
    "ActiveCall",
    "DegradeCandidate",
    "McpHubConfig",
    "McpHubNotice",
    "McpHubStateMachine",
    "McpLifecycleRecord",
    "McpServerState",
    "ReconnectSettings",
    "StateTransition",
    "check_state",
]

# ───────────────────────── 落盘位置 ─────────────────────────

HUB_STATE_PREFIX = "mcp-lifecycle/"
"""``config`` 分区内**当前状态**记录的目录前缀。"""

TRANSITION_PREFIX = "mcp-lifecycle/transition/"
"""``execution_log`` 分区内转移留痕（append-only）的目录前缀。"""

NOTICE_PREFIX = "mcp-lifecycle/notice/"
"""``execution_log`` 分区内崩溃提示（append-only）的目录前缀。"""

HUB_CONFIG_PREFIX = "mcp-hub/"
"""``config`` 分区内重连策略条目的目录前缀（01 §7 ``ConfigEntry``）。"""

HUB_CHANGE_PREFIX = "mcp-hub-change/"
"""``execution_log`` 分区内策略变更留痕的目录前缀（``change_id`` 回滚单位）。"""

MAX_RETRIES_CONFIG_ID = "mcp-hub/reconnect-max-retries"
"""重连次数条目的 ``config_id``（01 §7；与文件前缀同源便于双向定位）。"""

INTERVAL_CONFIG_ID = "mcp-hub/reconnect-interval-ms"
"""重连间隔条目的 ``config_id``（同上）。"""

DEFAULT_MAX_RETRIES = 3
"""重连次数缺省值（可经 01 §7 条目改；改动留 ``change_id``）。"""

DEFAULT_INTERVAL_MS = 1_000
"""重连间隔缺省值（毫秒）。"""

McpServerState = Literal[
    "connected", "disconnected", "reconnecting", "permission_pending", "disabled"
]
"""03 §5.3 的五态 Server 生命周期状态。"""

STATES: tuple[McpServerState, ...] = (
    "connected", "disconnected", "reconnecting", "permission_pending", "disabled",
)

ALLOWED_TRANSITIONS: dict[McpServerState, frozenset[McpServerState]] = {
    "connected": frozenset({"disconnected", "reconnecting", "disabled", "permission_pending"}),
    "disconnected": frozenset({"connected", "reconnecting", "permission_pending", "disabled"}),
    "reconnecting": frozenset({"connected", "disconnected", "disabled", "permission_pending"}),
    "permission_pending": frozenset({"connected", "reconnecting", "disconnected", "disabled"}),
    "disabled": frozenset({"disconnected", "permission_pending"}),
}
"""合法转移表（03 §5.3 + 假设 A3/A4）。

**唯一的状态来源约束在 ``disabled`` 上**：``disabled → connected`` /
``disabled → reconnecting`` 有意不在表内——必须先启用（回 ``disconnected`` /
``permission_pending``）再经探测进入 ``connected``，避免「已禁用却又自己连上」。
其余态之间放开，因为断连事件可能在任何非禁用态被观测到（留痕滞后），重连是合法应对。

同态转移不在此表内——它是**幂等写**（只刷新当前态、不记转移），见 ``transition``。
"""


def check_state(value: str) -> McpServerState:
    """校验状态取值（非法 → ``McpValidationError``）。"""
    if value not in STATES:
        raise McpValidationError(f"非法 MCP Server 状态 {value!r}；合法取值 = {list(STATES)}")
    return value  # type: ignore[return-value]


def _now() -> datetime:
    """用户本地时区当前时刻（01 §8）。"""
    return datetime.now().astimezone()


def _new_change_id() -> str:
    """生成一个 ``change_id``（01 §7：每次配置变更产生，作回滚单位）。"""
    return f"chg_{secrets.token_hex(10)}"


def _stamp(occurred: datetime) -> str:
    """``<微秒时间戳>-<随机>`` 的文件名（append-only 记录唯一化）。"""
    return f"{int(occurred.timestamp() * 1_000_000):020d}-{os.urandom(4).hex()}"


# ───────────────────────── 数据形态 ─────────────────────────


class McpLifecycleRecord(BaseModel):
    """一台 Server 的**当前**生命周期状态（覆盖写；历史见 ``StateTransition``）。"""

    model_config = ConfigDict(frozen=True)

    server_id: str
    state: McpServerState
    updated_at: datetime
    trace_id: str | None = None
    reason: Annotated[str, Field(min_length=1)]

    @model_validator(mode="after")
    def _shape(self) -> "McpLifecycleRecord":
        check_server_id(self.server_id)
        if self.updated_at.tzinfo is None:
            raise McpValidationError("updated_at 必须带时区语义（01 §8）")
        return self


class StateTransition(BaseModel):
    """一次状态转移的留痕（GWT-1；落 ``execution_log``，append-only）。"""

    model_config = ConfigDict(frozen=True)

    server_id: str
    from_state: McpServerState
    to_state: McpServerState
    occurred_at: datetime
    trace_id: str | None = None
    reason: Annotated[str, Field(min_length=1)]

    @model_validator(mode="after")
    def _shape(self) -> "StateTransition":
        check_server_id(self.server_id)
        if self.occurred_at.tzinfo is None:
            raise McpValidationError("occurred_at 必须带时区语义（01 §8）")
        return self


NoticeKind = Literal[
    "server-disconnected", "reconnect-recovered", "reconnect-exhausted", "call-cancelled",
]

HUB_NOTICE_KINDS: tuple[NoticeKind, ...] = (
    "server-disconnected", "reconnect-recovered", "reconnect-exhausted", "call-cancelled",
)


class McpHubNotice(BaseModel):
    """崩溃 / 重连 / 中止的显式提示（GWT-2/GWT-4；中性措辞，落 ``execution_log``）。

    载体取本层结构化提示而非 01 §11 事件（假设 A1）：提供方是 L1 MCP Hub、
    订阅方是 MCP Hub 面板，M0 无跨层消费方。
    """

    model_config = ConfigDict(frozen=True)

    kind: NoticeKind
    server_id: str
    message: Annotated[str, Field(min_length=1)]
    """中性提示文案（禁拟人化：无人名 / 性格 / 第一人称 / 情感 / 对话体）。"""
    occurred_at: datetime

    @model_validator(mode="after")
    def _shape(self) -> "McpHubNotice":
        check_server_id(self.server_id)
        if self.occurred_at.tzinfo is None:
            raise McpValidationError("occurred_at 必须带时区语义（01 §8）")
        return self


class DegradeCandidate(BaseModel):
    """一条降级候选项（GWT-3）——**并列陈列**，不带排序位与推荐语。"""

    model_config = ConfigDict(frozen=True)

    skill_id: str
    name: str
    description: str
    source: SkillSource
    offline_level: OfflineLevel
    available: bool
    detail: Annotated[str, Field(min_length=1)]
    """可用性说明（中性事实陈述，不含「建议使用」类措辞）。"""


class ReconnectSettings(BaseModel):
    """重连策略取值（01 §7 两条 ``ConfigEntry`` 的合成视图）。"""

    model_config = ConfigDict(frozen=True)

    max_retries: int = Field(ge=0, le=100)
    interval_ms: int = Field(ge=0, le=600_000)


class ActiveCall:
    """一次进行中调用的句柄（GWT-4）。

    用法——把 ``cancel`` 透传给出网/传输层，禁用时该事件被置位：

    .. code-block:: python

        with hub.begin_call(server_id) as call:
            gateway.execute("remote_mcp", host, cancel=call.cancel, sender=..., ...)

    登记发生在 ``__enter__``、注销在 ``__exit__``（未进入 ``with`` 即不占登记）。
    """

    def __init__(self, machine: "McpHubStateMachine", server_id: str, trace_id: str | None) -> None:
        self._machine = machine
        self.server_id = server_id
        self.trace_id = trace_id
        self.cancel = threading.Event()

    @property
    def cancelled(self) -> bool:
        """取消信号是否已置位。"""
        return self.cancel.is_set()

    def __enter__(self) -> "ActiveCall":
        self._machine._register_call(self)
        return self

    def __exit__(self, *_exc: object) -> bool:
        self._machine._unregister_call(self)
        return False


# ───────────────────────── 重连策略（01 §7） ─────────────────────────


class McpHubConfig:
    """重连策略的 01 §7 注册表（新增 / 读取 / 变更 + ``change_id`` 留痕）。

    「次数」与「间隔」各为**独立条目**（假设 A6）：01 §7 一条目只有一个
    ``panel_form_spec``（单字段 ``PanelField``），双字段对象无法在面板通道渲染。

    :param store: ``Store`` 句柄（条目写 ``config``，变更留痕写 ``execution_log``）
    :param max_retries: 次数缺省值（未注册时用）
    :param interval_ms: 间隔缺省值（未注册时用）
    """

    def __init__(
        self,
        store,
        *,
        max_retries: int = DEFAULT_MAX_RETRIES,
        interval_ms: int = DEFAULT_INTERVAL_MS,
        now: Callable[[], datetime] = _now,
    ) -> None:
        self._store = store
        self._max_retries = max_retries
        self._interval_ms = interval_ms
        self._now = now

    # ───────────────────────── 注册 / 读取 ─────────────────────────

    def register_defaults(self) -> tuple[ConfigEntry, ...]:
        """把两条策略条目按缺省值注册进 ``config``（幂等，已存在不动）。"""
        entries = (
            self._retries_entry(self._max_retries),
            self._interval_entry(self._interval_ms),
        )
        for entry in entries:
            path = self._path(entry.config_id)
            if path not in self._store.list_files("config"):
                self._store.put("config", path, entry.model_dump_json().encode("utf-8"))
        return entries

    def get(self) -> ReconnectSettings:
        """读当前策略（条目缺失或损坏 → 回落缺省值）。"""
        return ReconnectSettings(
            max_retries=self._value(MAX_RETRIES_CONFIG_ID, self._max_retries),
            interval_ms=self._value(INTERVAL_CONFIG_ID, self._interval_ms),
        )

    def list_entries(self) -> tuple[ConfigEntry, ...]:
        """已注册的策略条目（按 ``config_id`` 升序；未注册则为空）。"""
        out = [
            self._parse_entry(self._store.get("config", name))
            for name in self._store.list_files("config")
            if name.startswith(HUB_CONFIG_PREFIX) and name.endswith(".json")
        ]
        return tuple(sorted(out, key=lambda e: e.config_id))

    def changes(self) -> tuple[ChangeRecord, ...]:
        """策略变更留痕（按 ``applied_at`` 升序；``change_id`` 即回滚单位）。"""
        out = [
            self._parse_change(self._store.get("execution_log", name))
            for name in self._store.list_files("execution_log")
            if name.startswith(HUB_CHANGE_PREFIX) and name.endswith(".json")
        ]
        return tuple(sorted(out, key=lambda c: c.applied_at))

    # ───────────────────────── 变更 ─────────────────────────

    def set(
        self,
        *,
        max_retries: int | None = None,
        interval_ms: int | None = None,
        trace_id: str | None = None,
    ) -> tuple[ChangeRecord, ...]:
        """改策略（只改传入项；值未变则不留痕），返回本次产生的变更记录。

        每次真实改动产生一条 ``ChangeRecord``（含 ``change_id``）并落
        ``execution_log/mcp-hub-change/<change_id>.json``（GWT-2）。
        """
        changes: list[ChangeRecord] = []
        plans = (
            (MAX_RETRIES_CONFIG_ID, max_retries, self._max_retries, self._retries_entry),
            (INTERVAL_CONFIG_ID, interval_ms, self._interval_ms, self._interval_entry),
        )
        for config_id, new_value, fallback, build in plans:
            if new_value is None:
                continue
            if not isinstance(new_value, int) or isinstance(new_value, bool) or new_value < 0:
                raise McpValidationError(f"{config_id} 的取值须为非负整数，收到 {new_value!r}")
            old_value = self._value(config_id, fallback)
            if old_value == new_value:
                continue
            entry = build(new_value)
            self._store.put("config", self._path(config_id), entry.model_dump_json().encode("utf-8"))
            change = ChangeRecord(
                change_id=_new_change_id(),
                config_id=config_id,
                old_value=old_value,
                new_value=new_value,
                applied_at=self._now().isoformat(),
                trace_ref=trace_id,
            )
            self._store.put(
                "execution_log", f"{HUB_CHANGE_PREFIX}{change.change_id}.json",
                change.model_dump_json().encode("utf-8"),
            )
            changes.append(change)
        return tuple(changes)

    # ───────────────────────── 条目构造与内部工具 ─────────────────────────

    def _value(self, config_id: str, fallback: int) -> int:
        try:
            raw = self._store.get("config", self._path(config_id))
        except KeyError:
            return fallback
        try:
            default = self._parse_entry(raw).default
        except McpError:
            return fallback
        return default if isinstance(default, int) and not isinstance(default, bool) else fallback

    @staticmethod
    def _retries_entry(value: int) -> ConfigEntry:
        return ConfigEntry(
            config_id=MAX_RETRIES_CONFIG_ID,
            display_name="MCP Server 断开后的自动重连次数",
            value_schema={"type": "number"},
            default=value,
            description_for_chat=(
                "MCP Server 连接断开后自动重连的最大尝试次数；用尽后状态转 disconnected，"
                "并给出平台内置 Skill 候选（并列陈列，不自动切换）。0 表示断开后不自动重连"
            ),
            panel_form_spec=PanelField(
                widget="number", label="重连次数",
                help_text="连接断开后自动重试的最大次数；0 表示不自动重连",
            ),
            scope="global",
            change_policy=ChangePolicy(requires_confirmation=False),
        )

    @staticmethod
    def _interval_entry(value: int) -> ConfigEntry:
        return ConfigEntry(
            config_id=INTERVAL_CONFIG_ID,
            display_name="MCP Server 自动重连的尝试间隔",
            value_schema={"type": "number"},
            default=value,
            description_for_chat="两次自动重连尝试之间的等待毫秒数",
            panel_form_spec=PanelField(
                widget="number", label="重连间隔（毫秒）",
                help_text="每两次重连尝试之间的等待时间；0 表示立即重试",
            ),
            scope="global",
            change_policy=ChangePolicy(requires_confirmation=False),
        )

    @staticmethod
    def _path(config_id: str) -> str:
        return f"{HUB_CONFIG_PREFIX}{config_id[len(HUB_CONFIG_PREFIX):]}.json"

    @staticmethod
    def _parse_entry(raw: bytes) -> ConfigEntry:
        try:
            return ConfigEntry(**json.loads(raw.decode("utf-8")))
        except (ValueError, UnicodeDecodeError, ValidationError) as exc:
            raise McpValidationError(f"MCP Hub 配置条目损坏无法解析：{exc}") from exc

    @staticmethod
    def _parse_change(raw: bytes) -> ChangeRecord:
        try:
            return ChangeRecord(**json.loads(raw.decode("utf-8")))
        except (ValueError, UnicodeDecodeError, ValidationError) as exc:
            raise McpValidationError(f"MCP Hub 变更记录损坏无法解析：{exc}") from exc


# ───────────────────────── 状态机门面 ─────────────────────────


class McpHubStateMachine:
    """MCP Server 生命周期门面（03 §5.3）。

    :param store: ``Store`` 句柄（当前态写 ``config``，留痕写 ``execution_log``）
    :param servers: ``McpServerRegistry``——注册记录 / 启停开关 / 权限批准态
    :param skills: ``SkillRegistry``——降级候选来源（可选；缺省则候选为空）
    :param sandbox: ``SkillSandbox``——候选可用性叠加已被禁用的 Skill（可选）
    :param config: ``McpHubConfig`` 重连策略（缺省自建）
    :param probe: 探测一次连接是否可用（缺省 ``servers.test_connection().ok``）
    :param sleep: 重连间隔等待函数（测试注入 no-op 即得离线用例）
    """

    def __init__(
        self,
        store,
        *,
        servers: McpServerRegistry,
        skills: SkillRegistry | None = None,
        sandbox=None,
        config: McpHubConfig | None = None,
        probe: Callable[[str], bool] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = _now,
    ) -> None:
        self._store = store
        self._servers = servers
        self._skills = skills
        self._sandbox = sandbox
        self._config = config if config is not None else McpHubConfig(store, now=now)
        self._probe = probe
        self._sleep = sleep
        self._now = now
        self._calls: dict[str, list[ActiveCall]] = {}
        self._lock = threading.RLock()

    @property
    def config(self) -> McpHubConfig:
        """重连策略注册表（01 §7）。"""
        return self._config

    # ───────────────────────── 读取 ─────────────────────────

    def state_of(self, server_id: str) -> McpServerState:
        """当前状态（**纯读**，无副作用）。

        注册表事实优先于留痕：未启用 → ``disabled``；有待批准权限 →
        ``permission_pending``；否则取当前态记录，无记录则 ``disconnected``。

        ``permission_pending`` 由 ``.1`` 权限账本**推导**而非「粘住」——权限全部
        批准后不再停留该态，回落到 ``disconnected``（随后由探测决定能否
        ``connected``）。由切换开关推导的 ``disabled`` 同理：启用后即离开。
        """
        sid = check_server_id(server_id)
        record = self._servers.get_server(sid)
        if not record.enabled:
            return "disabled"
        if self._servers.permissions.pending_permissions(sid):
            return "permission_pending"
        persisted = self._load_state(sid)
        if persisted is None or persisted.state == "permission_pending":
            return "disconnected"
        return persisted.state

    def transitions(self, server_id: str | None = None) -> tuple[StateTransition, ...]:
        """转移留痕（按发生时间升序；给 ``server_id`` 即只取该 Server）。"""
        sid = check_server_id(server_id) if server_id is not None else None
        out = [
            self._parse_transition(self._store.get("execution_log", name))
            for name in self._store.list_files("execution_log")
            if name.startswith(TRANSITION_PREFIX) and name.endswith(".json")
        ]
        return tuple(
            sorted((t for t in out if sid is None or t.server_id == sid),
                   key=lambda t: (t.occurred_at, t.server_id))
        )

    def notices(self, server_id: str | None = None) -> tuple[McpHubNotice, ...]:
        """显式提示留痕（按发生时间升序）。"""
        sid = check_server_id(server_id) if server_id is not None else None
        out = [
            self._parse_notice(self._store.get("execution_log", name))
            for name in self._store.list_files("execution_log")
            if name.startswith(NOTICE_PREFIX) and name.endswith(".json")
        ]
        return tuple(
            sorted((n for n in out if sid is None or n.server_id == sid),
                   key=lambda n: (n.occurred_at, n.server_id))
        )

    # ───────────────────────── 状态转移 ─────────────────────────

    def transition(
        self,
        server_id: str,
        to_state: McpServerState,
        *,
        trace_id: str | None = None,
        reason: str = "",
    ) -> McpLifecycleRecord:
        """执行一次状态转移（GWT-1）。

        同态 = **幂等写**（刷新当前态，不记转移）；不在 ``ALLOWED_TRANSITIONS``
        内 → ``McpStateTransitionError``（失败显式化，不静默改态）；合法转移写入
        当前态并追加一条 ``StateTransition`` 留痕。
        """
        sid = check_server_id(server_id)
        target = check_state(to_state)
        self._servers.get_server(sid)
        current = self._current_state(sid)
        if target == current:
            record = self._make_record(sid, current, trace_id,
                                       reason or f"状态维持 {current}")
            self._save_state(record)
            return record
        if target not in ALLOWED_TRANSITIONS[current]:
            allowed = sorted(ALLOWED_TRANSITIONS[current]) or ["（无合法后继）"]
            raise McpStateTransitionError(
                f"非法状态转移：MCP Server {sid} {current} → {target}；"
                f"合法后继 = {allowed}"
            )
        record = self._make_record(sid, target, trace_id,
                                   reason or f"状态由 {current} 转为 {target}")
        self._save_state(record)
        self._append_transition(StateTransition(
            server_id=sid, from_state=current, to_state=target,
            occurred_at=self._now(), trace_id=trace_id, reason=record.reason,
        ))
        return record

    def sync_from_registry(self, server_id: str, *, trace_id: str | None = None) -> McpLifecycleRecord:
        """按 ``.1`` 注册表 / 权限账本核对并落定状态（GWT-1 的 ``permission_pending`` 入口）。

        ``state_of`` 是纯读；本方法是**显式动作**，允许写盘与留痕。二者取值一致时
        只确保当前态记录存在，不产生转移留痕。
        """
        sid = check_server_id(server_id)
        desired = self.state_of(sid)
        current = self._current_state(sid)
        if desired == current:
            record = self._load_state(sid) or self._make_record(
                sid, current, trace_id, f"状态维持 {current}")
            self._save_state(record)
            return record
        return self.transition(sid, desired, trace_id=trace_id,
                               reason="按注册记录与权限批准态核对")

    # ───────────────────────── 启用 / 禁用（GWT-4） ─────────────────────────

    def disable(self, server_id: str, *, trace_id: str | None = None) -> McpLifecycleRecord:
        """禁用 Server：状态转 ``disabled`` 并**中止**全部进行中调用（GWT-4）。

        顺序有意为「先置开关 → 再置取消信号 → 最后落状态」：取消信号先于状态生效，
        故不会出现「已禁用但调用仍被当成成功」的窗口。
        """
        sid = check_server_id(server_id)
        self._servers.disable_server(sid)
        signalled = self.cancel_inflight(sid)
        record = self.transition(sid, "disabled", trace_id=trace_id, reason="用户禁用该 Server")
        if signalled:
            self._append_notice(McpHubNotice(
                kind="call-cancelled", server_id=sid,
                message=f"MCP Server {sid} 已禁用，{signalled} 个进行中调用已中止",
                occurred_at=self._now(),
            ))
        return record

    def enable(self, server_id: str, *, trace_id: str | None = None) -> McpLifecycleRecord:
        """启用 Server：回 ``disconnected``，或待批准权限尚存则回 ``permission_pending``。"""
        sid = check_server_id(server_id)
        self._servers.enable_server(sid)
        target: McpServerState = (
            "permission_pending"
            if self._servers.permissions.pending_permissions(sid)
            else "disconnected"
        )
        return self.transition(sid, target, trace_id=trace_id, reason="用户启用该 Server")

    def mark_permission_pending(self, server_id: str, *, trace_id: str | None = None) -> McpLifecycleRecord:
        """把状态落定为 ``permission_pending``（权限声明后、批准前调用）。"""
        return self.transition(check_server_id(server_id), "permission_pending",
                               trace_id=trace_id, reason="声明权限尚待逐项批准")

    # ───────────────────────── 崩溃与重连（GWT-2 / GWT-3） ─────────────────────────

    def on_disconnect(
        self,
        server_id: str,
        *,
        reason: str = "连接已断开",
        trace_id: str | None = None,
    ) -> tuple[McpHubNotice, ...]:
        """连接断开：提示 + 进 ``reconnecting`` + 按配置自动重试（GWT-2）。

        重试次数与间隔取自 01 §7 条目（``McpHubConfig``，可改、改动留 ``change_id``）。
        首次尝试**立即**进行，``interval_ms`` 是**两次尝试之间**的等待。
        成功 → ``connected`` + ``reconnect-recovered``；用尽 → ``disconnected`` +
        ``reconnect-exhausted``（GWT-3 的入口，候选由 ``degrade_candidates`` 并列给出）。
        返回本次产生的全部提示（按发生顺序）。
        """
        sid = check_server_id(server_id)
        if self.state_of(sid) == "disabled":
            raise McpStateError(
                f"MCP Server {sid} 已禁用，不进入重连；请先启用（enable）"
            )
        settings = self._config.get()
        notices = [self._append_notice(McpHubNotice(
            kind="server-disconnected", server_id=sid,
            message=f"MCP Server {sid} 连接已断开：{reason}；进入自动重连",
            occurred_at=self._now(),
        ))]
        self.transition(sid, "reconnecting", trace_id=trace_id, reason=reason)
        for attempt in range(1, settings.max_retries + 1):
            if attempt > 1 and settings.interval_ms:
                self._sleep(settings.interval_ms / 1000)
            if self._probe_ok(sid):
                self.transition(sid, "connected", trace_id=trace_id,
                                reason=f"第 {attempt} 次重连尝试成功")
                notices.append(self._append_notice(McpHubNotice(
                    kind="reconnect-recovered", server_id=sid,
                    message=f"MCP Server {sid} 已恢复连接（第 {attempt} 次尝试成功）",
                    occurred_at=self._now(),
                )))
                return tuple(notices)
        self.transition(sid, "disconnected", trace_id=trace_id,
                        reason=f"按配置重试 {settings.max_retries} 次仍未恢复")
        notices.append(self._append_notice(McpHubNotice(
            kind="reconnect-exhausted", server_id=sid,
            message=(
                f"MCP Server {sid} 已按配置重试 {settings.max_retries} 次仍未恢复；"
                "状态转 disconnected，平台内置 Skill 候选见 degrade_candidates"
                "（并列陈列，不自动切换）"
            ),
            occurred_at=self._now(),
        )))
        return tuple(notices)

    def reconnect(self, server_id: str, *, trace_id: str | None = None) -> bool:
        """做**一次**重连尝试（``reconnecting`` 态下的原语）；成功即转 ``connected``。"""
        sid = check_server_id(server_id)
        if self._current_state(sid) != "reconnecting":
            raise McpStateError(
                f"MCP Server {sid} 不在 reconnecting 态，不得单次重连"
            )
        if not self._probe_ok(sid):
            return False
        self.transition(sid, "connected", trace_id=trace_id, reason="重连尝试成功")
        return True

    def degrade_candidates(self, server_id: str) -> tuple[DegradeCandidate, ...]:
        """该 Server 失效时可用的**平台内置 Skill 候选**（GWT-3；假设 A2）。

        候选 = ``SkillRegistry.list_all()`` 中 ``source != "mcp-mapped"`` 者
        （失效 Server 自己派生的 Skill 正是被遮蔽者，故不入候选），每条附
        ``offline_level`` 与自身可用性，按 ``skill_id`` 确定性排序。

        **并列陈列：不排序权重、不合并、不给统一建议、不自动切换**（宪法铁律 4）。
        纯读，不改变任何执行路径与状态。
        """
        sid = check_server_id(server_id)
        self._servers.get_server(sid)
        if self._skills is None:
            return ()
        out = [
            self._candidate(d) for d in self._skills.list_all()
            if d.source != "mcp-mapped"
        ]
        return tuple(sorted(out, key=lambda c: c.skill_id))

    # ───────────────────────── 进行中调用（GWT-4） ─────────────────────────

    def begin_call(self, server_id: str, *, trace_id: str | None = None) -> ActiveCall:
        """开一次可被取消的调用句柄（GWT-4）。禁用态下直接拒。"""
        sid = check_server_id(server_id)
        self._servers.get_server(sid)
        if self.state_of(sid) == "disabled":
            raise McpStateError(f"MCP Server {sid} 已禁用，不得发起调用")
        return ActiveCall(self, sid, trace_id)

    def cancel_inflight(self, server_id: str) -> int:
        """给该 Server 全部进行中调用置取消信号，返回被信号化的调用数。

        只置信号、不改状态（状态变更归 ``disable``）；传输层与网关据此在检查点
        中断并按 ``cancelled`` 落审计。
        """
        sid = check_server_id(server_id)
        with self._lock:
            calls = tuple(self._calls.get(sid, ()))
        for call in calls:
            call.cancel.set()
        return len(calls)

    def inflight(self, server_id: str) -> tuple[ActiveCall, ...]:
        """当前登记的进行中调用（只读快照）。"""
        sid = check_server_id(server_id)
        with self._lock:
            return tuple(self._calls.get(sid, ()))

    # ───────────────────────── 内部工具 ─────────────────────────

    def _register_call(self, call: ActiveCall) -> None:
        with self._lock:
            self._calls.setdefault(call.server_id, []).append(call)

    def _unregister_call(self, call: ActiveCall) -> None:
        with self._lock:
            bucket = self._calls.get(call.server_id)
            if not bucket:
                return
            self._calls[call.server_id] = [c for c in bucket if c is not call]

    def _probe_ok(self, server_id: str) -> bool:
        if self._probe is not None:
            return bool(self._probe(server_id))
        return bool(self._servers.test_connection(server_id).ok)

    def _candidate(self, desc: SkillDescriptor) -> DegradeCandidate:
        if self._sandbox is not None and self._sandbox.is_disabled(desc.skill_id):
            available, detail = False, "该 Skill 已被禁用，需先启用"
        elif desc.offline_level == "full":
            available, detail = True, "离线完整可用"
        elif desc.offline_level == "degraded":
            available, detail = True, "离线降级可用（依赖项可能不完整）"
        else:
            available, detail = False, "离线不可用（需联网执行）"
        return DegradeCandidate(
            skill_id=desc.skill_id, name=desc.name, description=desc.description,
            source=desc.source, offline_level=desc.offline_level,
            available=available, detail=detail,
        )

    def _current_state(self, server_id: str) -> McpServerState:
        record = self._load_state(server_id)
        return record.state if record is not None else "disconnected"

    def _make_record(
        self, server_id: str, state: McpServerState,
        trace_id: str | None, reason: str,
    ) -> McpLifecycleRecord:
        return McpLifecycleRecord(
            server_id=server_id, state=state, updated_at=self._now(),
            trace_id=trace_id, reason=reason or f"状态为 {state}",
        )

    def _append_transition(self, record: StateTransition) -> None:
        self._store.put(
            "execution_log", f"{TRANSITION_PREFIX}{_stamp(record.occurred_at)}.json",
            record.model_dump_json().encode("utf-8"),
        )

    def _append_notice(self, notice: McpHubNotice) -> McpHubNotice:
        self._store.put(
            "execution_log", f"{NOTICE_PREFIX}{_stamp(notice.occurred_at)}.json",
            notice.model_dump_json().encode("utf-8"),
        )
        return notice

    def _save_state(self, record: McpLifecycleRecord) -> None:
        self._store.put(
            "config", self._state_path(record.server_id),
            record.model_dump_json().encode("utf-8"),
        )

    def _load_state(self, server_id: str) -> McpLifecycleRecord | None:
        path = self._state_path(server_id)
        if path not in self._store.list_files("config"):
            return None
        return self._parse_state(self._store.get("config", path))

    @staticmethod
    def _state_path(server_id: str) -> str:
        return f"{HUB_STATE_PREFIX}{check_server_id(server_id)}.json"

    @staticmethod
    def _parse_state(raw: bytes) -> McpLifecycleRecord:
        try:
            return McpLifecycleRecord(**json.loads(raw.decode("utf-8")))
        except (ValueError, UnicodeDecodeError, ValidationError) as exc:
            raise McpValidationError(f"MCP Server 状态记录损坏无法解析：{exc}") from exc

    @staticmethod
    def _parse_transition(raw: bytes) -> StateTransition:
        try:
            return StateTransition(**json.loads(raw.decode("utf-8")))
        except (ValueError, UnicodeDecodeError, ValidationError) as exc:
            raise McpValidationError(f"MCP 状态转移留痕损坏无法解析：{exc}") from exc

    @staticmethod
    def _parse_notice(raw: bytes) -> McpHubNotice:
        try:
            return McpHubNotice(**json.loads(raw.decode("utf-8")))
        except (ValueError, UnicodeDecodeError, ValidationError) as exc:
            raise McpValidationError(f"MCP 提示留痕损坏无法解析：{exc}") from exc
