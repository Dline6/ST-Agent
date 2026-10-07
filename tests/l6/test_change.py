"""变更流与单项回滚（[`change.py`](../../src/st_agent/l6/change.py)）的验收用例。

对齐任务文件 [`T-L6-003.2`](../项目管理/tasks/T-L6-003.2-变更流与变更历史回滚.md) 的 GWT-1..6——
审批门按档位与风险类、生效经 01 §7 门面并记入变更历史、告知是发布事件（未接总线不假装送达）、
一键接受 / 否决 / 延后、按 `change_id` 一键回滚、**不存在静默调参路径**。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from l6_helpers import NOW, PASS
from st_agent.l0.storage import Store
from st_agent.l1.events import EventBus
from st_agent.l1.registry import ConfigRegistryFacade
from st_agent.l6 import (
    CHANGE_APPLIED,
    CHANGE_ROLLED_BACK,
    NO_AUTHORIZATION_REASON,
    NO_REGISTRY_REASON,
    AuthorizationError,
    ChangeFlowError,
    ChangeProposal,
    build_l6,
)

AUTONOMOUS_OK = "weekly-report.time"          # 风险类＝可自主（表现层参数）
NEEDS_APPROVAL = "proposal.rate-limit"        # 未分类 ⇒ 保守（必须协作）
RED_LINE = "memory-policy/dynamics"           # 永不可自主

REASON = "触达集中在早晨，建议把生成时刻提前"


def proposal(config_id: str = AUTONOMOUS_OK, suggested: object = "08:00", **kw) -> ChangeProposal:
    return ChangeProposal(config_id=config_id, suggested=suggested, reason=REASON, **kw)


class Rig:
    """一次装配态（真 ``Store`` + 真门面 + 真总线；档位可控）。"""

    def __init__(self, tmp_path, *, tier: str = "autonomous", events: object = "auto") -> None:
        self.store = Store.create(tmp_path / "root", PASS)
        self.facade = ConfigRegistryFacade(self.store)
        self.bus = EventBus() if events == "auto" else events
        self.seen: list = []
        if self.bus is not None:
            self.bus.subscribe(CHANGE_APPLIED, self.seen.append, subscriber_id="t:applied")
            self.bus.subscribe(CHANGE_ROLLED_BACK, self.seen.append, subscriber_id="t:rolled")
        self.stack = build_l6(
            self.store, registry=self.facade, events=self.bus, now=lambda: NOW,
        )
        self.flow = self.stack.change_flow
        if tier is not None:
            self.stack.authorization.set_tier(tier)


class TestGwt1ApprovalGate:
    """GWT-1：门只回答「是否自动放行」；逐条结论与原因可查。"""

    def test_manual_does_not_file(self, tmp_path):
        rig = Rig(tmp_path, tier="manual")
        decision = rig.flow.gate(proposal())
        assert decision.filed is False and decision.auto is False
        assert "手动档" in decision.reason
        assert rig.flow.submit(proposal()).pending is None

    def test_collaborative_files_but_does_not_auto_apply(self, tmp_path):
        rig = Rig(tmp_path, tier="collaborative")
        decision = rig.flow.gate(proposal())
        assert (decision.filed, decision.auto) == (True, False)
        assert decision.tier == "collaborative"

    def test_autonomous_auto_releases_only_the_autonomous_class(self, tmp_path):
        rig = Rig(tmp_path, tier="autonomous")
        assert rig.flow.gate(proposal()).auto is True
        assert rig.flow.gate(proposal(NEEDS_APPROVAL, 3)).auto is False
        assert rig.flow.gate(proposal(RED_LINE, 7)).auto is False
        assert "红线" in rig.flow.gate(proposal(RED_LINE, 7)).reason

    def test_without_the_judge_it_is_fail_closed(self, tmp_path):
        store = Store.create(tmp_path / "root", PASS)
        built = build_l6(store, registry=ConfigRegistryFacade(store), now=lambda: NOW,
                         authorization=_NoJudge())
        with pytest.raises(ChangeFlowError):
            built.change_flow.gate(proposal())

    def test_absent_judge_reason_is_named(self):
        assert "授权判据" in NO_AUTHORIZATION_REASON


class TestGwt2ApplyThroughTheFacade:
    """GWT-2：生效经 01 §7 门面落值、产 `change_id`、记入变更历史（重启安全）。"""

    def test_accept_writes_through_the_facade(self, tmp_path):
        rig = Rig(tmp_path, tier="collaborative")
        run = rig.flow.submit(proposal())
        accepted = rig.flow.accept(run.pending.pending_id)
        assert accepted.applied is True
        assert rig.facade.entry(AUTONOMOUS_OK).default == "08:00"
        assert accepted.change.change_id.startswith("chg_")
        assert accepted.change.pending_id == run.pending.pending_id

    def test_history_is_reload_safe(self, tmp_path):
        rig = Rig(tmp_path)
        run = rig.flow.submit(proposal(), source="weekly-report")
        again = build_l6(rig.store, now=lambda: NOW)          # 换构造器续读留痕
        entries = again.change_flow.history()
        assert [c.change_id for c in entries] == [run.change.change_id]
        assert entries[0].source == "weekly-report"
        assert entries[0].reason == REASON

    def test_unknown_entry_is_refused_and_named(self, tmp_path):
        rig = Rig(tmp_path, tier="collaborative")
        run = rig.flow.submit(proposal("skill.demo.nope", 1))
        with pytest.raises(ChangeFlowError) as err:
            rig.flow.accept(run.pending.pending_id)
        assert "落值失败" in str(err.value)
        assert rig.flow.history() == ()

    def test_without_a_registry_apply_is_refused(self, tmp_path):
        built = build_l6(now=lambda: NOW)
        assert "门面" in NO_REGISTRY_REASON
        filed = built.change_flow.submit(proposal())
        assert filed.pending is not None                  # 立案不落值（还没到生效）
        with pytest.raises(ChangeFlowError) as err:
            built.change_flow.accept(filed.pending.pending_id)
        assert "门面" in str(err.value)

    def test_proposal_shape_is_shared_with_the_three_sources(self, tmp_path):
        rig = Rig(tmp_path, tier="collaborative")
        # 三条来源（周报第四段 / 训练对话 / 提案引擎）的候选共用同一形态
        run = rig.flow.submit(
            {"config_id": AUTONOMOUS_OK, "current": "20:00", "suggested": "08:00",
             "reason": REASON, "trace_ref": "tr_" + "5" * 20}
        )
        assert run.pending is not None


class TestGwt3Notification:
    """GWT-3：生效发 `ChangeApplied`、回滚发 `ChangeRolledBack`（必带 `change_id`）。"""

    def test_applied_event_carries_the_change_id(self, tmp_path):
        rig = Rig(tmp_path)
        run = rig.flow.submit(proposal())
        assert run.change.notified is True
        assert [e.change_id for e in rig.seen] == [run.change.change_id]
        assert rig.seen[0].event == CHANGE_APPLIED

    def test_rolled_back_event_carries_ids(self, tmp_path):
        rig = Rig(tmp_path)
        run = rig.flow.submit(proposal())
        rig.flow.rollback(run.change.change_id)
        rolled = rig.seen[-1]
        assert rolled.event == CHANGE_ROLLED_BACK
        assert rolled.payload["rolled_back_change_id"] == run.change.change_id
        assert rolled.change_id and rolled.change_id != run.change.change_id

    def test_without_a_bus_it_does_not_pretend_delivery(self, tmp_path):
        rig = Rig(tmp_path, events=None)
        run = rig.flow.submit(proposal())
        assert run.change.notified is False
        assert "未送达" in run.change.notify_note

    def test_a_failing_subscriber_does_not_block_the_others(self, tmp_path):
        store = Store.create(tmp_path / "root", PASS)
        bus = EventBus()
        seen: list = []

        def boom(_event):
            raise RuntimeError("subscriber exploded")

        bus.subscribe(CHANGE_APPLIED, boom, subscriber_id="t:boom")
        bus.subscribe(CHANGE_APPLIED, seen.append, subscriber_id="t:ok")
        stack = build_l6(store, registry=ConfigRegistryFacade(store), events=bus, now=lambda: NOW)
        stack.authorization.set_tier("autonomous")
        run = stack.change_flow.submit(proposal())
        assert len(seen) == 1                              # 抛错不阻断其余订阅者（01 §11）
        assert run.change.notified is True


class TestGwt4AcceptRejectDefer:
    """GWT-4：一键接受 / 否决 / 延后，三种处置都有落痕。"""

    def test_reject_keeps_the_value_untouched(self, tmp_path):
        rig = Rig(tmp_path, tier="collaborative")
        run = rig.flow.submit(proposal())
        before = rig.facade.entry(AUTONOMOUS_OK).default
        rejected = rig.flow.reject(run.pending.pending_id)
        assert rejected.status == "rejected"
        assert rig.facade.entry(AUTONOMOUS_OK).default == before
        assert rig.flow.history() == ()

    def test_defer_queues_instead_of_dropping(self, tmp_path):
        rig = Rig(tmp_path, tier="collaborative")
        run = rig.flow.submit(proposal())
        deferred = rig.flow.defer(run.pending.pending_id)
        assert deferred.status == "deferred" and deferred.deferrals == 1
        assert [p.pending_id for p in rig.flow.pending("deferred")] == [run.pending.pending_id]
        assert rig.flow.accept(run.pending.pending_id).applied is True     # 仍可再处置

    def test_double_disposal_is_refused(self, tmp_path):
        rig = Rig(tmp_path, tier="collaborative")
        run = rig.flow.submit(proposal())
        rig.flow.accept(run.pending.pending_id)
        with pytest.raises(ChangeFlowError):
            rig.flow.reject(run.pending.pending_id)

    def test_same_proposal_twice_is_the_same_pending(self, tmp_path):
        rig = Rig(tmp_path, tier="collaborative")
        first = rig.flow.submit(proposal())
        second = rig.flow.submit(proposal())
        assert first.pending.pending_id == second.pending.pending_id
        assert len(rig.flow.pending()) == 1


class TestGwt5Rollback:
    """GWT-5：按 `change_id` 一键回滚——回放旧值、原记录保留并标注。"""

    def test_rollback_restores_the_previous_value(self, tmp_path):
        rig = Rig(tmp_path, tier="collaborative")
        rig.facade.set(AUTONOMOUS_OK, "09:00")              # 先有「前一版本」
        run = rig.flow.submit(proposal())
        applied = rig.flow.accept(run.pending.pending_id)
        assert rig.facade.entry(AUTONOMOUS_OK).default == "08:00"
        back = rig.flow.rollback(applied.change.change_id)
        assert back.applied is True
        assert rig.facade.entry(AUTONOMOUS_OK).default == "09:00"
        kept = rig.flow.get(applied.change.change_id)
        assert kept.rolled_back_at is not None
        assert kept.rollback_change_id == back.change.change_id

    def test_first_ever_change_rolls_back_to_the_declared_value(self, tmp_path):
        rig = Rig(tmp_path)
        run = rig.flow.submit(proposal())
        assert run.change.old_value == "20:00"              # 未落过值 ⇒ 取声明面默认
        rig.flow.rollback(run.change.change_id)
        assert rig.facade.entry(AUTONOMOUS_OK).default == "20:00"

    def test_superseded_early_change_rolls_back_by_its_own_old_value(self, tmp_path):
        rig = Rig(tmp_path, tier="collaborative")
        rig.facade.set(AUTONOMOUS_OK, "09:00")
        first = rig.flow.accept(rig.flow.submit(proposal(suggested="08:00")).pending.pending_id)
        rig.flow.accept(rig.flow.submit(proposal(suggested="07:00")).pending.pending_id)
        rig.flow.rollback(first.change.change_id)
        assert rig.facade.entry(AUTONOMOUS_OK).default == "09:00"
        assert len(rig.flow.history()) == 3                  # 新旧记录都在

    def test_rolling_back_twice_is_refused(self, tmp_path):
        rig = Rig(tmp_path)
        run = rig.flow.submit(proposal())
        rig.flow.rollback(run.change.change_id)
        with pytest.raises(ChangeFlowError):
            rig.flow.rollback(run.change.change_id)

    def test_unknown_change_is_refused(self, tmp_path):
        with pytest.raises(ChangeFlowError):
            rig_flow(tmp_path).rollback("chg_" + "0" * 20)

    def test_cross_family_timeline_comes_from_the_facade(self, tmp_path):
        rig = Rig(tmp_path)
        run = rig.flow.submit(proposal())
        timeline = {c.change_id for c in rig.flow.changes()}
        assert run.change.change_id in timeline                 # 本层的演进变更在时间线上
        assert any(c.config_id.startswith("weekly-report.") for c in rig.flow.changes())


class TestGwt6NoSilentTuning:
    """GWT-6：本层**不存在**绕过变更流的静默调参路径（[08 §7](../../docs/技术架构-v2/08-L6-反思演进.md) 红线）。"""

    def test_change_flow_writes_only_through_the_facade(self):
        text = _l6_source("change.py")
        assert "self._registry.set(" in text
        assert "CONFIG_PARTITION" not in text
        assert "new_change_id" not in text            # `change_id` 只由门面铸造

    def test_only_the_entry_write_faces_touch_the_config_partition(self):
        writers = {
            path.name for path in _l6_sources()
            if "CONFIG_PARTITION" in path.read_text(encoding="utf-8")
        }
        assert writers == {"authorization_store.py", "proposal_store.py", "weekly_store.py"}

    def test_no_other_layer_writes_are_done_here(self):
        for path in _l6_sources():
            text = path.read_text(encoding="utf-8")
            assert "edit_node(" not in text, path.name
            assert "delete_node(" not in text, path.name


class _NoJudge:
    """授权面替身：判据不可用（`tier()` 抛错）——用于 fail-closed 用例。"""

    def tier(self):
        raise AuthorizationError("档位读不出")

    def classify(self, _config_id):
        return "collaborative-required"


def rig_flow(tmp_path):
    return Rig(tmp_path).flow


def _l6_sources() -> list[Path]:
    return sorted(
        (Path(__file__).resolve().parents[2] / "src" / "st_agent" / "l6").glob("*.py")
    )


def _l6_source(name: str) -> str:
    path = Path(__file__).resolve().parents[2] / "src" / "st_agent" / "l6" / name
    return path.read_text(encoding="utf-8")
