"""T-L5-001.2 测试：注意力预算与情境模式（[07 §2](../../docs/技术架构-v2/07-L5-主动触达.md)）。

GWT 对照（任务文件 8 条）：
- GWT-1 缺省矩阵完整（5×5×4）且「少而准」，逐格带来源标注
- GWT-2 判定给出格位坐标 / 允许级别 / 节奏上限 / `dispatch` / 中性理由
- GWT-3 `routine` 未配进格位即汇总进日报（**不丢弃**）；配进去即改判单独触达
- GWT-4 模式切换下一次判定即生效、落盘存活；非法模式即拒
- GWT-6 越界配置逐类显式拒、不落盘不留痕；取值未变同样不落盘不留痕
- GWT-7 跨零点时段判定正确
- GWT-8 条目损坏 → 回落缺省并**显式暴露**（`entry()` 报错），不停摆
"""

from __future__ import annotations

from datetime import time

import pytest
from pydantic import ValidationError

from l5_helpers import NOW, PASS
from st_agent.contracts.neutrality import NeutralityGuard
from st_agent.l0.storage import Store
from st_agent.l5 import (
    CONTENT_TYPES,
    DEFAULT_CELL,
    DEFAULT_SLOTS,
    MATRIX_CONFIG_ID,
    MODE_CONFIG_ID,
    SLOTS_CONFIG_ID,
    SITUATION_MODES,
    AttentionBudget,
    BudgetCell,
    BudgetKey,
    BudgetValidationError,
    TimeSlot,
    default_budget,
)

ALL_LEVELS = {"emergency", "important", "routine"}


def key(mode="workday", slot_id="morning_commute", content_type="long_form") -> BudgetKey:
    return BudgetKey(mode=mode, slot_id=slot_id, content_type=content_type)


class TestGwt1DefaultMap:
    """GWT-1：全新装置的预算地图完整、来源可辨、缺省少而准。"""

    def test_map_is_full_cross_product(self, budget):
        m = budget.map()
        assert m.modes == SITUATION_MODES
        assert m.content_types == CONTENT_TYPES
        assert m.current_mode == "workday"
        assert len(m.slots) == 5
        assert len(m.cells) == len(SITUATION_MODES) * len(m.slots) * len(CONTENT_TYPES) == 100
        assert {c.key.mode for c in m.cells} == set(SITUATION_MODES)

    def test_unconfigured_cell_falls_back_to_least_noise(self, budget):
        cell = budget.cell(key(mode="vacation", slot_id="market_hours", content_type="brief"))
        assert cell.allowed_levels == frozenset({"emergency"})
        assert cell.max_per_slot == 1
        assert budget.map().view(key(mode="vacation", slot_id="market_hours",
                                     content_type="brief")).source == "default"

    def test_three_builtin_overrides_match_story_lines(self, budget):
        for over_key in (
            key("workday", "morning_commute", "long_form"),      # 早通勤 20 分钟能看长内容
            key("workday", "evening", "interactive"),            # 晚上 30 分钟交互式对话
            key("weekend", "afternoon", "retrospective"),        # 周末长周期回顾
        ):
            cell = budget.cell(over_key)
            assert cell.allowed_levels == frozenset(ALL_LEVELS)
            assert cell.max_per_slot == 2
            assert budget.map().view(over_key).source == "configured"

    def test_every_cell_admits_emergency(self, budget):
        """预算不挡紧急信号——矩阵里没有一格会把 `emergency` 挡在日报里。"""
        assert all("emergency" in c.allowed_levels for c in budget.map().cells)

    def test_default_slots_cover_the_day_without_gaps(self, budget):
        for hour, minute in ((0, 0), (7, 0), (9, 29), (9, 30), (14, 59), (15, 0),
                             (19, 0), (22, 29), (22, 30), (23, 59)):
            verdict = budget.resolve(level="emergency", content_type="brief",
                                     at=time(hour, minute))
            assert verdict.key.slot_id in {s.slot_id for s in DEFAULT_SLOTS}

    def test_mode_entry_is_global_scope_with_matrix_panel(self, budget):
        entries = {e.config_id: e for e in budget.entries()}
        assert set(entries) == {MODE_CONFIG_ID, SLOTS_CONFIG_ID, MATRIX_CONFIG_ID}
        assert all(e.scope == "global" for e in entries.values())
        assert entries[MATRIX_CONFIG_ID].panel_form_spec.widget == "matrix"
        assert entries[MODE_CONFIG_ID].panel_form_spec.choices == SITUATION_MODES
        assert entries[MODE_CONFIG_ID].change_policy.requires_confirmation is True


