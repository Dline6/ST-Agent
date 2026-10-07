"""出厂重置（[`reset.py`](../../src/st_agent/l6/reset.py)）的验收用例。

对齐任务文件 [`T-L6-003.3`](../项目管理/tasks/T-L6-003.3-出厂重置.md) 的 GWT-1..4——
三次确认（不足即拒且不留痕）、三件事逐件可查、**Memory 原始数据保留**（机器断言）、
变更历史**归档**而非抹除。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from l6_helpers import NOW, PASS
from st_agent.l0.storage import Store
from st_agent.l1.events import EventBus
from st_agent.l1.registry import ConfigRegistryFacade
from st_agent.l6 import (
    DEFAULT_TIER,
    REPLAY_NOTE,
    RESET_CONFIRMATIONS,
    ChangeProposal,
    FactoryReset,
    FactoryResetError,
    build_l6,
)

TARGET = "weekly-report.time"
SCOPE = "delivery-strategy"
HYPOTHESIS = "把单独触达合并进日报可降低打扰"
REASON = "触达集中在早晨，建议把生成时刻提前"


class Rig:
    """一次装配态（真 ``Store`` + 真门面 + 真总线；自主档，可产可回滚的演进变更）。"""

    def __init__(self, tmp_path) -> None:
        self.store = Store.create(tmp_path / "root", PASS)
        self.facade = ConfigRegistryFacade(self.store)
        self.bus = EventBus()
        self.stack = build_l6(
            self.store, registry=self.facade, events=self.bus, now=lambda: NOW,
        )
        self.stack.authorization.set_tier("autonomous")

    def apply(self, suggested: object = "08:00"):
        run = self.stack.change_flow.submit(
            ChangeProposal(config_id=TARGET, suggested=suggested, reason=REASON),
            source="weekly-report",
        )
        return run.change

    def reset(self):
        return self.stack.factory_reset


class TestGwt1ThreeConfirmations:
    """GWT-1：确认次数不足**即拒**——不留痕、不动数据。"""

    def test_too_few_confirmations_is_refused_without_trace(self, tmp_path):
        rig = Rig(tmp_path)
        rig.apply()
        before = rig.facade.entry(TARGET).default
        outcome = rig.reset().request(RESET_CONFIRMATIONS - 1)
        assert outcome.performed is False
        assert "确认次数不足" in outcome.reason
        assert rig.reset().history() == ()                       # 不留痕
        assert rig.facade.entry(TARGET).default == before        # 一个动作都没做
        assert rig.stack.authorization.tier() == "autonomous"

    def test_three_confirmations_execute(self, tmp_path):
        rig = Rig(tmp_path)
        rig.apply()
        outcome = rig.reset().request(RESET_CONFIRMATIONS)
        assert outcome.performed is True and outcome.reset_id.startswith("rst_")

    def test_non_integer_confirmations_is_refused(self, tmp_path):
        with pytest.raises(FactoryResetError):
            Rig(tmp_path).reset().request(True)

    def test_without_a_change_flow_reset_is_refused(self, tmp_path):
        with pytest.raises(FactoryResetError) as err:
            FactoryReset().request(RESET_CONFIRMATIONS)
        assert "变更流" in str(err.value)


class TestGwt2ThreeEffects:
    """GWT-2：三件事逐件可查——参数恢复、实验停止、档位复位，且各自留痕。"""

    def test_parameters_are_restored_by_replaying_the_history(self, tmp_path):
        rig = Rig(tmp_path)
        rig.facade.set(TARGET, "09:00")
        first = rig.apply("08:00")
        rig.apply("07:00")
        outcome = rig.reset().request(RESET_CONFIRMATIONS)
        assert rig.facade.entry(TARGET).default == "09:00"       # 回到最早一条的旧值
        assert first.change_id in outcome.replayed
        assert outcome.reset_id in {r.reset_id for r in rig.reset().history()}

    def test_first_ever_change_is_restored_to_the_declared_value(self, tmp_path):
        rig = Rig(tmp_path)
        change = rig.apply("08:00")
        outcome = rig.reset().request(RESET_CONFIRMATIONS)
        assert change.change_id in outcome.replayed
        assert rig.facade.entry(TARGET).default == "20:00"

    def test_running_experiments_are_stopped_and_kept(self, tmp_path):
        rig = Rig(tmp_path)
        started = rig.stack.experiments.start(HYPOTHESIS, SCOPE)
        assert started.enabled is True
        experiment_id = started.experiment.experiment_id
        outcome = rig.reset().request(RESET_CONFIRMATIONS)
        assert outcome.experiments_stopped == (experiment_id,)
        kept = rig.stack.experiments.get(experiment_id)
        assert kept is not None and kept.status == "stopped"     # 留痕保留、不删
        assert rig.stack.experiments.running() == ()

    def test_tier_returns_to_the_default(self, tmp_path):
        rig = Rig(tmp_path)
        outcome = rig.reset().request(RESET_CONFIRMATIONS)
        assert outcome.tier_reset is True
        assert rig.stack.authorization.tier() == DEFAULT_TIER

    def test_reset_is_reload_safe(self, tmp_path):
        rig = Rig(tmp_path)
        rig.apply()
        rig.reset().request(RESET_CONFIRMATIONS)
        again = build_l6(rig.store, now=lambda: NOW)
        assert len(again.factory_reset.history()) == 1

    def test_reset_without_any_evolution_change_still_succeeds(self, tmp_path):
        rig = Rig(tmp_path)
        outcome = rig.reset().request(RESET_CONFIRMATIONS)
        assert outcome.performed is True and outcome.replayed == ()


class TestGwt3MemoryIsPreserved:
    """GWT-3：Memory 原始数据**逐字节不变**（[08 §7](../../docs/技术架构-v2/08-L6-反思演进.md) 红线）。"""

    def test_memory_partition_is_untouched(self, tmp_path):
        rig = Rig(tmp_path)
        rig.store.put("memory", "nodes/mn_" + "4" * 20 + ".json", b'{"node": 1}')
        rig.store.put("memory", "edges/ed.json", b'{"edge": 2}')
        before = {name: rig.store.get("memory", name) for name in rig.store.list_files("memory")}
        assert before                                                    # 前置：确有数据
        rig.apply()
        rig.reset().request(RESET_CONFIRMATIONS)
        after = {name: rig.store.get("memory", name) for name in rig.store.list_files("memory")}
        assert after == before

    def test_layer_never_touches_the_deletion_surfaces(self):
        for path in _l6_sources():
            text = path.read_text(encoding="utf-8")
            assert "memory.deleter" not in text, path.name
            assert "delete_node(" not in text, path.name
            assert "wipe_partition(" not in text, path.name

    def test_reset_reports_that_memory_is_out_of_scope(self, tmp_path):
        outcome = Rig(tmp_path).reset().request(RESET_CONFIRMATIONS)
        assert "保留" in outcome.memory_note
        assert REPLAY_NOTE


class TestGwt4HistoryIsArchived:
    """GWT-4：变更历史**归档而非抹除**——重置前的变化仍可查。"""

    def test_history_survives_the_reset(self, tmp_path):
        rig = Rig(tmp_path)
        rig.facade.set(TARGET, "09:00")
        first = rig.apply("08:00")
        rig.reset().request(RESET_CONFIRMATIONS)
        history = rig.stack.change_flow.history()
        assert first.change_id in {c.change_id for c in history}
        archived = rig.stack.change_flow.get(first.change_id)
        assert archived.rolled_back_at is not None               # 可看出它已被回放
        assert archived.rollback_change_id in {c.change_id for c in history}

    def test_untouched_entries_stay_untouched(self, tmp_path):
        rig = Rig(tmp_path)
        rig.apply()
        unrelated = rig.stack.reports.day_of_week()
        rig.reset().request(RESET_CONFIRMATIONS)
        assert rig.stack.reports.day_of_week() == unrelated


def _l6_sources() -> list[Path]:
    return sorted(
        (Path(__file__).resolve().parents[2] / "src" / "st_agent" / "l6").glob("*.py")
    )
