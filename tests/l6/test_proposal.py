"""主动提案与周报候选生成（[`proposal.py`](../../src/st_agent/l6/proposal.py)）的验收用例。

对齐任务文件 [`T-L6-002.2`](../项目管理/tasks/T-L6-002.2-主动提案与周报候选生成.md) 的 GWT-1..5——
模式识别（含 Skill 草稿）与阈值可配、频率上限的排队不丢、周报第四段的规则表接线
（现值不可读即跳过、零 `change_id`）、`proposal.*` 族的门面落值。
"""

from __future__ import annotations

import json

import pytest

from l6_helpers import (
    FB_IGNORED,
    NEXT_WEEK_NOW,
    NOW,
    PASS,
    WEEK,
    WEEKLY_EVIDENCE,
    entry_reader,
    observation,
    observer,
)
from st_agent.l0.storage import Store
from st_agent.l1.registry.errors import RegistryValidationError
from st_agent.l1.registry.facade import ConfigRegistryFacade
from st_agent.l6 import (
    DEFAULT_RATE_LIMIT,
    DEFAULT_REPEAT_THRESHOLD,
    NO_ADVISOR_NOTE,
    OBSERVER_ABSENT_REASON,
    RATE_LIMIT_CONFIG_ID,
    REPEAT_CONFIG_ID,
    ProposalEngine,
    ProposalError,
    SkillProposal,
    build_l6,
    proposal_family,
    week_key,
)
from st_agent.l6.proposal import DEFAULT_RULES

IGNORE_THRESHOLD = "fatigue.ignore-threshold"
DEDUP_WINDOW = "frequency.dedup-window-minutes"


def engine(tmp_path, *, store=None, observations=(), registry=None, **kw):
    """带真 ``Store`` 的提案面（观察面 / 条目读面按需注入）。"""
    store = store if store is not None else Store.create(tmp_path / "root", PASS)
    face = ProposalEngine(
        store=store, observer=observer(*observations) if observations else None,
        registry=registry, now=lambda: NOW, **kw,
    )
    return face, store


class TestGwt1PatternDetection:
    """GWT-1：观察面给出的模式 ⇒ 产出含草稿的提案；未注入观察面 ⇒ 不产提案。"""

    def test_proposal_carries_a_skill_draft(self, tmp_path):
        face, store = engine(tmp_path, observations=(observation(count=6),))
        run = face.detect()
        assert run.enabled is True
        assert len(run.proposals) == 1
        proposal = run.proposals[0]
        assert isinstance(proposal, SkillProposal)
        assert proposal.count == 6
        assert proposal.draft.name == "估值复核流程"
        assert proposal.draft.nodes and proposal.draft.flow_name == "valuation_review"
        # 落痕在既有分区（不新造分区），路径由标识确定性派生
        assert store.list_files("reflection") == (f"proposals/{proposal.proposal_id}.json",)

    def test_observation_below_threshold_produces_nothing(self, tmp_path):
        face, store = engine(tmp_path, observations=(observation(count=5),))
        assert face.detect().proposals == ()
        assert store.list_files("reflection") == ()

    def test_absent_observer_yields_an_explicit_reason(self, tmp_path):
        face, store = engine(tmp_path)
        run = face.detect()
        assert run.enabled is False
        assert run.reason == OBSERVER_ABSENT_REASON
        assert run.proposals == ()
        assert store.list_files("reflection") == ()   # 不由「计数 0」反推「无模式」

    def test_observation_without_a_draft_is_refused(self, tmp_path):
        face, _ = engine(tmp_path, observations=(observation(draft=None),))
        with pytest.raises(ProposalError, match="未附 Skill 草稿"):
            face.detect()

    def test_illegal_observation_shape_is_refused(self, tmp_path):
        face, _ = engine(tmp_path, observations=("不是观察",))
        with pytest.raises(ProposalError, match="非法结构"):
            face.detect()

    def test_draft_name_must_pass_neutral_naming(self, tmp_path):
        bad = {**observation(count=6)["draft"], "name": "小李的复核流程"}
        face, _ = engine(tmp_path, observations=(observation(count=6, draft=bad),))
        with pytest.raises(ProposalError, match="中性化命名"):
            face.detect()

    def test_generated_reason_must_be_neutral(self, tmp_path):
        face, _ = engine(tmp_path, observations=(observation(count=6, reason="我认为该建一个流程"),))
        with pytest.raises(ProposalError, match="中性化"):
            face.detect()

    def test_sample_is_user_data(self, tmp_path):
        """样本原话带第一人称照常承载（数据展示不过 §6），且**随提案分字段留存**。"""
        face, _ = engine(tmp_path, observations=(
            observation(count=6, sample="我觉得这个标的是不是太贵了"),
        ))
        proposal = face.detect().proposals[0]
        assert proposal.key == "high-valuation"
        assert proposal.sample == "我觉得这个标的是不是太贵了"

    def test_same_observation_is_the_same_proposal(self, tmp_path):
        face, store = engine(tmp_path, observations=(observation(count=6),))
        first = face.detect().proposals[0].proposal_id
        second = face.detect().proposals[0].proposal_id
        assert first == second
        assert len(store.list_files("reflection")) == 1