class TestGwt2Verdict:
    """GWT-2：判定面（格位坐标 + 允许级别 + 节奏上限 + `dispatch` + 中性理由）。"""

    def test_verdict_fields(self, budget):
        v = budget.resolve(level="important", content_type="long_form", at=time(8, 0))
        assert v.key == key("workday", "morning_commute", "long_form")
        assert v.allowed_levels == frozenset(ALL_LEVELS)
        assert v.max_per_slot == 2
        assert v.admitted is True
        assert v.dispatch == "separate"
        assert v.source == "configured"

    def test_verdict_reason_is_neutral_and_informative(self, budget):
        v = budget.resolve(level="routine", content_type="brief", at=time(11, 0))
        assert NeutralityGuard().check_output(v.reason).passed
        assert "workday" in v.reason and "盘中" in v.reason and "brief" in v.reason

    def test_resolve_accepts_hhmm_string(self, budget):
        assert budget.resolve(level="emergency", content_type="brief",
                              at="11:00").key.slot_id == "market_hours"

    @pytest.mark.parametrize("bad", [
        {"level": "urgent"},
        {"content_type": "long"},
        {"at": "25:00"},
        {"at": 11},
    ])
    def test_invalid_inputs_rejected(self, budget, bad):
        args = {"level": "emergency", "content_type": "brief", "at": time(11, 0), **bad}
        with pytest.raises(BudgetValidationError):
            budget.resolve(**args)

    def test_resolve_is_pure(self, budget):
        """本层不做计数（记账归 07 §6 / T-L5-003）——同一输入恒得同一结论、不落盘。"""
        first = budget.resolve(level="important", content_type="brief", at=time(11, 0))
        second = budget.resolve(level="important", content_type="brief", at=time(11, 0))
        assert first == second


class TestGwt3RoutineRouting:
    """GWT-3：未配进格位的级别**汇总进日报**，不丢弃；配进去即改判单独触达。"""

    def test_routine_defaults_to_daily_report(self, budget):
        v = budget.resolve(level="routine", content_type="brief", at=time(11, 0))
        assert v.admitted is False
        assert v.dispatch == "daily_report"
        assert "不丢弃" in v.reason

    def test_important_also_routes_to_daily_report_by_default(self, budget):
        v = budget.resolve(level="important", content_type="brief", at=time(11, 0))
        assert v.dispatch == "daily_report"

    def test_emergency_always_goes_out_separately(self, budget):
        v = budget.resolve(level="emergency", content_type="brief", at=time(3, 0))
        assert (v.admitted, v.dispatch) == (True, "separate")

    def test_user_opt_in_flips_routine_to_separate(self, budget):
        target = key("workday", "market_hours", "brief")
        budget.set_cell(target, BudgetCell(
            allowed_levels=frozenset({"emergency", "routine"}), max_per_slot=1,
        ))
        v = budget.resolve(level="routine", content_type="brief", at=time(11, 0))
        assert (v.admitted, v.dispatch, v.source) == (True, "separate", "configured")
        # 用户没放行的另一级别仍进日报
        assert budget.resolve(level="important", content_type="brief",
                              at=time(11, 0)).dispatch == "daily_report"


class TestGwt4ModeSwitch:
    """GWT-4：切换即时生效 + 落盘存活；非法模式即拒。"""

    def test_switch_takes_effect_on_next_verdict(self, stored_budget):
        weekend = key("weekend", "afternoon", "retrospective")
        assert stored_budget.resolve(level="routine", content_type="retrospective",
                                     at=time(16, 0)).dispatch == "daily_report"
        stored_budget.switch_mode("weekend")
        assert stored_budget.resolve(level="routine", content_type="retrospective",
                                     at=time(16, 0)).dispatch == "separate"

    def test_switch_survives_reopen(self, store):
        first = AttentionBudget(store=store, now=lambda: NOW)
        first.switch_mode("vacation")
        second = AttentionBudget(store=store, now=lambda: NOW)
        assert second.mode == "vacation"
        assert second.resolve(level="routine", content_type="brief",
                              at=time(8, 0)).dispatch == "daily_report"

    def test_switch_records_change(self, stored_budget):
        record = stored_budget.switch_mode("business_trip", trace_ref="tr_" + "1" * 20)
        assert record is not None
        assert record.config_id == MODE_CONFIG_ID
        assert record.old_value is None and record.new_value == "business_trip"
        assert record.trace_ref == "tr_" + "1" * 20
        assert [c.change_id for c in stored_budget.changes()] == [record.change_id]

    def test_switch_to_same_mode_leaves_no_trace(self, stored_budget):
        stored_budget.switch_mode("weekend")
        assert stored_budget.switch_mode("weekend") is None
        assert len(stored_budget.changes()) == 1

    def test_invalid_mode_rejected_and_mode_unchanged(self, stored_budget):
        with pytest.raises(BudgetValidationError, match="情境模式"):
            stored_budget.switch_mode("holiday")
        assert stored_budget.mode == "workday"
        assert stored_budget.changes() == ()

    def test_memory_only_switch_needs_no_store(self, budget):
        assert budget.switch_mode("urgent") is None
        assert budget.mode == "urgent"
        assert budget.changes() == ()


