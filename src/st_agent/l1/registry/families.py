"""存量族的适配器（把已自落的 01 §7 条目接进统一门面）。

**不改名、不迁移**（决策 [D-067](../../../项目管理/决策日志.md) ③）：各族的
``config_id`` 与落盘布局原样保留，本模块只把它们的**读面**接进
:class:`~st_agent.l1.registry.facade.ConfigRegistryFacade`，落值**一律委托各族
owner 的既有 API**——门面是**统一入口**，不是旁路写盘，故 owner 的值域校验与
留痕语义经门面照样成立。

**写面按取值形状划界**（[01 §7](../../../docs/技术架构-v2/01-平台共享契约.md)
「逐条目一次落值」）：本模块的族携带的都是**标量**条目；L2 的
``memory-policy/*`` 取值为对象 / 列表，其写面归 L2 owner（适配器见
``st_agent.l2.memory.registry_adapter``，**只读**）。

两个补充留痕的族（``retention`` / ``llm-provider-host``）在 owner 侧本来**不产生**
``ChangeRecord``，故由本模块补落一条（前缀 ``retention-change/`` /
``provider-host-change/``），使 [01 §7](../../../docs/技术架构-v2/01-平台共享契约.md)
的「每次变更产生 ``change_id``（可回滚）」对这两族也成立。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from st_agent.contracts.registry_types import ChangeRecord, ConfigEntry, ConfigScope
from st_agent.l0.backup import (
    get_policy,
    retention_config_entries,
    set_policy,
)
from st_agent.l1.mcp.lifecycle import (
    INTERVAL_CONFIG_ID,
    MAX_RETRIES_CONFIG_ID,
    McpHubConfig,
)
from st_agent.l1.registry.errors import RegistryValidationError
from st_agent.l1.registry.naming import CHANGE_PARTITION, new_change_id
from st_agent.l1.sandbox.provider_hosts import CONFIG_ID_PREFIX, ProviderHostRegistry
from st_agent.l1.scheduler.policy import OFFLINE_CATCH_UP_CONFIG_ID, SchedulerPolicy

__all__ = [
    "McpHubPolicyFamily",
    "ProviderHostFamily",
    "RetentionFamily",
    "SchedulerPolicyFamily",
]

_RETENTION_FIELDS: dict[str, str] = {
    "retention.chat_history_days": "chat_history_days",
    "retention.execution_log_days": "execution_log_days",
}
"""留存两旋钮的 ``config_id`` → ``RetentionPolicy`` 字段名。"""

_MCP_KWARGS: dict[str, str] = {
    MAX_RETRIES_CONFIG_ID: "max_retries",
    INTERVAL_CONFIG_ID: "interval_ms",
}
"""MCP 重连策略两旋钮的 ``config_id`` → ``McpHubConfig.set`` 的关键字名。"""


def _system_now() -> datetime:
    return datetime.now().astimezone()


class RetentionFamily:
    """L0 留存两旋钮（``retention.*``）——把**孤儿**条目纳入注册（D-067 ③）。

    ``entry`` 取的条目 = [`retention_config_entries()`](../../l0/backup/models.py) 的
    规范形态叠加 [`get_policy`](../../l0/backup/backup.py) 的当前取值；落值走
    [`set_policy`](../../l0/backup/backup.py)（其不产生 ``ChangeRecord``，由本族补落）。

    :param store: ``Store`` 句柄
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    config_prefix = "retention."
    scope: ConfigScope = "global"
    change_prefix = "retention-change/"

    def __init__(self, store, *, now: Callable[[], datetime] | None = None) -> None:
        self._store = store
        self._now = _system_now if now is None else now

    def entries(self) -> tuple[ConfigEntry, ...]:
        out = (self.entry(cid) for cid in sorted(_RETENTION_FIELDS))
        return tuple(e for e in out if e is not None)

    def entry(self, config_id: str) -> ConfigEntry | None:
        field = _RETENTION_FIELDS.get(config_id)
        if field is None:
            return None
        canonical = next(
            e for e in retention_config_entries() if e.config_id == config_id
        )
        return canonical.model_copy(update={"default": getattr(get_policy(self._store), field)})

    def apply(
        self, config_id: str, value: object, *, trace_id: str | None = None
    ) -> ChangeRecord | None:
        field = _RETENTION_FIELDS.get(config_id)
        if field is None:
            raise RegistryValidationError(f"{config_id!r} 不属本族（retention.*）")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise RegistryValidationError(f"{config_id} 的取值须为 ≥0 的整数：{value!r}")
        old = getattr(get_policy(self._store), field)
        if old == value:
            return None
        set_policy(self._store, **{field: value})
        return self._record(config_id, old, value, trace_id)

    def _record(self, config_id, old, new, trace_id) -> ChangeRecord:
        change = ChangeRecord(
            change_id=new_change_id(), config_id=config_id,
            old_value=old, new_value=new, applied_at=self._now().isoformat(),
            trace_ref=trace_id,
        )
        self._store.put(
            CHANGE_PARTITION, f"{self.change_prefix}{change.change_id}.json",
            change.model_dump_json().encode("utf-8"),
        )
        return change


