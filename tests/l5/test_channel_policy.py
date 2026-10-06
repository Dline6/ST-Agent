"""L5 渠道偏好（07 §3–§4）的验收用例。

对齐任务文件 [`T-L5-002.1`](../项目管理/tasks/T-L5-002.1-渠道适配器与渠道偏好.md) 的
GWT-1 / GWT-2 / GWT-5——缺省偏好与来源标注、越界配置逐类点名且不留痕、01 §7 登记与两通道同源。
"""

from __future__ import annotations

import pytest

from l5_helpers import NOW, PASS
from st_agent.l0.storage import Store
from st_agent.l1.registry.errors import RegistryValidationError
from st_agent.l1.registry.facade import ConfigRegistryFacade
from st_agent.l5.channel_policy import (
    DEFAULT_POLICIES,
    LEVELS,
    RESEND_CONFIG_ID,
    ChannelPolicies,
    policy_config_id,
)
from st_agent.l5.channel_registry import channel_policy_family
from st_agent.l5.errors import ChannelValidationError


@pytest.fixture()
def store(tmp_path):
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def policies(store):
    return ChannelPolicies(store=store, now=lambda: NOW)


class TestGwt1Defaults:
    """GWT-1：全新装置得三级齐备的偏好，逐级带来源标注，取向「少而准」。"""

    def test_three_levels_covered_with_defaults(self):
        snapshot = ChannelPolicies().map()
        assert tuple(p.level for p in snapshot) == LEVELS
        assert all(p.source == "default" for p in snapshot)

    def test_emergency_walks_the_full_chain(self):
        policy = ChannelPolicies().get("emergency")
        assert policy.channels == ("desktop", "email", "im_webhook")
        assert policy.waits_minutes == (5, 15)

    def test_routine_takes_only_the_lightest_channel_and_never_escalates(self):
        policy = ChannelPolicies().get("routine")
        assert policy.channels == ("desktop",)
        assert policy.waits_minutes == ()          # 不升级

    def test_chain_and_waits_are_consistent(self):
        for level, (chain, waits) in DEFAULT_POLICIES.items():
            assert len(waits) == len(chain) - 1, level

    def test_next_channel_and_wait_after(self):
        policy = ChannelPolicies().get("emergency")
        assert policy.next_channel(0) == "desktop"
        assert policy.wait_after(0) == 5
        assert policy.wait_after(1) == 15
        assert policy.next_channel(3) is None      # 链尽
        assert policy.wait_after(2) is None

    def test_configured_policy_is_marked_as_configured(self, policies):
        policies.set_policy("important", ["im_webhook", "desktop"], [3])
        assert policies.get("important").source == "configured"
        assert policies.get("routine").source == "default"   # 其余级别不受影响


class TestGwt2Validation:
    """GWT-2：越界配置逐类显式拒绝、不落盘不留痕；取值未变同样不留痕。"""

    @pytest.mark.parametrize("level, channels, waits, hint", [
        ("urgent", ["desktop"], [], "级别"),
        ("emergency", ["carrier_pigeon"], [], "未知渠道"),
        ("emergency", [], [], "不得为空"),
        ("emergency", ["desktop", "desktop"], [5], "两次"),
        ("emergency", ["desktop", "email"], [5, 5], "与链长匹配"),
        ("emergency", ["desktop", "email"], [0], "未读等待"),
        ("emergency", ["desktop", "email"], [-1], "未读等待"),
        ("emergency", ["desktop", "email"], ["5"], "未读等待"),
    ])
    def test_invalid_policy_rejected_and_not_persisted(
        self, policies, store, level, channels, waits, hint
    ):
        before = store.list_files("config")
        with pytest.raises(ChannelValidationError, match=hint):
            policies.set_policy(level, channels, waits)
        assert store.list_files("config") == before        # 不落盘
        assert policies.changes() == ()                    # 不留痕

    def test_unchanged_value_leaves_no_trace(self, policies, store):
        # 写**内置缺省同样的取值**即「值未变」⇒ 不落盘、不留痕（01 §7）
        assert policies.set_policy("emergency", ["desktop", "email", "im_webhook"], [5, 15])
        assert store.list_files("config") == ()
        assert policies.changes() == ()
        # 改一个取值 ⇒ 落盘 + 留痕；再写同一取值 ⇒ 不再新增留痕
        policies.set_policy("emergency", ["im_webhook", "email", "desktop"], [5, 15])
        assert store.list_files("config") != ()
        trace_count = len(policies.changes())
        assert trace_count == 1
        policies.set_policy("emergency", ["im_webhook", "email", "desktop"], [5, 15])
        assert len(policies.changes()) == trace_count

    def test_waits_default_to_current_when_chain_length_unchanged(self, policies):
        policy = policies.set_policy("emergency", ["im_webhook", "email", "desktop"])
        assert policy.waits_minutes == (5, 15)

    def test_corrupt_entry_falls_back_to_default_and_is_exposed(self, policies, store):
        policies.set_policy("emergency", ["im_webhook"], [])
        store.put("config", "channel-delivery/emergency.json", b"{ not json")
        assert policies.get("emergency").source == "default"   # 投递不停摆
        with pytest.raises(ChannelValidationError):            # 损坏仍可显式暴露
            policies.entry(policy_config_id("emergency"))
        assert policies.entry(RESEND_CONFIG_ID) is not None    # 其余条目不受牵连


