"""数据源开关（story-05「数据源逐个可开关」；01 §7 配置注册表条目）。

story-05 的 In Scope 明列「用户可以逐个开关数据源（比如禁用舆情抓取）」。本模块
是这条的 **owner 侧**：开关值落 ``config`` 分区的 ``data-source/<source_id>.json``，
是**唯一真相源**；同步引擎的 ``enabled`` 映射由 :func:`enabled_task_map` 从这里派生。

**粒度是「源」而消费面是「任务」**：用户看得懂的是源（巨潮 / 深交所 / 东财 / 互动易…），
而 ``InfoSync`` 的开关按 ``task_key`` 收（[`sources.py`](sources.py)）。故本模块把
**源级**布尔映射为**任务级**映射——禁用一个源即禁用其全部任务，用户不必知道哪个源
喂了哪几个任务。

统一注册表的**消费面**（列举 / 落值带 ``change_id`` 留痕）由 L1 门面经
:class:`~st_agent.l1.registry.families.DataSourceSwitchFamily` 接入，与 [02 §6] 的
出网审计开关同款（「所有可配置项必须注册」不留孤儿，[01 §7]）。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, ValidationError

from st_agent.contracts.registry_types import ChangePolicy, ConfigEntry, PanelField
from st_agent.l0.info.errors import InfoValidationError
from st_agent.l0.info.sources import ATTENTION_TASKS, INFO_SOURCES, INFO_TASKS

__all__ = [
    "DEFAULT_SOURCE_ENABLED",
    "SOURCE_SWITCH_PREFIX",
    "SourceSwitch",
    "enabled_task_map",
    "get_source_switches",
    "set_source_switch",
    "source_switch_config_entries",
    "source_switch_config_id",
    "source_switch_path",
]

SOURCE_SWITCH_PREFIX = "data-source/"
"""``config_id`` 前缀（路径前缀式，与 `net-audit/` 同款）。"""

DEFAULT_SOURCE_ENABLED = True
"""缺省＝**开**——开关的语义是「用户可以关掉」，不是「默认关闭」（story-05）。"""

_SOURCE_IDS: frozenset[str] = frozenset(s.source_id for s in INFO_SOURCES)
_SOURCE_BY_ID = {s.source_id: s for s in INFO_SOURCES}


def source_switch_config_id(source_id: str) -> str:
    """源 → ``config_id``（``data-source/<source_id>``）。"""
    return f"{SOURCE_SWITCH_PREFIX}{_check_source(source_id)}"


def source_switch_path(source_id: str) -> str:
    """源 → ``config`` 分区内的相对路径（``config_id`` 主干即文件名）。"""
    return f"{source_switch_config_id(source_id)}.json"


def _check_source(source_id: str) -> str:
    if source_id not in _SOURCE_IDS:
        raise InfoValidationError(f"未注册的信息面源 {source_id!r}")
    return source_id


class SourceSwitch(BaseModel):
    """一个源的开关落盘形态。"""

    model_config = ConfigDict(frozen=True)

    source_id: str
    enabled: bool = DEFAULT_SOURCE_ENABLED


def get_source_switches(store) -> dict[str, bool]:
    """读取**全部**已注册源的开关（无配置即缺省＝开；损坏显式抛，不静默回退）。

    返回的字典**恒含每个注册源**——「没有配置文件」与「显式设为开」在行为上等价，
    但对读面而言「未登记」会让用户看到缺口，故此处一次补齐。
    """
    out: dict[str, bool] = {}
    for source in INFO_SOURCES:
        try:
            raw = store.get("config", source_switch_path(source.source_id))
        except KeyError:
            out[source.source_id] = DEFAULT_SOURCE_ENABLED
            continue
        try:
            record = SourceSwitch.model_validate_json(raw.decode("utf-8"))
        except (ValueError, ValidationError, UnicodeDecodeError) as exc:
            raise InfoValidationError(
                f"数据源开关文件损坏（{source.source_id}）：{exc}"
            ) from exc
        out[source.source_id] = record.enabled
    return out


def set_source_switch(store, source_id: str, enabled: bool) -> bool:
    """写入一个源的开关；返回**取值是否真的变了**（未变则不落盘）。

    与各 owner 的既有取向一致：取值不变的条目**不留痕、不落盘**（[01 §7]）。
    """
    _check_source(source_id)
    if not isinstance(enabled, bool):
        raise InfoValidationError(f"数据源开关须为布尔：{enabled!r}")
    current = get_source_switches(store)[source_id]
    if current == enabled:
        return False
    record = SourceSwitch(source_id=source_id, enabled=enabled)
    store.put("config", source_switch_path(source_id), record.model_dump_json().encode("utf-8"))
    return True


def enabled_task_map(store) -> dict[str, bool]:
    """由源级开关派生**任务级** ``enabled`` 映射（喂给 ``InfoSync.run_task/run_all``）。

    **关注面任务（``coverage='watch'``）不在此列**——它们的缺省是**禁用**且须显式
    注入关注池（[`D-037`](../../../项目管理/决策日志.md) / 05）。若此处把它们一律写进
    映射，一个「源开」就会顺带把关注面任务打开，从而在未注入关注池时刷 ``failed``
    污染 ``sync_state``——故**保持缺省语义**：不写进映射（[`sync._is_enabled`]）。
    """
    switches = get_source_switches(store)
    return {
        task.task_key: switches[task.source_id]
        for task in INFO_TASKS
        if task.task_key not in ATTENTION_TASKS
    }


def source_switch_config_entries() -> tuple[ConfigEntry, ...]:
    """全部源的 01 §7 ``ConfigEntry``（供注册表面板逐条开关；默认值＝开）。"""
    return tuple(
        ConfigEntry(
            config_id=source_switch_config_id(source.source_id),
            display_name=source.name,
            value_schema={"type": "boolean"},
            default=DEFAULT_SOURCE_ENABLED,
            description_for_chat=(
                f"是否从「{source.name}」抓取数据（{source.manual_ref}）。"
                "关闭后该源的同步任务不再发起请求；已缓存的数据仍可查询"
            ),
            panel_form_spec=PanelField(
                widget="toggle", label=source.name,
                help_text=f"关闭即不再向 {source.hosts[0]} 发起抓取；已缓存数据不受影响",
            ),
            scope="global",
            change_policy=ChangePolicy(requires_confirmation=False),
        )
        for source in INFO_SOURCES
    )