class SchedulerPolicyFamily:
    """L1 离线补跑策略（``scheduler-policy/offline-catch-up``）。

    落值委托 :meth:`SchedulerPolicy.set`——其值域门（``catch-up`` / ``skip``）
    与「值未变不留痕」经门面**照样生效**，错误类型原样上抛。
    """

    config_prefix = "scheduler-policy/"
    scope: ConfigScope = "global"

    def __init__(self, policy: SchedulerPolicy) -> None:
        self._policy = policy

    def entries(self) -> tuple[ConfigEntry, ...]:
        entry = self.entry(OFFLINE_CATCH_UP_CONFIG_ID)
        return () if entry is None else (entry,)

    def entry(self, config_id: str) -> ConfigEntry | None:
        if config_id != OFFLINE_CATCH_UP_CONFIG_ID:
            return None
        return self._policy.entry().model_copy(update={"default": self._policy.value()})

    def apply(
        self, config_id: str, value: object, *, trace_id: str | None = None
    ) -> ChangeRecord | None:
        if config_id != OFFLINE_CATCH_UP_CONFIG_ID:
            raise RegistryValidationError(
                f"{config_id!r} 不属本族（{OFFLINE_CATCH_UP_CONFIG_ID}）"
            )
        return self._policy.set(value, trace_ref=trace_id)  # type: ignore[arg-type]


class McpHubPolicyFamily:
    """L1 MCP 重连策略（``mcp-hub/reconnect-max-retries`` / ``...-interval-ms``）。

    落值委托 :meth:`McpHubConfig.set`（非负整数门与「值未变不留痕」照旧）。
    条目未注册（owner 未调 ``register_defaults``）时**列举为空**——不伪造默认条目。
    """

    config_prefix = "mcp-hub/"
    scope: ConfigScope = "global"

    def __init__(self, config: McpHubConfig) -> None:
        self._config = config

    def entries(self) -> tuple[ConfigEntry, ...]:
        return self._config.list_entries()

    def entry(self, config_id: str) -> ConfigEntry | None:
        for entry in self._config.list_entries():
            if entry.config_id == config_id:
                return entry
        return None

    def apply(
        self, config_id: str, value: object, *, trace_id: str | None = None
    ) -> ChangeRecord | None:
        kwarg = _MCP_KWARGS.get(config_id)
        if kwarg is None:
            raise RegistryValidationError(
                f"{config_id!r} 不属本族（{' / '.join(sorted(_MCP_KWARGS))}）"
            )
        changes = self._config.set(**{kwarg: value}, trace_id=trace_id)  # type: ignore[arg-type]
        return changes[0] if changes else None


class ProviderHostFamily:
    """L1 ``provider → host`` 映射（``llm-provider-host/<provider>``）。

    落值委托 :meth:`ProviderHostRegistry.update`（主机形态校验与「已登记」前置
    照旧）；该 owner **不产生** ``ChangeRecord``，由本族补落一条。
    """

    config_prefix = CONFIG_ID_PREFIX
    scope: ConfigScope = "global"
    change_prefix = "provider-host-change/"

    def __init__(
        self,
        registry: ProviderHostRegistry,
        store,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._registry = registry
        self._store = store
        self._now = _system_now if now is None else now

    def entries(self) -> tuple[ConfigEntry, ...]:
        return self._registry.list()

    def entry(self, config_id: str) -> ConfigEntry | None:
        provider = self._provider_of(config_id)
        if provider is None:
            return None
        try:
            return self._registry.get(provider)
        except Exception:  # 未登记 / 记录损坏 → 本族无此条目（不臆测）
            return None

    def apply(
        self, config_id: str, value: object, *, trace_id: str | None = None
    ) -> ChangeRecord | None:
        provider = self._provider_of(config_id)
        if provider is None:
            raise RegistryValidationError(f"{config_id!r} 不属本族（{CONFIG_ID_PREFIX}*）")
        old = self._registry.resolve(provider)
        if old == value:
            return None
        self._registry.update(provider, value)  # type: ignore[arg-type]
        change = ChangeRecord(
            change_id=new_change_id(), config_id=config_id,
            old_value=old, new_value=value, applied_at=self._now().isoformat(),
            trace_ref=trace_id,
        )
        self._store.put(
            CHANGE_PARTITION, f"{self.change_prefix}{change.change_id}.json",
            change.model_dump_json().encode("utf-8"),
        )
        return change

    def _provider_of(self, config_id: str) -> str | None:
        if not config_id.startswith(self.config_prefix):
            return None
        provider = config_id[len(self.config_prefix):]
        return provider or None