class TestGwt6InvalidConfig:
    """GWT-6：越界配置逐类显式拒，且**不落盘、不留痕**。"""

    @pytest.mark.parametrize("bad_slot", ["unknown_slot", "夜間"])
    def test_unknown_slot_id_rejected(self, stored_budget, bad_slot):
        with pytest.raises(BudgetValidationError, match="未知时段 id"):
            stored_budget.set_cell(key(slot_id=bad_slot), DEFAULT_CELL)

    @pytest.mark.parametrize("bad_cell, hint", [
        ({"allowed_levels": ["important"], "max_per_slot": 1}, "emergency"),
        ({"allowed_levels": [], "max_per_slot": 1}, "emergency"),
        ({"allowed_levels": ["emergency", "urgent"], "max_per_slot": 1}, "allowed_levels"),
        ({"allowed_levels": ["emergency"], "max_per_slot": 0}, "max_per_slot"),
        ({"allowed_levels": "emergency", "max_per_slot": 1}, "allowed_levels"),
        ({"allowed_levels": ["emergency"]}, "缺字段"),
        ("not-a-cell", "格位须为"),
    ])
    def test_invalid_cell_rejected(self, store, stored_budget, bad_cell, hint):
        with pytest.raises(BudgetValidationError, match=hint):
            stored_budget.set_cell(key(), bad_cell)
        assert stored_budget.changes() == ()
        assert store.list_files("config") == ()

    def test_cell_invariant_enforced_at_value_object(self):
        with pytest.raises(ValidationError, match="emergency"):
            BudgetCell(allowed_levels=frozenset({"routine"}), max_per_slot=1)
        with pytest.raises(ValidationError):
            BudgetCell(allowed_levels=frozenset({"emergency"}), max_per_slot=0)

    @pytest.mark.parametrize("bad_key", [
        {"mode": "holiday"}, {"content_type": "long"}, {"slot_id": 1},
    ])
    def test_invalid_key_rejected_at_value_object(self, bad_key):
        args = {"mode": "workday", "slot_id": "morning_commute",
                "content_type": "long_form", **bad_key}
        with pytest.raises(ValidationError):
            BudgetKey(**args)

    @pytest.mark.parametrize("slots, hint", [
        ((), "不得为空"),
        (({"slot_id": "a", "label": "全段", "start": "00:00", "end": "00:00"},), "零长"),
        (({"slot_id": "a", "label": "半天", "start": "00:00", "end": "12:00"},), "未覆盖全天"),
        (DEFAULT_SLOTS + (TimeSlot(slot_id="extra", label="重", start=time(0, 0),
                                   end=time(1, 0)),), "未覆盖全天或相互重叠"),
        (tuple(reversed(DEFAULT_SLOTS)), None),   # 同一套时段换顺序 → 合法
    ])
    def test_slot_set_validation(self, budget, slots, hint):
        if hint is None:
            assert budget.set_slots(slots) is None
            return
        with pytest.raises(BudgetValidationError, match=hint):
            budget.set_slots(slots)

    def test_duplicate_slot_id_rejected(self, budget):
        duplicated = (
            TimeSlot(slot_id="whole", label="上半", start=time(0, 0), end=time(12, 0)),
            TimeSlot(slot_id="whole", label="下半", start=time(12, 0), end=time(0, 0)),
        )
        with pytest.raises(BudgetValidationError, match="时段 id 重复"):
            budget.set_slots(duplicated)

    def test_set_cell_accepts_mapping_form(self, stored_budget):
        record = stored_budget.set_cell(
            key("workday", "market_hours", "brief"),
            {"allowed_levels": ["emergency", "routine"], "max_per_slot": 3},
        )
        assert record is not None and record.config_id == MATRIX_CONFIG_ID
        assert stored_budget.cell(key("workday", "market_hours", "brief")).max_per_slot == 3

    def test_unchanged_cell_leaves_no_trace(self, stored_budget):
        target = key("workday", "market_hours", "brief")
        payload = {"allowed_levels": ["emergency", "routine"], "max_per_slot": 2}
        assert stored_budget.set_cell(target, payload) is not None
        assert stored_budget.set_cell(target, payload) is None
        assert len(stored_budget.changes()) == 1

    def test_invalid_slot_replacement_leaves_no_trace(self, stored_budget):
        with pytest.raises(BudgetValidationError):
            stored_budget.set_slots(())
        assert stored_budget.changes() == ()
        assert stored_budget.slots() == DEFAULT_SLOTS


