"""T-L1-001.2 测试：03 §1.2 执行流水线。

GWT 对照（任务文件 4 条）：
- GWT-2 匹配+确认卡：查询命中摘帽评估 + 参数确认卡数据
- GWT-3 参数即时生效：set_parameters 改值后下次执行用新值
- GWT-4 依赖失败显式：上游失败 → 下游 dependency_failed；成环/缺失拒绝
- GWT-6 推理链可查：SkillRun 落盘 + TraceStep 追加 + 回放可见依赖链
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.trace import Trace
from st_agent.l1.runner import RUN_PREFIX, RunnerValidationError, SkillRunner
from st_agent.l1.runner.errors import CycleDetectedError
from st_agent.l1.skills import (
    SkillNotFoundError,
    SkillRegistry,
    SkillValidationError,
    ensure_official_pack,
)
from st_agent.l0.storage import Store

PASS = "correct horse battery staple"

UNHAT = "sk_unhat_eligibility_check_v1.0"
WATCH = "sk_stock_watch_v1.0"


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def registry(store: Store) -> SkillRegistry:
    reg = SkillRegistry(store)
    ensure_official_pack(reg)
    return reg


@pytest.fixture()
def runner(store: Store, registry: SkillRegistry) -> SkillRunner:
    return SkillRunner(store, registry)


def approved_of(registry: SkillRegistry, skill_id: str) -> tuple[str, ...]:
    return registry.get(skill_id).permissions


def ok_executor(payload: object):
    def _fn(ctx, params):
        assert ctx.skill_id and ctx.skill_run_id and ctx.trace_id
        return ResultEnvelope.ok({"echo": payload, "params": params})
    return _fn


# ───────────────────────── GWT-2 匹配 + 确认卡 ─────────────────────────


class TestGwt2MatchAndCard:
    def test_match_unhat_query(self, runner):
        desc = runner.match("帮我做摘帽可能性分析")
        assert desc is not None
        assert desc.skill_id == UNHAT

    def test_match_empty_or_unknown(self, runner):
        assert runner.match("") is None
        assert runner.match("   ") is None

    def test_confirm_card_shape(self, runner):
        card = runner.confirm_card(UNHAT, {})
        assert card["skill_id"] == UNHAT
        names = [p["name"] for p in card["parameters"]]
        assert "fiscal_years" in names
        row = next(p for p in card["parameters"] if p["name"] == "fiscal_years")
        assert row["default"] == 2
        assert (row["min_value"], row["max_value"]) == (1, 5)

    def test_confirm_card_applies_given_values(self, runner):
        card = runner.confirm_card(UNHAT, {"fiscal_years": 3})
        row = next(p for p in card["parameters"] if p["name"] == "fiscal_years")
        assert row["default"] == 3

    def test_confirm_card_bad_value_rejected(self, runner):
        with pytest.raises(SkillValidationError):
            runner.confirm_card(UNHAT, {"fiscal_years": 99})

    def test_confirm_card_unknown_skill(self, runner):
        with pytest.raises(SkillNotFoundError):
            runner.confirm_card("sk_missing_thing_v1.0", {})


# ───────────────────────── GWT-3 参数即时生效 ─────────────────────────


class TestGwt3ParamEffective:
    def test_new_default_used_next_run(self, runner, registry):
        registry.set_parameters(WATCH, {"frequency_minutes": 15})
        seen: dict = {}

        def _fn(ctx, params):
            seen.update(params)
            return ResultEnvelope.ok({"ok": True})

        runner.register_executor(WATCH, _fn)
        out = runner.run(WATCH, {}, approved_permissions=approved_of(registry, WATCH))
        assert out.envelope.status == "ok"
        assert seen["frequency_minutes"] == 15

    def test_call_time_values_override(self, runner, registry):
        def _fn(ctx, params):
            return ResultEnvelope.ok({"freq": params["frequency_minutes"]})

        runner.register_executor(WATCH, _fn)
        out = runner.run(WATCH, {"frequency_minutes": 30},
                         approved_permissions=approved_of(registry, WATCH))
        assert out.envelope.status == "ok"
        raw = json.loads(
            runner._store.get("execution_log",
                              f"{RUN_PREFIX}{out.skill_run_id}.json").decode("utf-8"))
        assert raw["params"] == {"frequency_minutes": 30}

    def test_call_time_bad_value_rejected_not_executed(self, runner, registry):
        called: list = []
        runner.register_executor(WATCH, lambda ctx, p: called.append(1) or
                                 ResultEnvelope.ok({}))
        out = runner.run(WATCH, {"frequency_minutes": 9999},
                         approved_permissions=approved_of(registry, WATCH))
        assert out.envelope.status == "validation_failed"
        assert called == []


# ───────────────────────── GWT-4 依赖失败显式 ─────────────────────────


class TestGwt4DependencyFailed:
    def _downstream_pair(self, registry, runner, upstream_status: str):
        registry.register(base="sk_up_demo", name="上游演示",
                          description="上游演示 Skill",
                          permissions=("local_read:<data/cache/**>",))
        registry.register(base="sk_down_demo", name="下游演示",
                          description="下游演示 Skill",
                          dependencies=("sk_up_demo_v1.0",),
                          permissions=("local_read:<data/cache/**>",))

        def _up(ctx, params):
            if upstream_status == "ok":
                return ResultEnvelope.ok({"n": 1})
            return ResultEnvelope.failed("上游炸了", log_ref="skill-run/whatever.json")

        runner.register_executor("sk_up_demo_v1.0", _up)
        runner.register_executor("sk_down_demo_v1.0", ok_executor("down"))
        return "sk_up_demo_v1.0", "sk_down_demo_v1.0"

    def test_upstream_ok_runs_downstream(self, registry, runner):
        _, down = self._downstream_pair(registry, runner, "ok")
        out = runner.run(down, {},
                         approved_permissions=("local_read:<data/cache/**>",))
        assert out.envelope.status == "ok"
        types = [s.step_type for s in out.trace.steps]
        assert types == ["skill_run", "skill_run"]

    def test_upstream_failed_marks_dependency_failed(self, registry, runner):
        _, down = self._downstream_pair(registry, runner, "failed")
        out = runner.run(down, {},
                         approved_permissions=("local_read:<data/cache/**>",))
        assert out.envelope.status == "dependency_failed"
        assert "依赖失败" in (out.envelope.reason or "")
        # 下游执行器不得被调用：下游记录仍落盘但为 dependency_failed
        raw = json.loads(
            runner._store.get("execution_log",
                              f"{RUN_PREFIX}{out.skill_run_id}.json").decode("utf-8"))
        assert raw["envelope"]["status"] == "dependency_failed"
        assert raw["upstream"] == ["sk_up_demo_v1.0"]

    def test_missing_dependency_rejected(self, registry, runner):
        registry.register(base="sk_lonely_demo", name="孤儿演示",
                          description="依赖缺失的 Skill",
                          dependencies=("sk_ghost_missing_v1.0",),
                          permissions=("local_read:<data/cache/**>",))
        runner.register_executor("sk_lonely_demo_v1.0", ok_executor("x"))
        out = runner.run("sk_lonely_demo_v1.0", {},
                         approved_permissions=("local_read:<data/cache/**>",))
        assert out.envelope.status == "dependency_failed"

    def test_cycle_rejected(self, registry, runner):
        registry.register(base="sk_cyc_a", name="成环甲",
                          description="成环测试甲",
                          dependencies=("sk_cyc_b_v1.0",),
                          permissions=("local_read:<data/cache/**>",))
        registry.register(base="sk_cyc_b", name="成环乙",
                          description="成环测试乙",
                          dependencies=("sk_cyc_a_v1.0",),
                          permissions=("local_read:<data/cache/**>",))
        runner.register_executor("sk_cyc_a_v1.0", ok_executor("a"))
        runner.register_executor("sk_cyc_b_v1.0", ok_executor("b"))
        out = runner.run("sk_cyc_a_v1.0", {},
                         approved_permissions=("local_read:<data/cache/**>",))
        # 成环点内层为 validation_failed；入口按纪律透出 dependency_failed，原因带成环
        assert out.envelope.status == "dependency_failed"
        assert "成环" in (out.envelope.reason or "")


# ───────────────────────── 权限检查（A6） ─────────────────────────


class TestPermissionGate:
    def test_unapproved_blocked(self, runner, registry):
        runner.register_executor(UNHAT, ok_executor("x"))
        out = runner.run(UNHAT, {}, approved_permissions=())
        assert out.envelope.status == "validation_failed"
        assert "权限" in (out.envelope.reason or "")

    def test_approved_passes(self, runner, registry):
        runner.register_executor(UNHAT, ok_executor("x"))
        out = runner.run(UNHAT, {},
                         approved_permissions=approved_of(registry, UNHAT))
        assert out.envelope.status == "ok"

    def test_unregistered_skill_rejected(self, runner):
        out = runner.run("sk_missing_thing_v1.0", {}, approved_permissions=())
        assert out.envelope.status == "validation_failed"

    def test_bad_skill_id_shape_raises(self, runner):
        with pytest.raises(RunnerValidationError):
            runner.run("not-a-skill-id", {})


# ───────────────────────── GWT-6 推理链可查 ─────────────────────────


class TestGwt6Traceability:
    def test_skillrun_persisted_and_trace_step(self, runner, registry):
        runner.register_executor(UNHAT, ok_executor("unhat"))
        out = runner.run(UNHAT, {"fiscal_years": 2},
                         approved_permissions=approved_of(registry, UNHAT),
                         initiator="chat", purpose="摘帽评估")
        assert out.envelope.status == "ok"
        # SkillRun 落盘：输入快照 + 输出 + 耗时 + 依赖链
        raw = json.loads(
            runner._store.get("execution_log",
                              f"{RUN_PREFIX}{out.skill_run_id}.json").decode("utf-8"))
        assert raw["skill_id"] == UNHAT
        assert raw["params"] == {"fiscal_years": 2}
        assert raw["envelope"]["status"] == "ok"
        assert raw["duration_ms"] >= 0
        assert raw["upstream"] == []
        assert raw["trace_id"] == out.trace.trace_id.value
        # Trace 追加 skill_run 步并可回放
        assert len(out.trace.steps) == 1
        step = out.trace.steps[0]
        assert step.step_type == "skill_run"
        assert step.ref == out.skill_run_id
        replay = out.trace.replay()
        assert replay[0][0] == "skill_run" and replay[0][1] == out.skill_run_id

    def test_trace_continuity_with_caller_chain(self, runner, registry):
        runner.register_executor(WATCH, ok_executor("watch"))
        incoming = Trace(trace_id=__import__("st_agent.contracts.identifiers", fromlist=["TraceId"]).TraceId.generate())
        out = runner.run(WATCH, {}, trace=incoming,
                         approved_permissions=approved_of(registry, WATCH))
        assert out.trace.trace_id == incoming.trace_id
        assert len(out.trace.steps) == len(incoming.steps) + 1

    def test_failed_run_still_traced(self, runner, registry):
        def _boom(ctx, params):
            raise RuntimeError("执行器内部炸了")

        runner.register_executor(WATCH, _boom)
        out = runner.run(WATCH, {},
                         approved_permissions=approved_of(registry, WATCH))
        assert out.envelope.status == "failed"
        assert out.envelope.log_ref == f"{RUN_PREFIX}{out.skill_run_id}.json"
        assert len(out.trace.steps) == 1

    def test_no_executor_is_failed_not_crash(self, runner, registry):
        out = runner.run(WATCH, {},
                         approved_permissions=approved_of(registry, WATCH))
        assert out.envelope.status == "failed"
        assert "执行器" in (out.envelope.reason or "")

    def test_bad_return_type_is_failed(self, runner, registry):
        runner.register_executor(WATCH, lambda ctx, p: {"not": "envelope"})  # type: ignore[return-value]
        out = runner.run(WATCH, {},
                         approved_permissions=approved_of(registry, WATCH))
        assert out.envelope.status == "failed"


# ───────────────────────── A4 上下文注入（LLM/网关） ─────────────────────────


class TestContextInjection:
    def test_ctx_carries_llm_and_gateway(self, store, registry):
        sentinel_llm, sentinel_gw = object(), object()
        local = SkillRunner(store, registry, llm=sentinel_llm, gateway=sentinel_gw)
        seen: dict = {}

        def _fn(ctx, params):
            seen["llm"] = ctx.llm
            seen["gw"] = ctx.gateway
            seen["upstream"] = ctx.upstream
            return ResultEnvelope.ok({})

        local.register_executor(WATCH, _fn)
        out = local.run(WATCH, {}, approved_permissions=approved_of(registry, WATCH))
        assert out.envelope.status == "ok"
        assert seen["llm"] is sentinel_llm and seen["gw"] is sentinel_gw
        assert seen["upstream"] == {}

    def test_cycle_error_type_unit(self):
        assert issubclass(CycleDetectedError, Exception)
