"""T-AGT-004.1 测试：工具目录消费与循环工作上下文装配（[05 §10](../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

GWT-1..6 逐条；另加三条边界用例（非 ok 结果不被截断 / 空目录与非法入参的显式失败 /
与 L1 投影面的交接形态）。
"""

from __future__ import annotations

import json

import pytest

from st_agent.contracts.capability_types import ParameterSpec, Provenance, SkillDescriptor
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l1.runner import RUN_PREFIX
from st_agent.l1.skills import project_catalog
from st_agent.l1.skills.tool_catalog import ToolEntry
from st_agent.l3.errors import AgentRuntimeValidationError
from st_agent.l3.runtime import (
    CATALOG_EMPTY_REASON,
    DEFAULT_WORK_CONTEXT_BUDGET,
    TRUNCATED_PREVIEW_CHARS,
    TruncatedResult,
    WorkStep,
    assemble_work_context,
    log_ref_of,
    new_work_step,
)

THRESHOLD = 120
RUN_ID_A = "run_0123456789abcdef0123"
RUN_ID_B = "run_0123456789abcdef0124"


def entry(name: str = "sk_probe_echo", skill_id: str | None = None,
          description: str = "回显输入文本") -> ToolEntry:
    return ToolEntry(
        name=name,
        skill_id=skill_id or f"{name}_v1.0",
        description=description,
        parameters={"type": "object", "properties": {"text": {"type": "string"}}},
    )


def ok_env(data) -> ResultEnvelope:
    return ResultEnvelope.ok(data)


def step(tool_name: str = "sk_probe_echo", *, run_id: str = RUN_ID_A,
         data=None, arguments: dict | None = None,
         envelope: ResultEnvelope | None = None) -> WorkStep:
    return new_work_step(
        tool_name=tool_name,
        skill_id=f"{tool_name}_v1.0",
        arguments=arguments if arguments is not None else {"text": "ping"},
        skill_run_id=run_id,
        envelope=envelope if envelope is not None else ok_env(data if data is not None else {"echo": "ping"}),
    )


def assemble(tools=None, steps=(), **kwargs):
    envelope = assemble_work_context(
        "查证 sh.600000 的公告与舆情", entry_list(tools), tuple(steps),
        max_result_chars=THRESHOLD, **kwargs,
    )
    assert envelope.status == "ok", envelope.reason
    return envelope.data


def entry_list(tools):
    """``None`` → 默认两条目；否则原样（供空目录用例传 ``[]``）。"""
    return [entry("sk_probe_echo"), entry("sk_probe_quote", description="取行情")] \
        if tools is None else tools


# ───────────────────────── GWT-1 · 目录进上下文且可追溯 ─────────────────────────


def test_gwt1_catalog_entries_kept_with_skill_id():
    """Given 一份工具目录，When 装配，Then 每个条目可回指其 ``skill_id``（不丢来源）。"""
    context = assemble()
    assert [e.name for e in context.tools] == ["sk_probe_echo", "sk_probe_quote"]
    assert [e.skill_id for e in context.tools] == [
        "sk_probe_echo_v1.0", "sk_probe_quote_v1.0",
    ]


def test_gwt1_model_visible_face_drops_skill_id():
    """模型可见面只取三字段——``skill_id`` 是执行目标，不占模型窗口。"""
    context = assemble()
    specs = context.tool_specs()
    assert [s.name for s in specs] == ["sk_probe_echo", "sk_probe_quote"]
    assert specs[0].parameters == context.tools[0].parameters
    assert not hasattr(specs[0], "skill_id")


# ───────────────────────── GWT-2 · 空目录显式化 ─────────────────────────


def test_gwt2_empty_catalog_is_explicit_empty_state():
    """Given 目录为空，When 装配，Then 显式空态（不装配出「可调用但无工具」的上下文）。"""
    envelope = assemble_work_context("任务", [])
    assert envelope.status == "empty"
    assert envelope.reason == CATALOG_EMPTY_REASON
    assert envelope.data is None