class TestGwt7CrossMidnight:
    """GWT-7：跨零点时段（07 §2 的时钟区间口径）。"""

    def test_night_slot_wraps_midnight(self, budget):
        assert budget.resolve(level="emergency", content_type="brief",
                              at=time(2, 0)).key.slot_id == "night"
        assert budget.resolve(level="emergency", content_type="brief",
                              at=time(23, 30)).key.slot_id == "night"
        assert budget.resolve(level="emergency", content_type="brief",
                              at=time(12, 0)).key.slot_id == "market_hours"

    def test_covers_is_half_open(self):
        night = [s for s in DEFAULT_SLOTS if s.slot_id == "night"][0]
        assert night.covers(time(22, 30)) is True
        assert night.covers(time(7, 0)) is False      # 结束时刻不含
        assert night.covers(time(7, 0, 1)) is False

    def test_user_slots_can_cross_midnight(self, budget):
        budget.set_slots((
            {"slot_id": "sleep", "label": "静默", "start": "23:00", "end": "07:00"},
            {"slot_id": "awake", "label": "清醒", "start": "07:00", "end": "23:00"},
        ))
        assert budget.resolve(level="emergency", content_type="brief",
                              at=time(2, 0)).key.slot_id == "sleep"
        assert budget.resolve(level="emergency", content_type="brief",
                              at=time(9, 0)).key.slot_id == "awake"


class TestGwt8CorruptEntry:
    """GWT-8：条目损坏 → 回落缺省并显式暴露，不停摆。"""

    def test_corrupt_matrix_falls_back_and_stays_visible(self, store):
        store.put("config", "attention-budget/matrix.json", b"{not json")
        b = AttentionBudget(store=store, now=lambda: NOW)
        unconfigured = key(mode="vacation", slot_id="market_hours", content_type="brief")
        assert b.cell(unconfigured).allowed_levels == frozenset({"emergency"})   # 回落缺省
        with pytest.raises(BudgetValidationError, match="损坏"):
            b.entry(MATRIX_CONFIG_ID)                                           # 显式暴露

    def test_corrupt_mode_entry_falls_back_to_default(self, store):
        store.put("config", "attention-budget/current-mode.json",
                  b'{"config_id": "attention-budget.current-mode"}')
        b = AttentionBudget(store=store, now=lambda: NOW)
        assert b.mode == "workday"

    def test_corrupt_slots_entry_falls_back(self, store):
        store.put("config", "attention-budget/slots.json", b'{"slots": [{"slot_id": "x"}]}')
        b = AttentionBudget(store=store, now=lambda: NOW)
        assert b.slots() == DEFAULT_SLOTS

    def test_opaque_file_round_trip(self, tmp_path):
        """条目与留痕都落在既有两分区，不新造分区（01 §7）。"""
        store = Store.create(tmp_path / "rt", PASS)
        b = AttentionBudget(store=store, now=lambda: NOW)
        b.set_cell(key("workday", "market_hours", "brief"),
                   {"allowed_levels": ["emergency", "routine"], "max_per_slot": 2})
        b.switch_mode("weekend")
        assert sorted(store.list_files("config")) == [
            "attention-budget/current-mode.json", "attention-budget/matrix.json",
        ]
        changes = store.list_files("execution_log")
        assert len(changes) == 2
        assert all(name.startswith("attention-budget-change/") for name in changes)

    def test_default_budget_has_no_store(self):
        assert default_budget().changes() == ()