class TestGwt2ConfigurableThreshold:
    """GWT-2：阈值走 01 §7 条目，改阈值即改判定。"""

    def test_default_threshold_is_five(self, tmp_path):
        face, _ = engine(tmp_path)
        assert face.repeat_threshold() == DEFAULT_REPEAT_THRESHOLD == 5
        assert face.rate_limit() == DEFAULT_RATE_LIMIT == 1

    def test_raising_the_threshold_changes_the_verdict(self, tmp_path):
        face, store = engine(tmp_path, observations=(observation(count=3),))
        assert face.detect().proposals == ()
        first = face.set_repeat_threshold(2)
        assert first is not None and first.config_id == REPEAT_CONFIG_ID
        assert first.new_value == 2
        assert face.detect().proposals != ()
        second = face.set_repeat_threshold(3)
        assert second.old_value == 2 and second.new_value == 3      # 旧值取自落盘条目
        assert {c.change_id for c in face.changes()} == {first.change_id, second.change_id}

    def test_unchanged_value_leaves_no_trace(self, tmp_path):
        """取值与**已落盘**的当前值相同时不落盘不留痕（同 [01 §7] 的逐条目落值口径）。"""
        face, store = engine(tmp_path)
        assert face.set_repeat_threshold(5) is not None      # 首次落盘（盘上尚无该条目）
        assert face.set_repeat_threshold(5) is None
        assert len(face.changes()) == 1

    def test_out_of_range_value_is_refused(self, tmp_path):
        face, store = engine(tmp_path)
        with pytest.raises(ProposalError, match="正整数"):
            face.set_repeat_threshold(0)
        with pytest.raises(ProposalError, match="非负整数"):
            face.set_rate_limit(-1)
        assert face.changes() == () and store.list_files("config") == ()

    def test_entries_are_registered_shapes(self, tmp_path):
        face, _ = engine(tmp_path)
        ids = {entry.config_id for entry in face.entries()}
        assert ids == {REPEAT_CONFIG_ID, RATE_LIMIT_CONFIG_ID}
        assert face.entry("weekly-report.time") is None        # 不属本族
        assert face.entry(REPEAT_CONFIG_ID).default == 5


class TestGwt3RateLimitQueue:
    """GWT-3：频率上限——超限**排队不丢**，跨窗口按余额放行。"""

    def test_over_quota_is_queued_not_dropped(self, tmp_path):
        face, _ = engine(tmp_path, observations=(
            observation(key="a", count=6), observation(key="b", count=7),
        ))
        run = face.detect()
        assert len(run.proposals) == 2
        assert sum(1 for p in face.all() if p.queued) == 1
        assert sum(1 for p in face.all() if p.released_week == WEEK) == 1

    def test_zero_quota_queues_everything(self, tmp_path):
        face, _ = engine(tmp_path, observations=(observation(count=6),))
        face.set_rate_limit(0)
        face.detect()
        assert all(p.queued for p in face.all())
        assert face.release() == ()

    def test_queue_is_released_when_the_window_rolls(self, tmp_path):
        face, _ = engine(tmp_path, observations=(
            observation(key="a", count=6), observation(key="b", count=7),
        ))
        face.detect()
        queued = [p for p in face.all() if p.queued]
        assert len(queued) == 1
        released = face.release(now=NEXT_WEEK_NOW)
        assert [p.proposal_id for p in released] == [queued[0].proposal_id]
        assert released[0].released_week == week_key(NEXT_WEEK_NOW.date())
        assert all(not p.queued for p in face.all())

    def test_released_proposals_are_not_re_released(self, tmp_path):
        face, _ = engine(tmp_path, observations=(observation(count=6),))
        face.detect()
        assert face.release(now=NEXT_WEEK_NOW) == ()   # 已放行的不再放行

    def test_queue_state_survives_a_restart(self, tmp_path):
        face, store = engine(tmp_path, observations=(observation(count=6),))
        face.detect()
        reborn = ProposalEngine(store=store, now=lambda: NEXT_WEEK_NOW)
        assert len(reborn.all()) == 1
        assert reborn.all()[0].released_week == WEEK


