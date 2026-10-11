"""数据源开关（story-05「数据源逐个可开关」；[`T-UI-018.1`]）。

本组锁四件事：

1. **缺省＝开**——未落盘的源读回 `True`，且**每个注册源都在**（不留缺口）；
2. **落值即真相源**——`set_source_switch` 落 `config/data-source/<id>.json`，读回一致；
3. **取值未变不落盘**——与各 owner 的既有取向一致（[01 §7]）；
4. **任务级映射的缺省语义**——[`enabled_task_map`] 由源级开关派生，**不把关注面任务
   写进映射**（它们的缺省是禁用、须注入关注池，写进去会让「源开」顺带打开它们）。
"""

from __future__ import annotations

import pytest

from st_agent.l0.info.sources import ATTENTION_TASKS, INFO_SOURCES, INFO_TASKS
from st_agent.l0.info.switch_config import (
    DEFAULT_SOURCE_ENABLED,
    enabled_task_map,
    get_source_switches,
    set_source_switch,
    source_switch_config_entries,
    source_switch_config_id,
    source_switch_path,
)


def test_defaults_are_all_on_and_cover_every_registered_source(store) -> None:
    """GWT: 未落盘 ⇒ 缺省＝开，且**每个注册源都有值**（不留缺口）。"""
    switches = get_source_switches(store)
    assert switches == {source.source_id: DEFAULT_SOURCE_ENABLED for source in INFO_SOURCES}
    assert set(switches) == {source.source_id for source in INFO_SOURCES}


def test_switch_round_trips_through_the_config_partition(store) -> None:
    """GWT: 落值落 `config` 分区、按 `config_id` 主干确定路径，读回一致。"""
    assert set_source_switch(store, "eastmoney", False) is True
    assert store.get("config", source_switch_path("eastmoney"))
    assert get_source_switches(store)["eastmoney"] is False
    assert source_switch_config_id("eastmoney") == "data-source/eastmoney"
    # 只动了这一个源
    assert get_source_switches(store)["cninfo"] is DEFAULT_SOURCE_ENABLED


def test_unchanged_value_writes_nothing(store) -> None:
    """GWT: 取值未变 ⇒ 不落盘（与 retention / net-audit 的既有取向一致）。"""
    assert set_source_switch(store, "sse", DEFAULT_SOURCE_ENABLED) is False
    with pytest.raises(KeyError):
        store.get("config", source_switch_path("sse"))


def test_unknown_source_is_rejected(store) -> None:
    """未注册的源一律显式拒——不静默写一个无人消费的条目。"""
    from st_agent.l0.info.errors import InfoValidationError

    with pytest.raises(InfoValidationError):
        set_source_switch(store, "不存在的源", False)
    with pytest.raises(InfoValidationError):
        source_switch_config_id("不存在的源")


def test_non_boolean_value_is_rejected(store) -> None:
    """取值须为布尔（落值面按声明类型校验，不猜）。"""
    from st_agent.l0.info.errors import InfoValidationError

    with pytest.raises(InfoValidationError):
        set_source_switch(store, "cninfo", "false")


def test_enabled_map_derives_from_sources_and_keeps_attention_defaults(store) -> None:
    """GWT: 关掉一个源 ⇒ 其全部任务在映射里为 `False`；关注面任务**不出现**在映射里。"""
    baseline = enabled_task_map(store)
    assert all(baseline[task.task_key] is True for task in INFO_TASKS
               if task.task_key not in ATTENTION_TASKS)
    assert set(baseline) == {
        task.task_key for task in INFO_TASKS if task.task_key not in ATTENTION_TASKS
    }
    assert not (set(baseline) & set(ATTENTION_TASKS)), (
        "关注面任务的缺省是禁用、须注入关注池——写进映射会让「源开」顺带打开它们"
    )

    set_source_switch(store, "eastmoney", False)
    disabled = enabled_task_map(store)
    affected = [task.task_key for task in INFO_TASKS if task.source_id == "eastmoney"]
    assert affected, "本用例的前提是该源至少喂一个任务"
    assert all(disabled[key] is False for key in affected if key in disabled)
    assert all(disabled[task.task_key] is True for task in INFO_TASKS
               if task.source_id != "eastmoney" and task.task_key not in ATTENTION_TASKS)


def test_entries_are_registry_shaped_and_defaults_follow_current_value(store) -> None:
    """GWT: 条目形态合 [01 §7]（`value_schema` / `panel_form_spec` / `scope`），
    `default` 取**当前值**——面板据此回显「启用 / 停用」。"""
    entries = source_switch_config_entries()
    assert len(entries) == len(INFO_SOURCES)
    for entry in entries:
        assert entry.value_schema == {"type": "boolean"}
        assert entry.scope == "global"
        assert entry.panel_form_spec.widget == "toggle"
        assert entry.default is True
    set_source_switch(store, entries[0].config_id.rsplit("/", 1)[-1], False)


def test_enabled_map_is_consumed_by_the_sync_engine(store) -> None:
    """**接线证据**：映射的键就是 `InfoSync` 的开关键——被关闭的源下任务恒跳过。

    `_is_enabled` 是同步引擎**唯一**的开关判据；此处直接断言「本模块产出的映射喂进去
    即生效」，无需起抓取器（真实抓取归 live 套件）。
    """
    from st_agent.l0.info.sync import InfoSync

    set_source_switch(store, "cninfo", False)
    mapping = enabled_task_map(store)
    targets = [task.task_key for task in INFO_TASKS if task.source_id == "cninfo"]
    assert targets, "本用例的前提是巨潮至少喂一个任务"
    for key in targets:
        assert InfoSync._is_enabled(key, mapping) is False
    opened = [task.task_key for task in INFO_TASKS
              if task.source_id != "cninfo" and task.task_key not in ATTENTION_TASKS]
    assert all(InfoSync._is_enabled(key, mapping) is True for key in opened)