# ───────────────────────── GWT-3 · 大结果截断留指针 ─────────────────────────


def test_gwt3_large_result_truncated_with_real_pointer():
    big = {"text": "x" * (THRESHOLD * 3)}
    context = assemble(steps=[step(data=big)])
    got = context.steps[0]
    assert got.truncated is True
    assert isinstance(got.envelope.data, TruncatedResult)
    assert got.envelope.status == "ok"                      # 状态不改，只换载荷形态
    assert got.log_ref == log_ref_of(RUN_ID_A) == f"{RUN_PREFIX}{RUN_ID_A}.json"
    assert got.log_ref == got.envelope.data.log_ref          # 指针一致
    assert got.envelope.data.original_chars == len(json.dumps(big, ensure_ascii=False, sort_keys=True))
    assert got.envelope.data.preview == json.dumps(big, ensure_ascii=False, sort_keys=True)[:TRUNCATED_PREVIEW_CHARS]


def test_gwt3_pointer_form_matches_l1_execution_log_layout():
    """指针**口径**取自 L1 的单一真相源（``RUN_PREFIX``）——循环不另造一套路径。"""
    assert RUN_PREFIX == "skill-run/"
    assert log_ref_of("run_abc") == "skill-run/run_abc.json"


def test_gwt3_non_ok_result_not_truncated():
    """非 ok 态无载荷（01 §5）——截断只针对载荷，失败原因照实保留。"""
    failed = ResultEnvelope.failed("执行失败：" + "长" * 400, log_ref="skill-run/x.json")
    context = assemble(steps=[step(envelope=failed)])
    assert context.steps[0].truncated is False
    assert context.steps[0].envelope is failed or context.steps[0].envelope == failed


# ───────────────────────── GWT-4 · 小结果不截断 ─────────────────────────


def test_gwt4_small_result_byte_identical():
    envelope = ok_env({"echo": "ping"})
    context = assemble(steps=[step(envelope=envelope)])
    got = context.steps[0]
    assert got.truncated is False
    assert got.envelope.model_dump_json() == envelope.model_dump_json()   # 逐字节原样


# ───────────────────────── GWT-5 · 已执行动作按序保留 ─────────────────────────


def test_gwt5_steps_keep_order_and_carry_input_and_output():
    """每项可辨「当时依据什么、拿到什么」，且保持发生顺序。"""
    context = assemble(steps=[
        step("sk_probe_echo", run_id=RUN_ID_A, data={"echo": "a"}, arguments={"text": "a"}),
        step("sk_probe_quote", run_id=RUN_ID_B, data={"price": 1.0}, arguments={"code": "sh.600000"}),
    ])
    assert [s.tool_name for s in context.steps] == ["sk_probe_echo", "sk_probe_quote"]
    assert [s.skill_run_id for s in context.steps] == [RUN_ID_A, RUN_ID_B]
    assert context.steps[0].arguments == {"text": "a"}
    assert context.steps[1].envelope.data == {"price": 1.0}


def test_gwt5_failed_step_is_rendered_as_failure():
    """失败步照实呈现（不呈现为成功）。"""
    failed = ResultEnvelope.validation_failed("参数校验失败：text 超长")
    prompt = assemble(steps=[step(envelope=failed)]).render_prompt()
    assert "[validation_failed] 参数校验失败" in prompt


# ───────────────────────── GWT-6 · 纯函数 ─────────────────────────


def test_gwt6_same_input_byte_identical():
    """同一输入两次装配逐字节一致（无时间戳 / 随机序混入）。"""
    args = ([entry("sk_probe_echo")], [step(data={"echo": "ping"})])
    first = assemble_work_context("任务", args[0], args[1], max_result_chars=THRESHOLD)
    second = assemble_work_context("任务", args[0], args[1], max_result_chars=THRESHOLD)
    assert first.model_dump_json() == second.model_dump_json()


