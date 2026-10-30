"""T-AGT-017.1 测试：工具检索与延迟加载（[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)）。

GWT 对照：
- GWT-1：目录超阈值 → 装配只给摘要索引与检索入口。
- GWT-2：检索命中 → 匹配条目在后续轮次展开（完整描述与参数）。
- GWT-3：目录未超阈值 → 输出与未引入检索时**逐字节一致**。
"""

from __future__ import annotations

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.llm import ToolCall, ToolSpec
from st_agent.l1.runner import SkillRunner
from st_agent.l1.skills import SkillRegistry, ensure_official_pack
from st_agent.l1.skills.tool_catalog import ToolEntry
from st_agent.l3.runtime import GateDecision, TurnResult, run_agent_loop
from st_agent.l3.runtime.context import assemble_work_context
from st_agent.l3.runtime.tool_search import (
    DEFAULT_TOOL_SEARCH_LIMIT,
    MAX_TOOL_SEARCH_LIMIT,
    SUMMARY_CHARS,
    TOOL_SEARCH_TOOL,
    normalize_limit,
    normalize_query,
    search_catalog,
)


def _entry(name: str, description: str, parameters: dict | None = None) -> ToolEntry:
    return ToolEntry(
        name=name,
        skill_id=f"{name}_v1.0",
        description=description,
        parameters=parameters or {},
    )


TOOLS = (
    _entry("sk_alpha", "读取 Alpha 数据"),
    _entry("sk_beta", "写入 Beta 数据"),
)


# ───────────────────────── GWT-1 · 超阈值只给摘要与入口 ─────────────────────────


def test_gwt1_over_threshold_exposes_summary_and_search_entry():
    context = assemble_work_context("任务", TOOLS, tool_lazy_chars=1).data
    assert context.lazy_tools is True
    assert context.retrieved_tools == ()
    assert [spec.name for spec in context.tool_specs()] == [TOOL_SEARCH_TOOL]
    prompt = context.render_prompt(include_tools=True)
    assert "目录超过阈值" in prompt
    assert "- sk_alpha：读取 Alpha 数据" in prompt
    assert f"- {TOOL_SEARCH_TOOL}：" in prompt
    # 未展开的条目**不给**完整参数（摘要只到描述头部）
    assert "- sk_alpha：读取 Alpha 数据\n  参数：" not in prompt


def test_gwt1_summary_truncates_long_description():
    long_description = "很长" * (SUMMARY_CHARS * 2)
    context = assemble_work_context(
        "任务", (_entry("sk_long", long_description),), tool_lazy_chars=1
    ).data
    prompt = context.render_prompt(include_tools=True)
    assert "…" in prompt
    assert long_description not in prompt


def test_gwt1_threshold_defaults_to_the_context_budget():
    """阈值缺省＝本次预算：预算压到 1 时目录必然超阈值。"""
    context = assemble_work_context("任务", TOOLS, budget_chars=1).data
    assert context.lazy_tools is True


# ───────────────────────── GWT-2 · 检索后按需展开 ─────────────────────────


def test_gwt2_retrieval_expands_matched_entries():
    context = assemble_work_context(
        "任务", TOOLS, tool_lazy_chars=1, retrieved_tools=("sk_beta",)
    ).data
    assert [spec.name for spec in context.tool_specs()] == [TOOL_SEARCH_TOOL, "sk_beta"]
    prompt = context.render_prompt(include_tools=True)
    assert "已展开的工具（1 个）" in prompt
    assert "- sk_beta：写入 Beta 数据\n  参数：{}" in prompt
    assert "已展开的工具：无" not in prompt


def test_gwt2_native_channel_does_not_duplicate_expanded_parameters():
    context = assemble_work_context(
        "任务", TOOLS, tool_lazy_chars=1, retrieved_tools=("sk_beta",)
    ).data
    prompt = context.render_prompt(include_tools=False)
    assert "已展开的工具：sk_beta（完整参数经工具调用通道下发）" in prompt
    assert prompt.count("  参数：") == 1          # 只剩检索入口自己的参数


def test_gwt2_unknown_retrieved_names_are_ignored():
    context = assemble_work_context(
        "任务", TOOLS, tool_lazy_chars=1, retrieved_tools=("sk_gone", "sk_alpha")
    ).data
    assert context.retrieved_tools == ("sk_alpha",)


# ───────────────────────── GWT-3 · 未超阈值逐字节不变 ─────────────────────────


def test_gwt3_below_threshold_is_byte_identical():
    context = assemble_work_context("任务", TOOLS).data
    assert context.lazy_tools is False
    assert context.tool_specs() == (
        ToolSpec(name="sk_alpha", description="读取 Alpha 数据", parameters={}),
        ToolSpec(name="sk_beta", description="写入 Beta 数据", parameters={}),
    )
    assert context.render_prompt(include_tools=True) == (
        "任务：任务\n"
        "\n"
        "当前计划：无（尚未给出计划）\n"
        "\n"
        "会话历史：无（本会话无更早消息）\n"
        "\n"
        "可用工具（2 个）：\n"
        "- sk_alpha：读取 Alpha 数据\n"
        "  参数：{}\n"
        "- sk_beta：写入 Beta 数据\n"
        "  参数：{}\n"
        "\n"
        "已执行动作与结果：无（尚未执行任何动作）\n"
        "\n"
        "相关记忆：无（无相关记忆切片）"
    )


