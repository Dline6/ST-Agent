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
的「每次变更产生 ``change_id``（可回滚）」对这两族也成立。``net-audit`` 同此
（前缀 ``net-audit-change/``）：owner 只落开关值，留痕由本族补。
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
from st_agent.l0.info.switch_config import (
    SOURCE_SWITCH_PREFIX,
    get_source_switches,
    set_source_switch,
    source_switch_config_entries,
)
from st_agent.l0.net.audit_config import (
    AUDIT_CONFIG_ID,
    get_audit_policy,
    net_audit_config_entries,
    set_audit_policy,
)
from st_agent.l0.net.web_config import (
    ALLOWED_HOSTS_CONFIG_ID,
    EGRESS_ENABLED_CONFIG_ID,
    KEY_CONFIG_ID,
    PROVIDER_CONFIG_ID,
    SEARCH_CREDENTIAL_ID,
    SEARCH_PROVIDERS,
    get_allowed_hosts,
    get_egress_enabled,
    get_search_provider,
    set_allowed_hosts,
    set_egress_enabled,
    set_search_provider,
    validate_host_patterns,
    web_config_entries,
)
from st_agent.l0.secrets import CredentialVault
from st_agent.l0.secrets.errors import (
    CredentialError,
    CredentialExistsError,
    CredentialNotFoundError,
)
from st_agent.l0.storage.errors import StorageSecretsLockedError
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
    "DataSourceSwitchFamily",
    "McpHubPolicyFamily",
    "NetAuditFamily",
    "ProviderHostFamily",
    "RetentionFamily",
    "SchedulerPolicyFamily",
    "SearchFamily",
    "WebEgressFamily",
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


class NetAuditFamily:
    """L0 出网审计开关（``net-audit/enabled``，[02 §6](../../../docs/技术架构-v2/02-L0-本地优先基座.md)）。

    缺省值＝**关**（D-073）。落值委托
    :func:`~st_agent.l0.net.audit_config.set_audit_policy`（owner 侧的值域校验照旧），
    并**同步热切换**运行中的网关（``gateway.set_audit``）——「不重启进程即生效」
    （GWT-2）由此成立；owner 不产生 ``ChangeRecord``，由本族补落一条。

    :param store: ``Store`` 句柄
    :param gateway: 运行中的 ``EgressGateway``（可选；给定则落值时同步热切换）
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    config_prefix = "net-audit/"
    scope: ConfigScope = "global"
    change_prefix = "net-audit-change/"

    def __init__(self, store, *, gateway=None,
                 now: Callable[[], datetime] | None = None) -> None:
        self._store = store
        self._gateway = gateway
        self._now = _system_now if now is None else now

    def entries(self) -> tuple[ConfigEntry, ...]:
        return tuple(
            e.model_copy(update={"default": get_audit_policy(self._store).enabled})
            for e in net_audit_config_entries()
        )

    def entry(self, config_id: str) -> ConfigEntry | None:
        if config_id != AUDIT_CONFIG_ID:
            return None
        canonical = net_audit_config_entries()[0]
        return canonical.model_copy(
            update={"default": get_audit_policy(self._store).enabled}
        )

    def apply(
        self, config_id: str, value: object, *, trace_id: str | None = None
    ) -> ChangeRecord | None:
        if config_id != AUDIT_CONFIG_ID:
            raise RegistryValidationError(
                f"{config_id!r} 不属本族（{AUDIT_CONFIG_ID}）"
            )
        if not isinstance(value, bool):
            raise RegistryValidationError(f"{config_id} 的取值须为布尔：{value!r}")
        old = get_audit_policy(self._store).enabled
        if old == value:
            return None
        set_audit_policy(self._store, enabled=value)
        if self._gateway is not None:
            self._gateway.set_audit(value)
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



class DataSourceSwitchFamily:
    """L0 信息面数据源开关（``data-source/<source_id>``；story-05「数据源逐个可开关」）。

    条目**按已注册源派生**（[`INFO_SOURCES`](../../../src/st_agent/l0/info/sources.py)
    的 8 个源），缺省值＝**开**。落值委托
    :func:`~st_agent.l0.info.switch_config.set_source_switch`（owner 侧的源名与值域
    校验照旧），owner 不产生 ``ChangeRecord``，由本族补落一条（前缀
    ``data-source-change/``）——与 ``retention`` / ``net-audit`` 同款。

    本族**不反转合根**任何句柄：开关的消费方是同步引擎，由
    :func:`~st_agent.l0.info.switch_config.enabled_task_map` 在发起同步时现取。

    :param store: ``Store`` 句柄
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    config_prefix = SOURCE_SWITCH_PREFIX
    scope: ConfigScope = "global"
    change_prefix = "data-source-change/"

    def __init__(self, store, *, now: Callable[[], datetime] | None = None) -> None:
        self._store = store
        self._now = _system_now if now is None else now

    def entries(self) -> tuple[ConfigEntry, ...]:
        current = get_source_switches(self._store)
        return tuple(
            e.model_copy(update={"default": current[_source_of(e.config_id)]})
            for e in source_switch_config_entries()
        )

    def entry(self, config_id: str) -> ConfigEntry | None:
        source_id = _source_of(config_id, strict=False)
        if source_id is None:
            return None
        canonical = next(
            e for e in source_switch_config_entries() if e.config_id == config_id
        )
        return canonical.model_copy(
            update={"default": get_source_switches(self._store)[source_id]}
        )

    def apply(
        self, config_id: str, value: object, *, trace_id: str | None = None
    ) -> ChangeRecord | None:
        source_id = _source_of(config_id)
        if not isinstance(value, bool):
            raise RegistryValidationError(f"{config_id} 的取值须为布尔：{value!r}")
        old = get_source_switches(self._store)[source_id]
        if old == value:
            return None
        set_source_switch(self._store, source_id, value)
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


