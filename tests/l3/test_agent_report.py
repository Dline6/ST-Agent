"""T-AGT-004.3 测试：产出接入、留痕与失败语义（[05 §10](../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

GWT-1..7 逐条；另加三条守卫——终止原因的**穷尽映射**、描述件标签表与循环枚举的**覆盖一致**、
以及跨模块中性字段扫描（无 ``verdict`` 类字段）。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import get_args

import pytest

from st_agent.contracts.identifiers import TraceId
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.trace import Trace, TraceStep, digest_of
from st_agent.contracts.ui_description import (
    IMPLEMENTED_COMPONENT_TYPES,
    UiDescription,
)
from st_agent.l1.runner import RunOutcome
from st_agent.l3.errors import AgentRunNotFoundError
from st_agent.l3.render.describe import (
    AGENT_RUN_COLUMNS,
    AGENT_TERMINATION_LABELS,
    AGENT_RUN_TITLE,
    describe_agent_run,
)
from st_agent.l3.runtime import (
    AGENT_UNAVAILABLE_NOTICE,
    TerminationReason,
    TruncatedResult,
    TurnResult,
    LoopOutcome,
    agent_log_ref_of,
    build_agent_output,
    conclude_agent_run,
    load_agent_run,
    new_work_step,
    run_agent_loop,
)
from st_agent.l0.llm import ToolCall
from st_agent.l1.skills.tool_catalog import ToolEntry

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
RUN_1 = "run_00000000000000000001"
RUN_2 = "run_00000000000000000002"
AGENT_RUN = "agr_00000000000000000001"
INTERMEDIATE_TEXT = "我先看看行情，再决定要不要查公告"


# ───────────────────────── 夹具件 ─────────────────────────


def work_step(name: str = "sk_data_aggregate", *, run_id: str = RUN_1,
              status: str = "ok", data=None, truncated: bool = False) -> object:
    if status == "ok":
        if truncated:
            envelope = ResultEnvelope.ok(
                TruncatedResult(log_ref=f"skill-run/{run_id}.json", original_chars=9999,
                                preview="{"),
            )
        else:
            envelope = ResultEnvelope.ok(data if data is not None else {"cards": []})
    else:
        envelope = ResultEnvelope.failed("端点不可达", log_ref="skill-run/x.json")
    return new_work_step(
        tool_name=name, skill_id=f"{name}_v1.0", arguments={}, skill_run_id=run_id,
        envelope=envelope,
    )


def outcome(*, termination: str = "finished", steps=(), reason: str = "", answer: str = "",
            pending=None, gate_reason: str = "", llm_calls: int = 3, max_steps: int = 8,
            max_llm_calls: int = 12, failure=None) -> LoopOutcome:
    return LoopOutcome(
        task="查证 sh.600000 的公告与舆情", endpoint_id="ep_test",
        termination=termination, reason=reason, steps=tuple(steps), llm_calls=llm_calls,
        trace=Trace(trace_id=TraceId.generate()), answer=answer, pending=pending,
        gate_reason=gate_reason, max_steps=max_steps, max_llm_calls=max_llm_calls,
        failure=failure,
    )


def keys_of(payload) -> set[str]:
    """递归收集 JSON 里的全部键名（中性字段扫描用）。"""
    found: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            found.add(str(key).lower())
            found |= keys_of(value)
    elif isinstance(payload, list):
        for item in payload:
            found |= keys_of(item)
    return found


# ───────────────────────── GWT-1 · 产出是信封不是散文 ─────────────────────────


def test_gwt1_output_is_envelope_with_ui_description_payload(store):
    report = conclude_agent_run(store, outcome(steps=[work_step()], answer="证据已收集完毕"))
    envelope = report.envelope
    assert isinstance(envelope, ResultEnvelope) and envelope.status == "ok"
    assert isinstance(envelope.data, UiDescription)          # 载荷是描述件，不是散文
    assert envelope.data.component_type == "table"
    assert envelope.data.title == AGENT_RUN_TITLE


def test_gwt1_model_text_goes_to_a_slot_not_into_data_directly(store):
    """模型自由文本**不**直接当 ``data``：它是描述件里的一个 ``generated`` 槽。"""
    text = "先看行情数据再看公告，两者并列呈现"
    report = conclude_agent_run(store, outcome(steps=[work_step()], answer=text))
    description = report.envelope.data
    assert report.envelope.data is description                    # data 是描述本身
    assert description.slots["answer"] == text
    assert description.text_kinds["answer"] == "generated"
    assert description.text_kinds["rows"] == "data"               # 载荷与标识属数据


# ───────────────────────── GWT-2 · 走既有渲染路径 ─────────────────────────


def test_gwt2_component_type_is_registered_and_implemented(store):
    """组件型取自 01 §12 的**已实现**清单——故不存在「未登记即降级」的例外，也不新增型。"""
    report = conclude_agent_run(store, outcome(steps=[work_step()]))
    assert report.envelope.data.component_type in IMPLEMENTED_COMPONENT_TYPES


def test_gwt2_description_shape_matches_the_table_renderer(store):
    """``table`` 渲染件按 ``columns: [{key, label}]`` + ``rows`` 取值（渲染面是形态权威）。"""
    report = conclude_agent_run(store, outcome(steps=[work_step()]))
    slots = report.envelope.data.slots
    assert slots["columns"] == [dict(c) for c in AGENT_RUN_COLUMNS]
    assert set(slots["rows"][0]) == {c["key"] for c in AGENT_RUN_COLUMNS}
    assert set(report.envelope.data.text_kinds) == set(slots)     # 逐槽标注、不差一个


# ───────────────────────── GWT-3 · 留痕字段齐备 ─────────────────────────


def test_gwt3_record_has_steps_reason_and_bounds_and_is_auditable(store):
    report = conclude_agent_run(store, outcome(
        steps=[work_step(), work_step(run_id=RUN_2, status="failed")],
        termination="step_limit", reason="已达到步数上界（5），终止", max_steps=5, max_llm_calls=9,
        llm_calls=6,
    ))
    record = report.record
    assert record.termination == "step_limit"
    assert record.steps_count == 2
    assert (record.max_steps, record.max_llm_calls) == (5, 9)     # 上界记**实际生效值**
    assert record.at_bound is True and record.at_bound_kind == "step_limit"
    assert record.llm_calls == 6
    assert record.trace_id == report.record.trace_id
    assert agent_log_ref_of(report.agent_run_id) in store.list_files("execution_log")
    assert load_agent_run(store, report.agent_run_id) == record   # 审计读面取得到、且同值


def test_gwt3_normal_completion_record_is_complete_too(store):
    report = conclude_agent_run(store, outcome(steps=[work_step()]))
    record = report.record
    assert record.termination == "finished"
    assert record.at_bound is False and record.at_bound_kind == ""
    assert record.steps_count == 1
    assert load_agent_run(store, report.agent_run_id).termination == "finished"


def test_gwt3_missing_record_is_explicit_failure(store):
    with pytest.raises(AgentRunNotFoundError):
        load_agent_run(store, "agr_000000000000000000ff")


# ───────────────────────── GWT-4 · 中间推理不外显 ─────────────────────────


class _Protocol:
    """最小协议替身：**带工具调用的轮次里附一段自由文本**（真实提供方可能这么干）。"""

    def __init__(self, plan) -> None:
        self._plan = list(plan)

    def tool_calls_supported(self) -> bool:
        return True

    def invoke_turn(self, context, *, initiator: str, purpose: str) -> TurnResult:
        return self._plan.pop(0)


class _Runner:
    """最小执行面替身（同 ``SkillRunner.run`` 的鸭子端口）——只为打通「循环 → 收口」接线。"""

    def __init__(self) -> None:
        self._n = 0

    def run(self, skill_id, values=None, *, inputs=None, trace=None,
            approved_permissions=(), initiator="", purpose=""):
        self._n += 1
        run_id = f"run_{self._n:020x}"
        chain = (trace or Trace(trace_id=TraceId.generate())).append_step(TraceStep(
            step_type="skill_run", ref=run_id, input_digest=digest_of({"s": skill_id}),
            output_digest=digest_of({"ok": True}), duration_ms=1, timestamp=NOW,
        ))
        return RunOutcome(
            skill_id=skill_id, skill_run_id=run_id,
            envelope=ResultEnvelope.ok({"cards": []}), trace=chain,
        )


class _Gate:
    def authorize(self, **kwargs):
        return "autonomous-ok"


def _run_real_loop(store):
    tool = ToolEntry(name="sk_data_aggregate", skill_id="sk_data_aggregate_v1.0",
                     description="聚合数据", parameters={"type": "object"})
    protocol = _Protocol([
        TurnResult(text=INTERMEDIATE_TEXT, tool_calls=(
            ToolCall(call_id="c1", name="sk_data_aggregate", arguments={}),)),
        TurnResult(text="证据已收集完毕"),
    ])
    loop = run_agent_loop(
        task="查证 sh.600000 的公告与舆情", endpoint_id="ep_test",
        protocol=protocol, runner=_Runner(), tools=(tool,), gate=_Gate(),
    )
    assert loop.termination == "finished"
    return loop


def test_gwt4_intermediate_reasoning_never_leaves_the_loop(store):
    """带工具调用轮次里的文本**不落盘、不出站**（丢弃是结构性的，不靠事后过滤）。"""
    loop = _run_real_loop(store)
    report = conclude_agent_run(store, loop)
    assert INTERMEDIATE_TEXT not in report.envelope.data.model_dump_json()
    assert INTERMEDIATE_TEXT not in report.record.model_dump_json()
    assert report.envelope.data.slots["answer"] == "证据已收集完毕"    # 只有收尾文本出站


# ───────────────────────── GWT-5 · 不给统一建议 ─────────────────────────


def test_gwt5_no_verdict_like_fields_anywhere(store):
    """产出载荷与留痕里无 ``verdict`` / ``recommendation`` / ``advice`` 类字段（铁律 4）。"""
    report = conclude_agent_run(store, outcome(
        steps=[work_step(), work_step(run_id=RUN_2, status="failed")],
        answer="两条证据并列呈现",
    ))
    banned = {"verdict", "recommendation", "advice", "conclusion", "suggestion",
              "recommendations", "verdicts"}
    payload = json.loads(report.envelope.data.model_dump_json())
    record = json.loads(report.record.model_dump_json())
    assert not (keys_of(payload) & banned)
    assert not (keys_of(record) & banned)


def test_gwt5_steps_are_juxtaposed_not_merged(store):
    """证据**并列**呈现：每一步各占一行，不合并、不排序成一条结论。"""
    report = conclude_agent_run(store, outcome(
        steps=[work_step(), work_step(name="sk_sector_heatmap", run_id=RUN_2)],
    ))
    rows = report.envelope.data.slots["rows"]
    assert [row["tool_name"] for row in rows] == ["sk_data_aggregate", "sk_sector_heatmap"]
    assert [row["index"] for row in rows] == [1, 2]


# ───────────────────────── GWT-6 · 端点不支持即显式降级 ─────────────────────────


def test_gwt6_endpoint_unsupported_is_explicit_fail_closed(store):
    loop = outcome(
        termination="endpoint_unavailable",
        reason="端点 'ep_test' 未声明支持工具调用，自主查证循环无法运行",
    )
    report = conclude_agent_run(store, loop)
    envelope = report.envelope
    assert envelope.status == "unavailable"
    assert AGENT_UNAVAILABLE_NOTICE in envelope.reason
    assert envelope.last_updated_at is not None
    assert envelope.data is None                       # 不伪造一份「结果」
    assert "未运行自主查证" in envelope.reason
    assert report.record.termination == "endpoint_unavailable"


# ───────────────────────── GWT-7 · 单步失败如实呈现 ─────────────────────────


def test_gwt7_failed_step_is_presented_as_failed(store):
    loop = _run_real_loop(store)
    failed = new_work_step(
        tool_name="sk_data_aggregate", skill_id="sk_data_aggregate_v1.0", arguments={},
        skill_run_id=RUN_2,
        envelope=ResultEnvelope.failed("端点不可达", log_ref=f"skill-run/{RUN_2}.json"),
    )
    loop = loop.model_copy(update={"steps": (*loop.steps, failed)})
    report = conclude_agent_run(store, loop)

    rows = report.envelope.data.slots["rows"]
    assert [row["status"] for row in rows] == ["ok", "failed"]
    notes = report.envelope.data.slots["step_notes"]
    assert notes[-1]["index"] == 2 and "端点不可达" in notes[-1]["note"]
    assert [ref.ref for ref in report.envelope.evidence_refs] == [loop.steps[0].skill_run_id]
    assert report.record.steps[1].status == "failed"          # 留痕同样照实


# ───────────────────────── 终止原因：穷尽映射 ─────────────────────────


NO_STEP_STATUS = {
    "finished": "ok",
    "catalog_empty": "empty",
    "endpoint_unavailable": "unavailable",
    "gate_absent": "failed",
    "unknown_tool": "failed",
    "llm_failed": "failed",
    "needs_confirmation": "empty",
    "denied": "empty",
    "step_limit": "empty",
    "llm_call_limit": "empty",
}

WITH_STEP_STATUS = {
    "finished": "ok",
    "step_limit": "ok",
    "llm_call_limit": "ok",
    "needs_confirmation": "ok",
    "denied": "ok",
    "unknown_tool": "ok",
    "catalog_empty": "ok",
    "gate_absent": "ok",
    "endpoint_unavailable": "ok",
    "llm_failed": "ok",
}


def test_termination_literal_is_fully_mapped():
    """映射表穷尽 ``TerminationReason``——新增一个终止原因而不定产出即在此失败。"""
    reasons = set(get_args(TerminationReason))
    assert set(NO_STEP_STATUS) == reasons
    assert set(WITH_STEP_STATUS) == reasons
    assert set(AGENT_TERMINATION_LABELS) == reasons      # 描述件的标签表同样穷尽
    assert reasons == {
        "finished", "step_limit", "llm_call_limit", "gate_absent", "needs_confirmation",
        "denied", "endpoint_unavailable", "llm_failed", "unknown_tool", "catalog_empty",
    }


@pytest.mark.parametrize("termination,expected", sorted(NO_STEP_STATUS.items()))
def test_output_status_without_steps(store, termination, expected):
    loop = outcome(termination=termination, reason=f"{termination} 的原因")
    if termination == "llm_failed":
        loop = loop.model_copy(update={
            "failure": ResultEnvelope.failed("端点不可达", log_ref="llm/x.json"),
        })
        assert build_agent_output(loop, agent_run_id=AGENT_RUN).status == "failed"
        return
    envelope = build_agent_output(loop, agent_run_id=AGENT_RUN)
    assert envelope.status == expected, envelope.reason
    if expected == "unavailable":
        assert AGENT_UNAVAILABLE_NOTICE in envelope.reason


@pytest.mark.parametrize("termination,expected", sorted(WITH_STEP_STATUS.items()))
def test_output_status_with_steps(store, termination, expected):
    loop = outcome(termination=termination, reason=f"{termination} 的原因",
                   steps=[work_step()])
    envelope = build_agent_output(loop, agent_run_id=AGENT_RUN)
    assert envelope.status == expected, envelope.reason


def test_llm_failure_envelope_is_passed_through_verbatim(store):
    """端点侧的信封**原样透出**：不重包、不改 status、不吞 reason。"""
    failure = ResultEnvelope.validation_failed("工具条目非法：name 为空")
    loop = outcome(termination="llm_failed", reason="工具条目非法：name 为空", failure=failure)
    assert build_agent_output(loop, agent_run_id=AGENT_RUN) is failure


def test_unknown_termination_is_not_fabricated(store):
    class Odd:
        termination = "没有这一种"
        task = "t"
        steps = ()
        answer = ""
        pending = None
        reason = ""
        gate_reason = ""
        at_bound = False
        llm_calls = 0
        max_steps = 1
        max_llm_calls = 1

    assert describe_agent_run(Odd()).status == "validation_failed"


def test_finished_with_no_steps_still_yields_evidence_pack(store):
    """模型直接作答也是合法结果——不因「无步」而谎称空（与其余终止原因区别开来）。"""
    report = conclude_agent_run(store, outcome(answer="无需调用能力即可作答"))
    assert report.envelope.status == "ok"
    assert report.envelope.data.slots["rows"] == []
    assert report.envelope.evidence_refs == ()
