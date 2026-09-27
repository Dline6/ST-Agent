"""T-L2-002.1 · 自主写入白名单与写入策略（04 §4 首句；GWT-1..5）。"""

from __future__ import annotations

import pytest
from memory_helpers import inferred, node

from st_agent.l0.storage import Store
from st_agent.l2.memory import (
    DEFAULT_WRITE_WHITELIST,
    NODE_TYPES,
    WRITE_WHITELIST_CONFIG_ID,
    MemoryPolicyStore,
    MemoryValidationError,
    WritePolicy,
)

WRITELIST_PATH = "memory-policy/write-whitelist.json"


class TestGwt1EntryIsConfigRegistryEntry:
    """GWT-1：白名单是 01 §7 条目（七字段齐备），取值域＝§1 六类节点。"""

    def test_entry_carries_all_seven_fields(self, policy: WritePolicy) -> None:
        entry = policy.entry()
        assert entry.config_id == WRITE_WHITELIST_CONFIG_ID
        assert entry.display_name
        assert entry.value_schema["items"]["enum"] == list(NODE_TYPES)
        assert entry.default == list(policy.dimensions())
        assert entry.description_for_chat
        assert entry.panel_form_spec.choices == NODE_TYPES
        assert entry.scope == "global"
        assert entry.change_policy.requires_confirmation is True

    def test_updates_land_in_config_partition(self, store: Store, policy: WritePolicy) -> None:
        assert WRITELIST_PATH not in store.list_files("config")
        policy.set_dimensions(["thesis"])
        assert WRITELIST_PATH in store.list_files("config")

    def test_dimensions_are_always_within_six_node_types(self, policy: WritePolicy) -> None:
        assert set(policy.dimensions()) <= set(NODE_TYPES)


class TestGwt2WhitelistDecidesDirectOrPropose:
    """GWT-2：白名单内直写、白名单外须询问。"""

    def test_whitelisted_dimension_is_direct(self, policy: WritePolicy) -> None:
        assert policy.dimensions() == DEFAULT_WRITE_WHITELIST
        assert policy.decide(inferred("pattern", pattern="总在周一冲动加仓")) == "direct"

    def test_unlisted_dimension_must_ask(self, policy: WritePolicy) -> None:
        assert "identity" not in policy.dimensions()
        assert policy.decide(inferred("identity", risk_preference="稳健")) == "propose"

    def test_decision_follows_the_config_not_a_frozen_table(self, policy: WritePolicy) -> None:
        policy.set_dimensions(["identity"])
        assert policy.decide(inferred("identity", risk_preference="稳健")) == "direct"
        assert policy.decide(inferred("pattern", pattern="总在周一冲动加仓")) == "propose"


class TestGwt3WhitelistOnlyGatesInferredWrites:
    """GWT-3：白名单只约束副驾推断面——用户显式写入不过本门（§3.2 直写）。"""

    def test_user_stated_is_direct_even_outside_the_whitelist(self, policy: WritePolicy) -> None:
        assert "thesis" not in policy.dimensions()
        assert policy.decide(node("thesis")) == "direct"

    def test_same_dimension_differs_by_source(self, policy: WritePolicy) -> None:
        assert policy.decide(node("identity")) == "direct"
        assert policy.decide(inferred("identity")) == "propose"


class TestGwt4ChangesTakeEffectAndLeaveATrace:
    """GWT-4：改配置即生效且留痕；值未变不留痕。"""

    def test_change_produces_a_change_record(self, store: Store, policy: WritePolicy) -> None:
        change = policy.set_dimensions(["pattern", "thesis"])
        assert change is not None
        assert change.config_id == WRITE_WHITELIST_CONFIG_ID
        assert change.old_value == list(DEFAULT_WRITE_WHITELIST)
        assert change.new_value == ["thesis", "pattern"]          # 规范化为 §1 六类序
        assert f"memory-policy-change/{change.change_id}.json" in store.list_files("execution_log")

    def test_change_takes_effect_immediately(self, policy: WritePolicy) -> None:
        policy.set_dimensions(["pattern", "thesis"])
        assert policy.dimensions() == ("thesis", "pattern")
        assert policy.decide(inferred("pattern", pattern="总在周一冲动加仓")) == "direct"

    def test_unchanged_value_leaves_no_trace(self, policy: WritePolicy) -> None:
        policy.set_dimensions(["thesis"])
        assert len(policy.changes()) == 1
        assert policy.set_dimensions(["thesis"]) is None
        assert len(policy.changes()) == 1

    def test_duplicates_are_normalized_not_duplicated(self, policy: WritePolicy) -> None:
        policy.set_dimensions(["thesis", "thesis", "pattern"])
        assert policy.dimensions() == ("thesis", "pattern")


class TestGwt5OutOfRangeAndCorruptEntries:
    """GWT-5：越界取值显式失败；条目缺失 / 损坏时回落缺省值而非停摆。"""

    def test_unknown_dimension_is_rejected(self, policy: WritePolicy) -> None:
        with pytest.raises(MemoryValidationError, match="未知的记忆维度"):
            policy.set_dimensions(["thesis", "mood"])

    def test_missing_entry_falls_back_to_default(self, policy: WritePolicy) -> None:
        assert policy.dimensions() == DEFAULT_WRITE_WHITELIST

    def test_corrupt_entry_falls_back_to_default(self, store: Store, policy: WritePolicy) -> None:
        store.put("config", WRITELIST_PATH, b"{ not json")
        assert policy.dimensions() == DEFAULT_WRITE_WHITELIST

    def test_corrupt_entry_is_still_inspectable(self, store: Store) -> None:
        store.put("config", WRITELIST_PATH, b"{ not json")
        with pytest.raises(MemoryValidationError, match="损坏"):
            MemoryPolicyStore(store).entry(WRITE_WHITELIST_CONFIG_ID)