def _source_of(config_id: str, *, strict: bool = True) -> str | None:
    """``config_id`` → 源标识；不属本族（或源未注册）时按 ``strict`` 抛或回 ``None``。"""
    if not config_id.startswith(SOURCE_SWITCH_PREFIX):
        if strict:
            raise RegistryValidationError(
                f"{config_id!r} 不属本族（前缀 {SOURCE_SWITCH_PREFIX!r}）"
            )
        return None
    if config_id not in {e.config_id for e in source_switch_config_entries()}:
        if strict:
            raise RegistryValidationError(f"未注册的数据源开关条目：{config_id!r}")
        return None
    return config_id[len(SOURCE_SWITCH_PREFIX):]


class SearchFamily:
    """联网搜索的两条 L0 条目（``search.provider`` / ``search.key``；T-AGT-015.1）。

    ``search.provider`` 落 ``config`` 分区；``search.key`` 的**明文只落加密凭据库**
    （``secrets`` 分区，标识 :data:`SEARCH_CREDENTIAL_ID`），本族只向门面暴露
    「已配置 / 未配置」状态与掩码视图。配置变更由本族补落 ``ChangeRecord``
    （前缀 ``search-change/``），与 ``net-audit`` 同款。

    :param store: ``Store`` 句柄
    :param vault: ``CredentialVault``；缺省 ``None`` 时密钥条目不可落值（显式拒）
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    config_prefix = "search."
    scope: ConfigScope = "global"
    change_prefix = "search-change/"

    def __init__(self, store, *, vault: CredentialVault | None = None,
                 now: Callable[[], datetime] | None = None) -> None:
        self._store = store
        self._vault = vault
        self._now = _system_now if now is None else now

    def entries(self) -> tuple[ConfigEntry, ...]:
        configured = "已配置" if self._key_configured() else ""
        defaults = {
            PROVIDER_CONFIG_ID: get_search_provider(self._store),
            KEY_CONFIG_ID: configured,
        }
        return tuple(
            entry.model_copy(update={"default": defaults[entry.config_id]})
            for entry in web_config_entries()
            if entry.config_id in defaults
        )

    def entry(self, config_id: str) -> ConfigEntry | None:
        for entry in self.entries():
            if entry.config_id == config_id:
                return entry
        return None

    def apply(
        self, config_id: str, value: object, *, trace_id: str | None = None
    ) -> ChangeRecord | None:
        if config_id == PROVIDER_CONFIG_ID:
            if not isinstance(value, str) or value not in ("", *SEARCH_PROVIDERS):
                raise RegistryValidationError(
                    f"{config_id} 取值须为 {('', *SEARCH_PROVIDERS)} 之一：{value!r}"
                )
            old = get_search_provider(self._store)
            if old == value:
                return None
            set_search_provider(self._store, value)
            return self._record(config_id, old, value, trace_id)

        if config_id == KEY_CONFIG_ID:
            if not isinstance(value, str) or not value.strip():
                raise RegistryValidationError("搜索密钥须为非空字符串")
            if len(value) > 8192:
                raise RegistryValidationError("搜索密钥过长（上限 8192 字符）")
            if self._vault is None:
                raise RegistryValidationError(
                    "搜索密钥需要凭据库句柄（组合根未注入 CredentialVault）"
                )
            existed = self._key_configured()
            try:
                if existed:
                    self._vault.rotate(SEARCH_CREDENTIAL_ID, value)
                else:
                    self._vault.add(SEARCH_CREDENTIAL_ID, "search_api_key", value)
            except CredentialExistsError:
                self._vault.rotate(SEARCH_CREDENTIAL_ID, value)
            except (StorageSecretsLockedError, CredentialError) as exc:
                raise RegistryValidationError(
                    f"搜索密钥写入失败（凭据分区未解锁或凭据库不可用）：{exc}"
                ) from exc
            # 明文不进 ChangeRecord：只记状态变化。
            return self._record(
                config_id, "已配置" if existed else "未配置", "已配置", trace_id
            )

        raise RegistryValidationError(f"{config_id!r} 不属本族（search.provider / search.key）")

    def _key_configured(self) -> bool:
        if self._vault is None:
            return False
        try:
            self._vault.get_view(SEARCH_CREDENTIAL_ID)
        except (CredentialError, StorageSecretsLockedError):
            return False
        return True

    def _record(
        self, config_id: str, old: object, new: object, trace_id: str | None
    ) -> ChangeRecord:
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


class WebEgressFamily:
    """开放网络开关与域名名单（``web-egress/*``；T-AGT-015.3）。

    两条均为 L0 owner 的标量条目：开关落 ``web-egress/enabled.json``，名单落
    ``web-egress/allowed-hosts.json``；变更由本族补落 ``ChangeRecord``（前缀
    ``web-egress-change/``）。关闭态与名单拦截的**消费方**是两条原语，它们在
    每次调用时现读，故落值即对下一次调用生效。

    :param store: ``Store`` 句柄
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    config_prefix = "web-egress/"
    scope: ConfigScope = "global"
    change_prefix = "web-egress-change/"

    def __init__(self, store, *, now: Callable[[], datetime] | None = None) -> None:
        self._store = store
        self._now = _system_now if now is None else now

    def entries(self) -> tuple[ConfigEntry, ...]:
        defaults = {
            EGRESS_ENABLED_CONFIG_ID: get_egress_enabled(self._store),
            ALLOWED_HOSTS_CONFIG_ID: get_allowed_hosts(self._store),
        }
        return tuple(
            entry.model_copy(update={"default": defaults[entry.config_id]})
            for entry in web_config_entries()
            if entry.config_id in defaults
        )

    def entry(self, config_id: str) -> ConfigEntry | None:
        for entry in self.entries():
            if entry.config_id == config_id:
                return entry
        return None

    def apply(
        self, config_id: str, value: object, *, trace_id: str | None = None
    ) -> ChangeRecord | None:
        if config_id == EGRESS_ENABLED_CONFIG_ID:
            if not isinstance(value, bool):
                raise RegistryValidationError(f"{config_id} 的取值须为布尔：{value!r}")
            old = get_egress_enabled(self._store)
            if old == value:
                return None
            set_egress_enabled(self._store, value)
            return self._record(config_id, old, value, trace_id)

        if config_id == ALLOWED_HOSTS_CONFIG_ID:
            if not isinstance(value, str):
                raise RegistryValidationError(f"{config_id} 的取值须为字符串：{value!r}")
            try:
                validate_host_patterns(value)
            except Exception as exc:  # noqa: BLE001 —— 统一为本层失败形态
                raise RegistryValidationError(str(exc)) from exc
            old = get_allowed_hosts(self._store)
            if old == value:
                return None
            set_allowed_hosts(self._store, value)
            return self._record(config_id, old, value, trace_id)

        raise RegistryValidationError(
            f"{config_id!r} 不属本族（web-egress/enabled / web-egress/allowed-hosts）"
        )

    def _record(
        self, config_id: str, old: object, new: object, trace_id: str | None
    ) -> ChangeRecord:
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