class TestGwt4WeeklyCandidates:
    """GWT-4：规则表接上 `advisor`；现值不可读即跳过；零 `change_id`。"""

    def test_default_rules_are_the_declared_two(self):
        assert [rule.rule_id for rule in DEFAULT_RULES] == ["ignored-majority", "delivery-density"]

    def test_hit_rule_yields_a_candidate_with_a_trace_anchor(self, tmp_path):
        face, _ = engine(tmp_path, registry=entry_reader(**{IGNORE_THRESHOLD: 5}))
        run = face.evaluate(WEEKLY_EVIDENCE)
        assert [c.config_id for c in run.candidates] == [IGNORE_THRESHOLD]
        candidate = run.candidates[0]
        assert candidate.current == 5 and candidate.suggested == 4
        assert candidate.trace_ref == FB_IGNORED              # 真实留痕锚点，不臆造
        assert [n.note for n in run.notes if n.hit] == ["命中"]

    def test_unreadable_current_value_skips_the_rule(self, tmp_path):
        face, _ = engine(tmp_path)                            # 未注入条目读面
        run = face.evaluate(WEEKLY_EVIDENCE)
        assert run.candidates == ()
        assert any("现值不可读" in note.note for note in run.notes if note.hit)
        assert face.last_run() is run

    def test_floor_prevents_a_no_op_suggestion(self, tmp_path):
        face, _ = engine(tmp_path, registry=entry_reader(**{IGNORE_THRESHOLD: 1}))
        run = face.evaluate(WEEKLY_EVIDENCE)
        assert run.candidates == ()
        assert any("已达下界" in note.note for note in run.notes)

    def test_majority_rule_requires_a_majority(self, tmp_path):
        face, _ = engine(tmp_path, registry=entry_reader(**{IGNORE_THRESHOLD: 5}))
        thin = {**WEEKLY_EVIDENCE, "ignored": 3, "feedback_total": 20}
        assert face.evaluate(thin).candidates == ()

    def test_delivery_density_rule_targets_the_dedup_window(self, tmp_path):
        face, _ = engine(tmp_path, registry=entry_reader(**{
            IGNORE_THRESHOLD: 5, DEDUP_WINDOW: 30,
        }))
        dense = {"week_deliveries": 25, "feedback_total": 1, "ignored": 0,
                 "feedback_ids": (), "delivery_ids": ("dlv_1",)}
        assert [c.config_id for c in face.evaluate(dense).candidates] == [DEDUP_WINDOW]

    def test_non_numeric_current_value_is_skipped(self, tmp_path):
        face, _ = engine(tmp_path, registry=entry_reader(**{IGNORE_THRESHOLD: "high"}))
        assert face.evaluate(WEEKLY_EVIDENCE).candidates == ()

    def test_weekly_report_gets_the_candidates_and_no_change_id(self, tmp_path):
        face, _ = engine(tmp_path, registry=entry_reader(**{IGNORE_THRESHOLD: 5}))
        assert face.proposals(WEEKLY_EVIDENCE) == face.evaluate(WEEKLY_EVIDENCE).candidates
        blob = json.dumps(
            [c.model_dump(mode="json") for c in face.proposals(WEEKLY_EVIDENCE)],
            ensure_ascii=False,
        )
        assert blob and "change_id" not in blob and "chg_" not in blob

    def test_build_l6_wires_the_rules_into_the_weekly_report(self, tmp_path):
        store = Store.create(tmp_path / "root", PASS)
        built = build_l6(
            store, registry=ConfigRegistryFacade(store),
            observer=observer(observation(count=6)), now=lambda: NOW,
        )
        section = next(
            s for s in built.reports.build(WEEK, now=NOW).sections
            if s.name == "adjustment_proposals"
        )
        assert NO_ADVISOR_NOTE not in section.empty_note      # 建议面**已接上**（规则表）
        assert built.proposals.detect().proposals != ()


class TestGwt5RegistryFamily:
    """GWT-5：`proposal.*` 族注入 01 §7 门面——读面全接、标量可写并产 `change_id`。"""

    def test_family_is_registered_and_readable(self, tmp_path):
        store = Store.create(tmp_path / "root", PASS)
        facade = ConfigRegistryFacade(store)
        built = build_l6(store, registry=facade, now=lambda: NOW)
        listed = {e.config_id for e in facade.list(scope="global")}
        assert {REPEAT_CONFIG_ID, RATE_LIMIT_CONFIG_ID} <= listed
        assert facade.entry(REPEAT_CONFIG_ID).default == 5

    def test_scalar_apply_writes_and_records(self, tmp_path):
        store = Store.create(tmp_path / "root", PASS)
        facade = ConfigRegistryFacade(store)
        built = build_l6(store, registry=facade, now=lambda: NOW)
        change = proposal_family(built.proposals).apply(
            RATE_LIMIT_CONFIG_ID, 3, trace_id="tr_" + "9" * 20,
        )
        assert change is not None and change.new_value == 3
        assert built.proposals.rate_limit() == 3
        assert [c.change_id for c in built.proposals.changes()] == [change.change_id]

    def test_unknown_entry_is_refused(self, tmp_path):
        face, _ = engine(tmp_path)
        with pytest.raises(RegistryValidationError, match="不是本族已知条目"):
            proposal_family(face).apply(REPEAT_CONFIG_ID + "-typo", 3)

    def test_foreign_family_is_refused(self, tmp_path):
        face, _ = engine(tmp_path)
        with pytest.raises(RegistryValidationError, match="不属本族"):
            proposal_family(face).apply("weekly-report.time", "08:00")

    def test_out_of_range_apply_is_reported_in_registry_vocabulary(self, tmp_path):
        face, _ = engine(tmp_path)
        with pytest.raises(RegistryValidationError):
            proposal_family(face).apply(RATE_LIMIT_CONFIG_ID, -2)
