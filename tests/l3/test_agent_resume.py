"""T-AGT-011.3 测试：待确认与待输入步骤的一键续跑（[05 §10](../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

GWT 对照：
- GWT-1：``needs_confirmation`` 终止后用户确认 → **从该步继续**而非重跑全程（前序证据不重放）
- GWT-2：``needs_user_input`` 终止后用户作答 → 带答案续跑
- GWT-3：续跑依据已过期 → 显式提示并允许重跑

另钉：续跑产新 ``agent_run_id`` / 新链、以 ``resumed_from`` 指回旧留痕；放弃与不匹配的选择
都回显式信封；前序步骤由 ``skill_run`` 记录重建、不计入新循环上界。
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.llm import ToolCall
from st_agent.l0.storage import Store
from st_agent.l1.builtin import install_builtins
from st_agent.l1.builtin.ask_user import ASK_USER
from st_agent.l1.builtin.plan_update import PLAN_UPDATE
from st_agent.l1.runner import SkillRunner
from st_agent.l1.skills import SkillRegistry, ensure_official_pack
from st_agent.l3.runtime import (
    ASK_USER_TOOL,
    CONTINUATION_TTL,
    GateDecision,
    TurnResult,
    conclude_agent_run,
    load_continuation,
    resume_agent_run,
    run_agent_loop,
)
from l3_helpers import NOW

EP = "ep_resume"
TASK = "查证 sh.600000 的公告与舆情"
PASS = "resume-test-passphrase"

SK_A = "sk_data_aggregate"
SK_B = "sk_sector_heatmap"


@pytest.fixture()
def rig(store: Store):
    registry = SkillRegistry(store)
    ensure_official_pack(registry)
    runner = SkillRunner(store, registry)
    install_builtins(registry, runner, primitives=(PLAN_UPDATE, ASK_USER))
    runner.register_executor(f"{SK_A}_v1.0", lambda ctx, params: ResultEnvelope.ok({"cards": []}))
    runner.register_executor(
        f"{SK_B}_v1.0", lambda ctx, params: ResultEnvelope.ok({"top_n": params.get("top_n")}),
    )
    return store, runner, registry.tool_catalog()


class ScriptedProtocol:
    def __init__(self, replies) -> None:
        self._replies = list(replies)

    def tool_calls_supported(self) -> bool:
        return True

    def invoke_turn(self, context, *, initiator: str, purpose: str) -> TurnResult:
        item = self._replies.pop(0) if self._replies else TurnResult(text="（脚本已尽）")
        return item(context) if callable(item) else item


class SelectiveGate:
    """放行列出的工具；其余判「须确认」（用于构造 ``needs_confirmation``）。"""

    def __init__(self, allowed=()) -> None:
        self._allowed = set(allowed)

    def authorize(self, *, skill_id: str, tool_name: str, arguments: dict) -> GateDecision:
        verdict = "autonomous-ok" if tool_name in self._allowed else "needs-confirmation"
        return GateDecision(verdict=verdict, reason="不在自主清单")


def call(name: str, arguments: dict | None = None, *, call_id: str = "c") -> TurnResult:
    return TurnResult(tool_calls=(
        ToolCall(call_id=call_id, name=name, arguments=arguments or {}),
    ))


def finish(text: str = "证据已收集完毕") -> TurnResult:
    return TurnResult(text=text)


def _initial_run(rig, protocol, gate):
    _store, runner, tools = rig
    return run_agent_loop(
        task=TASK, endpoint_id=EP, protocol=protocol, runner=runner, tools=tools,
        gate=gate, history=(), memory=(),
    )


def _skill_runs(store: Store) -> list[str]:
    return [name for name in store.list_files("execution_log")
            if name.startswith("skill-run/")]


def _resume(rig, record, protocol, *, decision, **kwargs):
    store, runner, tools = rig
    return resume_agent_run(
        store, record.agent_run_id,
        decision=decision,
        endpoint_id=EP, protocol=protocol, runner=runner, tools=tools,
        gate=kwargs.pop("gate", SelectiveGate({SK_A, SK_B})),
        **kwargs,
    )


def _blocked_record(rig):
    """跑一次「第一步成功、第二步被拦」的循环 → 留痕里的待确认步是 SK_B。"""
    _store, _runner, _tools = rig
    outcome = _initial_run(
        rig,
        ScriptedProtocol([
            call(SK_A, {}, call_id="c1"), call(SK_B, {"top_n": 5}, call_id="c2"),
        ]),
        SelectiveGate({SK_A}),
    )
    assert outcome.termination == "needs_confirmation"
    report = conclude_agent_run(rig[0], outcome, now=NOW)
    return report.record


def _asked_record(rig):
    outcome = _initial_run(
        rig,
        ScriptedProtocol([TurnResult(tool_calls=(
            ToolCall(call_id="c_ask", name=ASK_USER_TOOL, arguments={
                "questions": [{"parameter": "window", "prompt": "统计窗口（天）"}],
            }),
        ))]),
        SelectiveGate({ASK_USER_TOOL}),
    )
    assert outcome.termination == "needs_user_input"
    report = conclude_agent_run(rig[0], outcome, now=NOW)
    return report.record


# ───────────────────── GWT-1 · 确认后从该步继续 ─────────────────────


def test_gwt1_approve_executes_the_pending_step_and_continues(rig):
    store, _runner, _tools = rig
    record = _blocked_record(rig)
    assert record.pending is not None and record.pending.tool_name == SK_B

    result = _resume(rig, record, ScriptedProtocol([finish()]), decision="approve", now=NOW)

    assert result.envelope.status == "ok"
    assert result.report is not None
    resumed = result.report.record
    assert resumed.termination == "finished"
    assert [step.tool_name for step in resumed.steps] == [SK_A, SK_B]
    assert resumed.resumed_from == record.agent_run_id
    assert resumed.trace_id != record.trace_id          # 续跑是新链
    assert len(_skill_runs(store)) == 2                  # 前序 SK_A 未重跑


def test_gwt1_prior_steps_do_not_consume_the_new_step_budget(rig):
    """前序 1 步 + 上界 1 ⇒ 续跑仍能执行新的确认步（前序不计入新循环上界）。"""
    record = _blocked_record(rig)
    result = _resume(
        rig, record, ScriptedProtocol([finish()]), decision="approve",
        now=NOW, max_steps=1,
    )
    assert result.report is not None
    assert result.report.record.termination == "finished"


def test_gwt1_approve_rejects_a_pending_tool_no_longer_in_catalog(rig):
    record = _blocked_record(rig)
    store, runner, _tools = rig
    result = resume_agent_run(
        store, record.agent_run_id, decision="approve", endpoint_id=EP,
        protocol=ScriptedProtocol([finish()]), runner=runner,
        tools=tuple(entry for entry in _tools if entry.name != SK_B),
        gate=SelectiveGate({SK_A, SK_B}), now=NOW,
    )
    assert result.envelope.status == "validation_failed"
    assert "不在当前工具目录" in result.envelope.reason


# ───────────────────── GWT-2 · 带答案续跑 ─────────────────────


def test_gwt2_answer_is_carried_into_the_next_work_context(rig):
    record = _asked_record(rig)
    observed: dict = {}

    def after_answer(context):
        observed["user_inputs"] = dict(context.user_inputs)
        observed["prompt"] = context.render_prompt()
        return finish()

    result = _resume(
        rig, record, ScriptedProtocol([after_answer]), decision="answer",
        answers={"window": 30}, now=NOW,
    )

    assert result.report is not None
    assert observed["user_inputs"] == {"window": 30}
    assert "用户补充（续跑" in observed["prompt"]
    assert result.report.record.resumed_from == record.agent_run_id


# ───────────────────── GWT-3 · 依据过期与重跑 ─────────────────────


def test_gwt3_expired_basis_is_explained_not_replayed(rig):
    record = _blocked_record(rig)
    later = NOW + CONTINUATION_TTL + timedelta(seconds=1)
    result = _resume(rig, record, ScriptedProtocol([finish()]), decision="approve", now=later)

    assert result.stale is True
    assert result.report is None                       # 没有沿用旧证据
    assert result.envelope.status == "empty"
    assert "已过期" in result.envelope.reason
    assert "重跑" in result.envelope.reason


def test_gwt3_rerun_starts_fresh_from_the_task(rig):
    store, _runner, _tools = rig
    record = _blocked_record(rig)
    later = NOW + CONTINUATION_TTL + timedelta(seconds=1)

    result = _resume(rig, record, ScriptedProtocol([finish()]), decision="rerun", now=later)

    assert result.stale is True
    assert result.report is not None
    assert result.report.record.steps == ()            # 不带前序
    assert result.report.record.resumed_from == record.agent_run_id
    assert result.envelope.status == "ok"


# ───────────────────── 拒绝面 ─────────────────────


def test_cancel_returns_explicit_empty_and_runs_nothing(rig):
    store, _runner, _tools = rig
    record = _blocked_record(rig)
    before = len([n for n in store.list_files("execution_log")
                  if n.startswith("agent-run/")])
    result = _resume(rig, record, ScriptedProtocol([finish()]), decision="cancel", now=NOW)
    after = len([n for n in store.list_files("execution_log")
                 if n.startswith("agent-run/")])
    assert result.report is None and after == before
    assert result.envelope.status == "empty"
    assert "已放弃续跑" in result.envelope.reason


def test_mismatched_decision_is_rejected(rig):
    record = _asked_record(rig)
    result = _resume(rig, record, ScriptedProtocol([finish()]), decision="approve", now=NOW)
    assert result.envelope.status == "validation_failed"
    assert "待确认" in result.envelope.reason


def test_missing_record_is_rejected_with_envelope(rig):
    store, runner, tools = rig
    result = resume_agent_run(
        store, "agr_000000000000000000ff", decision="approve", endpoint_id=EP,
        protocol=ScriptedProtocol([finish()]), runner=runner, tools=tools,
        gate=SelectiveGate({SK_A}), now=NOW,
    )
    assert result.envelope.status == "validation_failed"


def test_load_continuation_rejects_a_finished_run(rig):
    outcome = _initial_run(rig, ScriptedProtocol([finish()]), SelectiveGate({SK_A}))
    record = conclude_agent_run(rig[0], outcome, now=NOW).record
    with pytest.raises(Exception) as excinfo:
        load_continuation(rig[0], record.agent_run_id)
    assert "不是可续跑状态" in str(excinfo.value)
