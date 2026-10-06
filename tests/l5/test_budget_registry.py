"""T-L5-001.2 测试：预算注册进 01 §7 统一配置注册表（[07 §2](../../docs/技术架构-v2/07-L5-主动触达.md)）。

GWT 对照（任务文件 GWT-5）：
- 门面读到的三个条目与 `AttentionBudget.entries()` **同源**（对话通道与面板通道读同一份值）
- `attention-budget.current-mode` 可经门面落值（委托 `switch_mode` 并留痕）
- `slots` / `matrix` 两个**对象取值**条目的落值 **fail-closed 并点名 owner**（01 §7）
"""

from __future__ import annotations

import pytest

from st_agent.l1.registry.errors import (
    RegistryFamilyConflict,
    RegistryValidationError,
)
from st_agent.l1.registry.facade import ConfigRegistryFacade
from st_agent.l5 import (
    MATRIX_CONFIG_ID,
    MODE_CONFIG_ID,
    SLOTS_CONFIG_ID,
    AttentionBudget,
    BudgetKey,
    attention_budget_family,
)
from st_agent.l5.registry_adapter import WRITE_OWNER

OUR_IDS = (MATRIX_CONFIG_ID, MODE_CONFIG_ID, SLOTS_CONFIG_ID)


@pytest.fixture()
def facade(store):
    facade = ConfigRegistryFacade(store)
    facade.register_family(attention_budget_family(AttentionBudget(store=store)))
    return facade


class TestGwt5RegistryChannels:
    """GWT-5：两条配置通道同源；写法按条目取值形态分流。"""

    def test_entries_visible_through_facade(self, facade):
        listed = {e.config_id: e for e in facade.list(scope="global") if e.config_id in OUR_IDS}
        assert set(listed) == set(OUR_IDS)
        assert listed[MATRIX_CONFIG_ID].panel_form_spec.widget == "matrix"
        assert listed[MODE_CONFIG_ID].default == "workday"

    def test_facade_and_owner_agree_on_current_values(self, store, facade):
        budget = AttentionBudget(store=store)
        budget.switch_mode("vacation")
        budget.set_cell(
            BudgetKey(mode="workday", slot_id="market_hours", content_type="brief"),
            {"allowed_levels": ["emergency", "routine"], "max_per_slot": 3},
        )
        # 面板通道（门面）读到的值 == 本层自有面读到的值 → 双通道效果一致
        assert facade.entry(MODE_CONFIG_ID).default == "vacation" == budget.mode
        matrix = facade.entry(MATRIX_CONFIG_ID).default
        assert matrix == {
            "cells": [{
                "mode": "workday", "slot_id": "market_hours", "content_type": "brief",
                "allowed_levels": ["emergency", "routine"], "max_per_slot": 3,
            }]
        }

    def test_mode_writable_through_facade(self, store, facade):
        record = facade.set(MODE_CONFIG_ID, "vacation", trace_id="tr_" + "1" * 20)
        assert record is not None and record.new_value == "vacation"
        assert AttentionBudget(store=store).mode == "vacation"

    def test_object_entries_fail_closed_naming_owner(self, facade):
        for config_id in (SLOTS_CONFIG_ID, MATRIX_CONFIG_ID):
            with pytest.raises(RegistryValidationError, match="不在统一落值面内") as err:
                facade.set(config_id, {"slots": []})
            assert WRITE_OWNER in str(err.value)

    def test_invalid_mode_surfaces_as_registry_error(self, facade):
        with pytest.raises(RegistryValidationError, match="情境模式"):
            facade.set(MODE_CONFIG_ID, "holiday")

    def test_unknown_entry_refused(self, facade):
        with pytest.raises(RegistryValidationError, match="不是本族的已知条目"):
            facade.set("attention-budget.unknown", "x")
        assert facade.entry("attention-budget.unknown") is None

    def test_foreign_config_id_refused(self, facade):
        with pytest.raises(RegistryValidationError, match="未注册该族"):
            facade.set("memory-policy.confidence", {"baseline": 1})

    def test_foreign_config_id_not_claimed(self, facade):
        assert facade.entry("memory-policy.confidence") is None

    def test_family_prefix_conflict_refused(self, store, facade):
        with pytest.raises(RegistryFamilyConflict):
            facade.register_family(attention_budget_family(AttentionBudget(store=store)))