def test_gwt3_no_search_entry_appears_below_threshold():
    context = assemble_work_context("任务", TOOLS).data
    assert TOOL_SEARCH_TOOL not in context.render_prompt(include_tools=True)


# ───────────────────────── 检索语义（确定性 / 排序 / 边界） ─────────────────────────


def test_search_ranks_name_hits_before_description_hits():
    tools = (_entry("sk_other", "alpha 数据"), _entry("sk_alpha", "通用描述"))
    assert [entry.name for entry in search_catalog(tools, "alpha")] == [
        "sk_alpha", "sk_other",
    ]


def test_search_requires_every_token():
    assert search_catalog((_entry("sk_alpha", "读取 Alpha 数据"),), "alpha 缺失") == ()


def test_search_is_deterministic_and_bounded():
    tools = tuple(_entry(f"sk_t{index:02d}", "同质能力") for index in range(8))
    first = search_catalog(tools, "同质", limit=3)
    assert [entry.name for entry in first] == ["sk_t00", "sk_t01", "sk_t02"]
    assert search_catalog(tools, "同质", limit=3) == first


def test_normalize_query_and_limit():
    assert normalize_query("  alpha   beta ") == ("alpha beta", "")
    assert normalize_query(None)[1]
    assert normalize_query(3)[1]
    assert normalize_query("   ")[1]
    assert normalize_limit(None) == (DEFAULT_TOOL_SEARCH_LIMIT, "")
    assert normalize_limit(MAX_TOOL_SEARCH_LIMIT)[0] == MAX_TOOL_SEARCH_LIMIT
    assert normalize_limit(MAX_TOOL_SEARCH_LIMIT + 1)[1]
    assert normalize_limit(0)[1]
    assert normalize_limit(True)[1]


# ───────────────────────── 循环接入（检索是决策，不是动作） ─────────────────────────


@pytest.fixture()
def skills(store) -> SkillRegistry:
    registry = SkillRegistry(store)
    ensure_official_pack(registry)
    return registry


@pytest.fixture()
def runner(store, skills) -> SkillRunner:
    out = SkillRunner(store, skills)
    out.register_executor(
        "sk_data_aggregate_v1.0", lambda ctx, params: ResultEnvelope.ok({"cards": []})
    )
    return out


class ScriptedProtocol:
    def __init__(self, replies) -> None:
        self._replies = list(replies)
        self.contexts: list = []

    def tool_calls_supported(self) -> bool:
        return True

    def invoke_turn(self, context, *, initiator: str, purpose: str) -> TurnResult:
        self.contexts.append(context)
        return self._replies.pop(0)


class AllowGate:
    def authorize(self, **kwargs) -> GateDecision:
        return GateDecision(verdict="autonomous-ok")


def test_search_does_not_produce_a_step_or_trace_entry(store, runner, skills):
    protocol = ScriptedProtocol([
        TurnResult(tool_calls=(
            ToolCall(call_id="c1", name=TOOL_SEARCH_TOOL,
                     arguments={"query": "sk_data_aggregate"}),
        )),
        TurnResult(tool_calls=(
            ToolCall(call_id="c2", name="sk_data_aggregate", arguments={}),
        )),
        TurnResult(text="结束"),
    ])
    outcome = run_agent_loop(
        task="任务", endpoint_id="ep", protocol=protocol, runner=runner,
        tools=skills.tool_catalog(), gate=AllowGate(), tool_lazy_chars=1,
    )
    assert outcome.termination == "finished"
    assert len(outcome.steps) == 1
    assert len(outcome.trace.steps) == 1
    assert [record.query for record in outcome.tool_searches] == ["sk_data_aggregate"]
    assert "sk_data_aggregate" in outcome.tool_searches[0].matched
    assert "sk_data_aggregate" in protocol.contexts[1].retrieved_tools
    assert protocol.contexts[1].tool_search_note


def test_invalid_query_does_not_terminate_the_loop(store, runner, skills):
    protocol = ScriptedProtocol([
        TurnResult(tool_calls=(
            ToolCall(call_id="c1", name=TOOL_SEARCH_TOOL, arguments={}),
        )),
        TurnResult(text="结束"),
    ])
    outcome = run_agent_loop(
        task="任务", endpoint_id="ep", protocol=protocol, runner=runner,
        tools=skills.tool_catalog(), gate=AllowGate(), tool_lazy_chars=1,
    )
    assert outcome.termination == "finished"
    note = outcome.tool_searches[0].note
    assert "query" in note
    assert protocol.contexts[1].tool_search_note == note


def test_search_is_unknown_when_catalog_is_not_lazy(store, runner, skills):
    protocol = ScriptedProtocol([
        TurnResult(tool_calls=(
            ToolCall(call_id="c1", name=TOOL_SEARCH_TOOL, arguments={"query": "x"}),
        )),
    ])
    outcome = run_agent_loop(
        task="任务", endpoint_id="ep", protocol=protocol, runner=runner,
        tools=skills.tool_catalog(), gate=AllowGate(),
    )
    assert outcome.termination == "unknown_tool"
    assert outcome.tool_searches == ()