def test_gwt6_prompt_is_deterministic_and_carries_both_faces():
    context = assemble(steps=[step()])
    assert context.render_prompt() == context.render_prompt()
    with_tools = context.render_prompt(include_tools=True)
    assert "sk_probe_quote" in with_tools and "sk_probe_quote" not in context.render_prompt()
    assert "任务：查证 sh.600000 的公告与舆情" in with_tools


# ───────────────────────── 规模口径与超限 ─────────────────────────


def test_budget_size_matches_full_visible_face_and_flags_overrun():
    """规模口径＝模型可见形态（含目录）的字符数；超限**如实标注**、不压缩不丢弃。"""
    context = assemble(steps=[step()])
    assert context.size_chars == len(context.render_prompt(include_tools=True))
    assert context.budget_chars == DEFAULT_WORK_CONTEXT_BUDGET
    assert context.over_budget is False

    tight = assemble_work_context(
        "任务", [entry()], [step()], budget_chars=10, max_result_chars=THRESHOLD,
    )
    assert tight.data.over_budget is True
    assert len(tight.data.steps) == 1          # 超限不丢步（本期不做压缩）


# ───────────────────────── 非法入参：显式失败，不静默 ─────────────────────────


@pytest.mark.parametrize("task", ["", "   "])
def test_empty_task_rejected(task):
    with pytest.raises(AgentRuntimeValidationError, match="循环任务不得为空"):
        assemble_work_context(task, [entry()])


@pytest.mark.parametrize("budget", [0, -1, "4000"])
def test_bad_budget_rejected(budget):
    with pytest.raises(AgentRuntimeValidationError, match="规模预算须为"):
        assemble_work_context("任务", [entry()], budget_chars=budget)


def test_bad_entry_and_step_shapes_rejected():
    """坏条目 / 坏步骤不留半个上下文（半份目录比空目录更危险）。"""
    with pytest.raises(AgentRuntimeValidationError, match="工具条目须为 ToolEntry"):
        assemble_work_context("任务", [{"name": "sk_x"}])
    with pytest.raises(AgentRuntimeValidationError, match="执行步骤须为 WorkStep"):
        assemble_work_context("任务", [entry()], [{"tool_name": "sk_x"}])
    with pytest.raises(AgentRuntimeValidationError, match="skill_run_id"):
        new_work_step(tool_name="sk_x", skill_id="sk_x_v1.0", arguments={},
                      skill_run_id="", envelope=ResultEnvelope.ok({}))


# ───────────────────────── 与 L1 投影面的交接形态 ─────────────────────────


def test_consumes_l1_tool_catalog_projection():
    """交接面成立：L1 的 ``project_catalog`` 产出可直接进本装配件（跨任务交接点）。"""
    descriptors = [
        SkillDescriptor(
            skill_id="sk_probe_echo_v1.0", name="回显文本", description="回显输入文本",
            input_schema={"type": "object", "properties": {}}, output_schema={},
            parameters=(ParameterSpec(name="text", type="string", description="待回显文本"),),
            source="official", provenance=Provenance(), offline_level="full",
            version_policy="follow-latest",
        ),
        SkillDescriptor(
            skill_id="sk_probe_echo_v1.1", name="回显文本", description="回显输入文本",
            input_schema={"type": "object", "properties": {}}, output_schema={},
            parameters=(ParameterSpec(name="text", type="string", description="待回显文本"),),
            source="official", provenance=Provenance(), offline_level="full",
            version_policy="follow-latest",
        ),
    ]
    context = assemble(tools=list(project_catalog(descriptors)), steps=[step()])
    assert [e.name for e in context.tools] == ["sk_probe_echo"]
    assert context.tools[0].skill_id == "sk_probe_echo_v1.1"     # 执行目标取最高版本
    assert "text" in context.tool_specs()[0].parameters["properties"]
