"""A/B 实验（[`experiment.py`](../../src/st_agent/l6/experiment.py)）的验收用例。

对齐任务文件 [`T-L6-002.3`](../项目管理/tasks/T-L6-002.3-AB实验.md) 的 GWT-1..5——
启用门经注入的授权面（缺省不启用）、低风险范围闭集、五字段留痕与重启安全、
保守判定（不加强断言）、状态可分别查得且重复结算幂等。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from l6_helpers import PASS, NOW
from st_agent.l0.storage import Store
from st_agent.l6 import (
    AUTHORIZER_ABSENT_REASON,
    DEFAULT_MIN_SAMPLE,
    LOW_RISK_SCOPES,
    NOT_PERMITTED_REASON,
    Experiment,
    ExperimentConsole,
    ExperimentError,
    build_l6,
    judge,
)

HYPOTHESIS = "把单独触达合并进日报可降低打扰"
SCOPE = "delivery-strategy"
SAMPLE = {"size": 12, "window": "2026-W41"}
RESULT = {"size": 6, "preference": "new"}


def console(tmp_path, *, authorizer=None, store=None, **kw):
    """带真 ``Store`` 的实验面（授权面按需注入）。"""
    store = store if store is not None else Store.create(tmp_path / "root", PASS)
    return ExperimentConsole(store=store, authorizer=authorizer, now=lambda: NOW, **kw), store


def permits(*scopes: str) -> Any:
    return SimpleNamespace(permits=lambda scope: scope in scopes)


class TestGwt1AuthorizationGate:
    """GWT-1：经注入的授权面判定；缺省不启用、不落盘、不假装开跑。"""

    def test_permitted_experiment_starts_and_is_persisted(self, tmp_path):
        face, store = console(tmp_path, authorizer=permits(SCOPE))
        started = face.start(HYPOTHESIS, SCOPE, sample=SAMPLE)
        assert started.enabled is True
        assert started.experiment.status == "running"
        assert store.list_files("reflection") == (
            f"experiments/{started.experiment.experiment_id}.json",
        )

    def test_absent_authorizer_does_not_start(self, tmp_path):
        face, store = console(tmp_path)
        started = face.start(HYPOTHESIS, SCOPE)
        assert started.enabled is False
        assert started.reason == AUTHORIZER_ABSENT_REASON
        assert started.experiment is None
        assert store.list_files("reflection") == ()          # 不落盘、不假装开跑

    def test_denied_by_the_authorizer_does_not_start(self, tmp_path):
        face, store = console(tmp_path, authorizer=permits("lens-composition"))
        started = face.start(HYPOTHESIS, SCOPE)
        assert started.enabled is False
        assert started.reason == NOT_PERMITTED_REASON
        assert store.list_files("reflection") == ()

    def test_broken_authorizer_fails_explicitly(self, tmp_path):
        def boom(scope):
            raise RuntimeError("授权面故障")

        face, _ = console(tmp_path, authorizer=SimpleNamespace(permits=boom))
        with pytest.raises(ExperimentError, match="授权面不可用"):
            face.start(HYPOTHESIS, SCOPE)

    def test_build_l6_passes_the_authorizer_through(self, tmp_path):
        store = Store.create(tmp_path / "root", PASS)
        denied = build_l6(store, now=lambda: NOW)
        assert denied.experiments.start(HYPOTHESIS, SCOPE).enabled is False
        allowed = build_l6(store, authorizer=permits(SCOPE), now=lambda: NOW)
        assert allowed.experiments.start(HYPOTHESIS, SCOPE).enabled is True


class TestGwt2LowRiskScope:
    """GWT-2：范围是闭集——清单外的范围**显式拒**，不静默放行。"""

    def test_scope_list_is_the_declared_two(self):
        assert LOW_RISK_SCOPES == ("delivery-strategy", "lens-composition")

    def test_out_of_scope_is_refused_before_any_authorization(self, tmp_path):
        face, store = console(tmp_path, authorizer=permits("something-else"))
        with pytest.raises(ExperimentError, match="不在低风险清单内"):
            face.start(HYPOTHESIS, "skill-parameters")
        assert store.list_files("reflection") == ()

    def test_blank_hypothesis_is_refused(self, tmp_path):
        face, _ = console(tmp_path, authorizer=permits(SCOPE))
        with pytest.raises(ExperimentError, match="须给出假设"):
            face.start("   ", SCOPE)

    def test_generated_hypothesis_must_be_neutral(self, tmp_path):
        face, _ = console(tmp_path, authorizer=permits(SCOPE))
        with pytest.raises(ExperimentError, match="中性化"):
            face.start("我认为合并推送更好", SCOPE)

    def test_every_scope_in_the_list_is_accepted(self, tmp_path):
        face, _ = console(tmp_path, authorizer=permits(*LOW_RISK_SCOPES))
        for scope in LOW_RISK_SCOPES:
            assert face.start(HYPOTHESIS, scope).enabled is True


class TestGwt3RecordedFields:
    """GWT-3：五字段留痕、可读回、重启安全、空集不报错。"""

    def test_five_fields_are_readable(self, tmp_path):
        face, _ = console(tmp_path, authorizer=permits(SCOPE))
        started = face.start(HYPOTHESIS, SCOPE, sample=SAMPLE)
        settled = face.settle(started.experiment.experiment_id, result=RESULT)
        decided = face.decide(settled.experiment_id)
        assert set(decided.record) == {"hypothesis", "scope", "sample", "result", "decision"}
        assert decided.record["hypothesis"] == HYPOTHESIS
        assert decided.record["sample"] == SAMPLE
        assert decided.record["decision"] == "adopt"

    def test_read_face_survives_a_restart(self, tmp_path):
        face, store = console(tmp_path, authorizer=permits(SCOPE))
        started = face.start(HYPOTHESIS, SCOPE, sample=SAMPLE, trace_ref="tr_" + "9" * 20)
        reborn = ExperimentConsole(store=store, now=lambda: NOW)
        reloaded = reborn.get(started.experiment.experiment_id)
        assert isinstance(reloaded, Experiment)
        assert reloaded.trace_ref.startswith("tr_")
        assert [e.experiment_id for e in reborn.all()] == [started.experiment.experiment_id]

    def test_empty_read_face_is_an_empty_tuple(self, tmp_path):
        face, _ = console(tmp_path)
        assert face.all() == () and face.running() == ()
        assert face.get("exp_absent") is None

    def test_same_experiment_twice_is_the_same_record(self, tmp_path):
        face, store = console(tmp_path, authorizer=permits(SCOPE))
        first = face.start(HYPOTHESIS, SCOPE).experiment.experiment_id
        second = face.start(HYPOTHESIS, SCOPE).experiment.experiment_id
        assert first == second
        assert len(store.list_files("reflection")) == 1

    def test_pure_memory_mode_still_reads(self, tmp_path):
        face = ExperimentConsole(authorizer=permits(SCOPE), now=lambda: NOW)
        started = face.start(HYPOTHESIS, SCOPE)
        assert face.get(started.experiment.experiment_id) is not None


class TestGwt4ConservativeJudgement:
    """GWT-4：保守判定——样本不足 / 反馈未偏向 ⇒ `inconclusive`，不做显著性断言。"""

    def test_judge_below_the_sample_floor(self):
        decision, reason = judge({"size": DEFAULT_MIN_SAMPLE - 1, "preference": "new"})
        assert decision == "inconclusive" and "低于下限" in reason

    def test_judge_without_a_result(self):
        assert judge(None)[0] == "inconclusive"

    def test_judge_when_the_user_prefers_the_new_arm(self):
        assert judge({"size": DEFAULT_MIN_SAMPLE, "preference": "new"})[0] == "adopt"

    def test_judge_when_the_user_prefers_the_baseline(self):
        assert judge({"size": 99, "preference": "baseline"})[0] == "revert"

    def test_judge_without_a_clear_preference(self):
        assert judge({"size": 99, "preference": ""})[0] == "inconclusive"

    def test_judge_result_carries_no_significance_claims(self):
        """判据里**没有**统计断言——返回的只有保守决策与中性理由。"""
        decision, reason = judge({"size": 99, "preference": "new"})
        assert decision == "adopt"
        for banned in ("p 值", "置信", "显著", "p-value"):
            assert banned not in reason

    def test_decide_uses_the_conservative_verdict(self, tmp_path):
        face, _ = console(tmp_path, authorizer=permits(SCOPE))
        started = face.start(HYPOTHESIS, SCOPE)
        face.settle(started.experiment.experiment_id, result={"size": 1, "preference": "new"})
        decided = face.decide(started.experiment.experiment_id)
        assert decided.decision == "inconclusive"
        assert "低于下限" in decided.reason

    def test_decide_requires_a_settled_result(self, tmp_path):
        face, _ = console(tmp_path, authorizer=permits(SCOPE))
        started = face.start(HYPOTHESIS, SCOPE)
        with pytest.raises(ExperimentError, match="尚未结算"):
            face.decide(started.experiment.experiment_id)

    def test_explicit_decision_is_honoured(self, tmp_path):
        face, _ = console(tmp_path, authorizer=permits(SCOPE))
        started = face.start(HYPOTHESIS, SCOPE)
        face.settle(started.experiment.experiment_id, result=RESULT)
        decided = face.decide(started.experiment.experiment_id, decision="revert")
        assert decided.decision == "revert"

    def test_unknown_decision_is_refused(self, tmp_path):
        face, _ = console(tmp_path, authorizer=permits(SCOPE))
        started = face.start(HYPOTHESIS, SCOPE)
        face.settle(started.experiment.experiment_id, result=RESULT)
        with pytest.raises(ExperimentError, match="未知判定"):
            face.decide(started.experiment.experiment_id, decision="whatever")

    def test_unknown_experiment_is_refused(self, tmp_path):
        face, _ = console(tmp_path, authorizer=permits(SCOPE))
        with pytest.raises(ExperimentError, match="无此实验"):
            face.settle("exp_absent", result=RESULT)


class TestGwt5StatusesAndIdempotence:
    """GWT-5：三种状态可分别查得；重复结算幂等。"""

    def test_statuses_progress_and_are_queryable(self, tmp_path):
        face, _ = console(tmp_path, authorizer=permits(SCOPE))
        started = face.start(HYPOTHESIS, SCOPE)
        assert [e.status for e in face.running()] == ["running"]
        settled = face.settle(started.experiment.experiment_id, result=RESULT)
        assert settled.status == "settled" and face.running() == ()
        decided = face.decide(started.experiment.experiment_id)
        assert decided.status == "decided"
        assert face.get(decided.experiment_id).decision == "adopt"

    def test_repeated_settlement_with_the_same_result_is_idempotent(self, tmp_path):
        face, store = console(tmp_path, authorizer=permits(SCOPE))
        started = face.start(HYPOTHESIS, SCOPE)
        first = face.settle(started.experiment.experiment_id, result=RESULT)
        second = face.settle(started.experiment.experiment_id, result=dict(RESULT))
        assert second.settled_at == first.settled_at
        assert len(store.list_files("reflection")) == 1

    def test_a_decided_experiment_is_not_downgraded_by_a_re_settlement(self, tmp_path):
        face, _ = console(tmp_path, authorizer=permits(SCOPE))
        started = face.start(HYPOTHESIS, SCOPE)
        face.settle(started.experiment.experiment_id, result=RESULT)
        face.decide(started.experiment.experiment_id)
        again = face.settle(
            started.experiment.experiment_id, result={**RESULT, "size": 9},
        )
        assert again.status == "decided"