class TestGwt5Registry:
    """GWT-5：01 §7 登记（两通道同源），对象条目 fail-closed 并点名 owner。"""

    def test_entries_expose_current_values(self, policies):
        policies.set_policy("routine", ["desktop", "email"], [30])
        entries = {e.config_id: e for e in policies.entries()}
        assert set(entries) == {policy_config_id(level) for level in LEVELS} | {RESEND_CONFIG_ID}
        assert entries[policy_config_id("routine")].default == {
            "channels": ["desktop", "email"], "waits_minutes": [30],
        }

    def test_facade_and_owner_read_the_same_value(self, store, policies):
        policies.set_policy("important", ["im_webhook"], [])
        facade = ConfigRegistryFacade(store, now=lambda: NOW)
        facade.register_family(channel_policy_family(policies))
        via_facade = facade.entry(policy_config_id("important"))
        via_owner = policies.entry(policy_config_id("important"))
        assert via_facade is not None and via_owner is not None
        assert via_facade.default == via_owner.default
        listed = {e.config_id for e in facade.list()}
        assert policy_config_id("important") in listed

    def test_object_entries_are_fail_closed_naming_owner(self, store, policies):
        facade = ConfigRegistryFacade(store, now=lambda: NOW)
        facade.register_family(channel_policy_family(policies))
        with pytest.raises(RegistryValidationError, match="set_policy"):
            facade.set(policy_config_id("emergency"), {"channels": ["desktop"]})

    def test_resend_switch_is_writable_through_the_facade(self, store, policies):
        facade = ConfigRegistryFacade(store, now=lambda: NOW)
        facade.register_family(channel_policy_family(policies))
        assert policies.resend_on_reconnect is True
        change = facade.set(RESEND_CONFIG_ID, False)
        assert change is not None and change.new_value is False
        assert policies.resend_on_reconnect is False           # 两通道同源
        assert facade.entry(RESEND_CONFIG_ID).default is False
        assert facade.set(RESEND_CONFIG_ID, False) is None    # 值未变不留痕

    def test_resend_switch_rejects_non_boolean(self, store, policies):
        facade = ConfigRegistryFacade(store, now=lambda: NOW)
        facade.register_family(channel_policy_family(policies))
        with pytest.raises(RegistryValidationError):
            facade.set(RESEND_CONFIG_ID, "yes")

    def test_unknown_entry_in_family_is_rejected(self, store, policies):
        facade = ConfigRegistryFacade(store, now=lambda: NOW)
        facade.register_family(channel_policy_family(policies))
        with pytest.raises(RegistryValidationError, match="已知条目"):
            facade.set("channel-delivery.nonexistent", True)

    def test_family_ignores_foreign_config_id(self, store, policies):
        facade = ConfigRegistryFacade(store, now=lambda: NOW)
        family = channel_policy_family(policies)
        facade.register_family(family)
        assert family.entry("memory-policy.whatever") is None
        with pytest.raises(RegistryValidationError, match="不属本族"):
            family.apply("memory-policy.whatever", 1)


class TestInMemoryMode:
    """无 ``Store`` 的纯内存态：读面与写面照常，只是不落盘、不留痕。"""

    def test_in_memory_set_takes_effect_without_trace(self):
        policies = ChannelPolicies()
        policies.set_policy("important", ["im_webhook"], [])
        assert policies.get("important").channels == ("im_webhook",)
        assert policies.changes() == ()

    def test_in_memory_resend_switch(self):
        policies = ChannelPolicies(resend_default=False)
        assert policies.resend_on_reconnect is False
        assert policies.set_resend_on_reconnect(True) is None
        assert policies.resend_on_reconnect is True
