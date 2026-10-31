"""`T-INT-011` · M10 集成关卡：长任务运行时的跨层装配用例。

只测**装配关系与跨层数据流**（单叶行为已由各自套件覆盖）：GWT 锚在
[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md) 的四段循环与 M10 四组能力
（工具检索 / 大结果回读 / 并行调用 / 子 Agent）的契约面。

**全离线**：出网只换掉 HTTP 发送口（`llm_post`），`LlmClient` / transport / 出网网关 /
能力协商 / 启动探测**都是真的**；MCP 用本机 stdio 存根（`tests/l1/_mcp_stub_server.py`）。
**出厂配置一次都不放宽**（不调 `set_grading` / `set_tier`）——GWT-3 要证的正是这一点。
"""

from __future__ import annotations

import json
from pathlib import Path

from rig_m10 import (
    MCP_SERVER_ID,
    TASK_TEXT,
    Step,
    first_skill_run_id,
    latest_next_offset,
    load_skill_run,
    original_chars,
    seeded_m10,
    skill_run_ids,
)

from st_agent.app import TurnResult
from st_agent.l3.runtime import load_agent_run
from st_agent.l3.runtime.context import RESULT_TRUNCATE_CHARS

LONG_TASK_STEPS: list[tuple[str, dict]] = [
    ("sk_data_status", {}),
    ("sk_mcp_list_resources", {"server_id": MCP_SERVER_ID}),
    ("sk_mcp_read_resource", {
        "server_id": MCP_SERVER_ID, "uri": "stub://watchlist/default",
    }),
    ("sk_local_sql", {"sql": "SELECT code, code_name FROM security ORDER BY code"}),
    ("sk_local_sql", {"sql": "SELECT count(*) AS n FROM k_line_daily"}),
    ("sk_local_sql", {
        "sql": "SELECT code, trade_date, close, is_st FROM k_line_daily "
               "ORDER BY code, trade_date",
    }),
    ("sk_local_sql", {
        "sql": "SELECT task_key, last_status FROM sync_state ORDER BY task_key",
    }),
    ("sk_local_sql", {
        "sql": "SELECT code, type, status FROM st_membership ORDER BY code",
    }),
    ("sk_local_sql", {"sql": "SELECT count(*) AS n FROM st_membership"}),
    ("sk_local_sql", {"sql": "SELECT max(trade_date) AS d FROM k_line_daily"}),
    ("sk_local_sql", {"sql": "SELECT count(*) AS n FROM security WHERE status = 1"}),
    ("sk_local_sql", {"sql": "SELECT source_id, name FROM data_source ORDER BY source_id"}),
]
"""GWT-1 的十余步任务：四条不同工具 + 八条只读查询，全部 `autonomous-ok`。"""

BIG_SQL = (
    "WITH RECURSIVE seq(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM seq WHERE x < 40) "
    "SELECT x AS n, printf('%0500d', x) AS pad FROM seq"
)
"""GWT-2 的大结果（约 2 万字符，超过单条截断阈值 1200）。"""

SLOW_SQL = (
    "WITH RECURSIVE seq(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM seq WHERE x < 300000) "
    "SELECT count(*) AS n FROM seq"
)
"""GWT-3 并行批里的慢查询（用于制造完成先后，输出顺序仍须按调用序）。"""

FAST_SQL = "SELECT 1 AS n"

CHILD_TASK = "子任务：统计本地库里的 ST 成员并回传结论"
CHILD_SQL = "SELECT 'child-only-marker' AS tag, count(*) AS n FROM st_membership"
CHILD_ANSWER = "本地库中有 2 条 ST 成员记录。"
PARENT_ANSWER = "已由子 Agent 完成统计。"


class RecordingAuthorization:
    """闸门判据面的记录代理（GWT-3 断言「每个调用在执行前都问过，且按调用序」）。"""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.decisions: list[str] = []

    def decide(self, action_id: str):
        self.decisions.append(action_id)
        return self.inner.decide(action_id)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def _investigate(rig, text: str = TASK_TEXT):
    turn = rig.m10.chat.post(text)
    assert isinstance(turn, TurnResult), turn
    assert turn.needs_confirmation, "investigate 意图应产出确认卡"
    return rig.m10.chat.confirm_and_dispatch(turn.session_id)


def _payload_text(data) -> str:
    """与 `context._json` / `results._payload_text` 同一确定性序列化。"""
    return json.dumps(data, ensure_ascii=False, sort_keys=True, default=repr)


def _tune(rig, *, steps: int, llm_calls: int, budget: int) -> None:
    rig.m10.m1.bounds.set_steps(steps)
    rig.m10.m1.bounds.set_llm_calls(llm_calls)
    rig.m10.m1.bounds.set_context_budget(budget)


# ───────────── GWT-1 · 十余步、多工具：跑完且不崩上下文 ─────────────


class TestGwt1LongMultiToolTask:
    def test_twelve_steps_finish_with_every_result_retained(self, tmp_path: Path):
        """Given 12 步 / 4 类工具的盘点任务，When 循环执行，Then 不超预算、
        12 步结果一条不丢、模型自行收尾。"""
        rig = seeded_m10(
            tmp_path / "root",
            script=[Step(calls=(call,)) for call in LONG_TASK_STEPS],
        )
        _tune(rig, steps=16, llm_calls=24, budget=32000)

        outcome = _investigate(rig)
        record = outcome.investigation.report.record

        assert outcome.envelope.status == "ok"
        assert record.termination == "finished"
        assert record.at_bound is False
        assert record.max_steps == 16
        assert record.budget_chars == 32000
        assert record.executed_steps == len(LONG_TASK_STEPS) == 12
        assert record.context_dropped == ()

        # 步步都由模型按目录选出，且都真的经 L1 执行成功
        assert [s.tool_name for s in record.steps] == [
            name for name, _ in LONG_TASK_STEPS
        ]
        assert [s.status for s in record.steps] == ["ok"] * 12

        # 12 步 + 收尾轮：每轮 prompt 都在预算内
        assert len(rig.endpoint.loop_calls) == 13
        assert all(
            len(call["prompt"]) <= 32000 for call in rig.endpoint.loop_calls
        )

        final_prompt = rig.endpoint.loop_calls[-1]["prompt"]
        assert "已执行动作与结果（按发生顺序，共 12 步）" in final_prompt
        for step in record.steps:
            assert f"调用 {step.tool_name}（执行目标 {step.skill_id}）" in final_prompt

        # 每条结果都在上下文里：未截断者全文可见；截断者带真实回读指针
        for step in record.steps:
            text = _payload_text(load_skill_run(rig, step.skill_run_id).envelope.data)
            if len(text) <= RESULT_TRUNCATE_CHARS:
                assert text in final_prompt
            else:
                assert f"skill_run_id='{step.skill_run_id}'" in final_prompt

        # 留痕端到端可查：每步指向真实记录，记录状态与步骤一致
        for step in record.steps:
            run = load_skill_run(rig, step.skill_run_id)
            assert run.envelope.status == step.status == "ok"
            assert step.log_ref == f"skill-run/{step.skill_run_id}.json"
            assert rig.store.get("execution_log", step.log_ref)
        assert {ref.ref for ref in outcome.envelope.evidence_refs} == {
            step.skill_run_id for step in record.steps
        }


class TestGwt1ToolSearchKeepsTheWindowBounded:
    def test_lazy_catalog_expands_on_demand_and_the_expanded_tool_still_runs(
        self, tmp_path: Path
    ):
        """Given 目录超过预算进入惰性态，When 模型检索后调用展开的条目，Then
        检索只改上下文、不产执行步，展开的条目仍走同一条闸门与 L1 流水线。"""
        rig = seeded_m10(
            tmp_path / "root",
            script=[
                Step(calls=(("sk_tool_search", {"query": "本地 只读"}),)),
                Step(calls=(("sk_local_sql", {"sql": "SELECT 1 AS n"}),)),
            ],
            with_mcp=False,
        )
        _tune(rig, steps=8, llm_calls=12, budget=5000)

        outcome = _investigate(rig)
        record = outcome.investigation.report.record

        assert outcome.envelope.status == "ok"
        assert record.termination == "finished"
        # 惰性态：首轮只下发检索入口，展开后次轮才下发该条目
        assert rig.endpoint.loop_calls[0]["tools"] == ("sk_tool_search",)
        assert "sk_local_sql" in rig.endpoint.loop_calls[1]["tools"]
        assert "命中" in rig.endpoint.loop_calls[1]["prompt"]

        # 检索是上下文控制：不产 skill_run / Trace 步，记录进 agent_run
        assert len(record.tool_searches) == 1
        assert "sk_local_sql" in record.tool_searches[0].matched
        assert [s.tool_name for s in record.steps] == ["sk_local_sql"]
        assert [step.ref for step in outcome.investigation.trace.steps] == [
            record.steps[0].skill_run_id
        ]


# ───────────── GWT-2 · 大结果：可回读而不必重查 ─────────────


class TestGwt2LargeResultReadBack:
    def test_truncated_result_is_reassembled_from_read_back_chunks(
        self, tmp_path: Path
    ):
        """Given 一条超阈值结果，Then 截断件给指针，模型经 `sk_result_read` 分片
        拼回完整载荷，且原查询只执行一次。"""

        def next_step(prompt: str) -> Step:
            total = original_chars(prompt)
            offset = latest_next_offset(prompt)
            if total and offset >= total:
                return Step(text="完整结果已回读完毕")
            return Step(calls=((
                "sk_result_read",
                {
                    "skill_run_id": first_skill_run_id(prompt),
                    "offset": offset,
                    "max_chars": 8000,
                },
            ),))

        rig = seeded_m10(
            tmp_path / "root",
            script=[Step(calls=(("sk_local_sql", {"sql": BIG_SQL}),)), *([next_step] * 12)],
            with_mcp=False,
        )
        _tune(rig, steps=16, llm_calls=24, budget=32000)

        outcome = _investigate(rig)
        record = outcome.investigation.report.record

        assert outcome.envelope.status == "ok"
        assert record.termination == "finished"

        big_step = record.steps[0]
        big_run = load_skill_run(rig, big_step.skill_run_id)
        full_text = _payload_text(big_run.envelope.data)
        assert len(full_text) > RESULT_TRUNCATE_CHARS

        # 截断件确实进了上下文：带 skill_run_id / next_offset / read_tool 与回读提示
        truncation_prompt = rig.endpoint.loop_calls[1]["prompt"]
        assert "truncated=True" in truncation_prompt
        assert f"skill_run_id='{big_step.skill_run_id}'" in truncation_prompt
        assert "read_tool='sk_result_read'" in truncation_prompt
        assert "next_offset=0" in truncation_prompt
        assert (
            f'sk_result_read {{"skill_run_id": "{big_step.skill_run_id}", "offset": 0'
            in truncation_prompt
        )

        # 回读步：分片按顺序拼接，等于原始载荷；最后一片不再截断
        reads = [s for s in record.steps if s.tool_name == "sk_result_read"]
        read_data = [
            load_skill_run(rig, step.skill_run_id).envelope.data for step in reads
        ]
        assert len(reads) >= 2
        assembled = "".join(chunk["text"] for chunk in read_data)
        assert assembled == full_text
        assert read_data[0]["total_chars"] == len(full_text)
        assert read_data[0]["truncated"] is True
        assert read_data[-1]["truncated"] is False
        assert read_data[-1]["next_offset"] == len(full_text)

        # 不重查：同一条大查询只执行过一次
        executed_big = [
            run_id for run_id in skill_run_ids(rig)
            if load_skill_run(rig, run_id).skill_id.startswith("sk_local_sql_")
            and load_skill_run(rig, run_id).params.get("sql") == BIG_SQL
        ]
        assert executed_big == [big_step.skill_run_id]


# ───────────── GWT-3 · 并行调用：权限与留痕口径不放宽 ─────────────


class TestGwt3ParallelKeepsGateAndTrace:
    def test_non_autonomous_call_in_a_batch_blocks_the_whole_batch(
        self, tmp_path: Path, monkeypatch
    ):
        """Given 同轮一个只读调用 + 一个写类调用，Then 闸门按调用序先问过两者、
        整批不执行、零留痕，并点名同轮其余调用未执行。"""
        rig = seeded_m10(
            tmp_path / "root",
            script=[Step(calls=(
                ("sk_local_sql", {"sql": "SELECT 1 AS n"}),
                ("sk_file_write", {
                    "path": "workspace/m10-should-not-exist.txt",
                    "content": "不应写入",
                }),
            ))],
            with_mcp=False,
        )
        recorder = RecordingAuthorization(rig.m10.l6.agent_authorization)
        monkeypatch.setattr(rig.m10.investigations, "_authorization", recorder)

        outcome = _investigate(rig)
        record = outcome.investigation.report.record

        assert outcome.envelope.status == "empty"
        assert record.termination == "needs_confirmation"
        assert record.steps == ()
        assert record.pending is not None
        assert record.pending.tool_name == "sk_file_write"
        assert "同轮其余 1 个调用未执行" in (outcome.envelope.reason or "")

        # 闸门按调用顺序问过两个动作，且在执行之前
        assert recorder.decisions == ["skill.sk_local_sql", "skill.sk_file_write"]
        # 整批零执行：没有任何 skill_run，工作区也没有文件
        assert skill_run_ids(rig) == []
        assert rig.store.list_files("workspace") == ()
        assert len(rig.endpoint.loop_calls) == 1

    def test_parallel_batch_keeps_call_order_in_trace_and_records(
        self, tmp_path: Path, monkeypatch
    ):
        """Given 同轮一慢一快两条只读查询，When 真并行执行，Then Trace 与上下文
        仍按调用顺序回填，且两条留痕都在。"""
        rig = seeded_m10(
            tmp_path / "root",
            script=[Step(calls=(
                ("sk_local_sql", {"sql": SLOW_SQL}),
                ("sk_local_sql", {"sql": FAST_SQL}),
            ))],
            with_mcp=False,
        )
        recorder = RecordingAuthorization(rig.m10.l6.agent_authorization)
        monkeypatch.setattr(rig.m10.investigations, "_authorization", recorder)

        outcome = _investigate(rig)
        record = outcome.investigation.report.record

        assert outcome.envelope.status == "ok"
        assert record.termination == "finished"
        assert record.parallel_used is True
        assert "并行执行" in record.parallel_note

        # 输出、Trace 与留痕都按调用顺序（慢的在前，完成先后不影响）
        assert [
            load_skill_run(rig, s.skill_run_id).params["sql"] for s in record.steps
        ] == [SLOW_SQL, FAST_SQL]
        assert [step.ref for step in outcome.investigation.trace.steps] == [
            s.skill_run_id for s in record.steps
        ]
        for step in record.steps:
            assert load_skill_run(rig, step.skill_run_id).params["sql"] in (
                SLOW_SQL, FAST_SQL,
            )
        assert recorder.decisions == ["skill.sk_local_sql", "skill.sk_local_sql"]


# ───────────── GWT-4 · 子 Agent：上下文隔离，只回传结论与引用 ─────────────


class TestGwt4SubAgentIsolation:
    def test_child_steps_stay_out_of_the_main_window(self, tmp_path: Path):
        """Given 派生一个子 Agent，When 子循环跑完后父循环收尾，Then 子步不进主链 /
        主窗，主上下文只有结论与引用，子运行以 `parent_trace_id` 指回父链。"""
        rig = seeded_m10(
            tmp_path / "root",
            script=[
                Step(calls=(("sk_spawn_agent", {"task": CHILD_TASK}),)),
                Step(calls=(("sk_local_sql", {"sql": CHILD_SQL}),)),
                Step(text=CHILD_ANSWER),
                Step(text=PARENT_ANSWER),
            ],
            with_mcp=False,
        )

        outcome = _investigate(rig)
        record = outcome.investigation.report.record

        assert outcome.envelope.status == "ok"
        assert record.termination == "finished"
        # 派生本身不产动作步，子步骤也不并入父链
        assert record.steps == ()
        assert outcome.investigation.trace.steps == ()
        assert len(record.sub_agents) == 1

        sub = record.sub_agents[0]
        assert sub.task == CHILD_TASK
        assert sub.termination == "finished"
        assert sub.answer == CHILD_ANSWER
        assert sub.trace_id != record.trace_id
        assert len(sub.evidence) == 1

        # 子运行另落一条 agent_run，并用 parent_trace_id 指回父链
        child = load_agent_run(rig.store, sub.agent_run_id)
        assert child.parent_trace_id == record.trace_id
        assert child.task == CHILD_TASK
        assert [step.tool_name for step in child.steps] == ["sk_local_sql"]
        assert sub.evidence[0].ref == child.steps[0].skill_run_id
        child_run = load_skill_run(rig, child.steps[0].skill_run_id)
        assert child_run.envelope.data["rows"] == [
            {"n": 2, "tag": "child-only-marker"}
        ]

        # 主窗只拿到结论与引用：子步的结果载荷不出现在父循环的下一轮 prompt
        parent_prompt = rig.endpoint.loop_calls[-1]["prompt"]
        assert "子 Agent 结果（子上下文不进本窗，仅回传结论与引用）" in parent_prompt
        assert CHILD_ANSWER in parent_prompt
        assert child.steps[0].skill_run_id in parent_prompt
        assert "child-only-marker" not in parent_prompt

        # 子上下文同样隔离：不含父任务，且目录剔除派生原语（只允许一层）
        child_prompt = rig.endpoint.loop_calls[1]["prompt"]
        assert TASK_TEXT not in child_prompt
        assert CHILD_TASK in child_prompt
        assert "sk_spawn_agent" not in rig.endpoint.loop_calls[1]["tools"]

        # 预算归属：派生 1 步 + 子循环 1 步；LLM 调用 2（父）+ 2（子）
        assert record.executed_steps == 2
        assert record.llm_calls == 4
        # 子 Agent 的成功步引用并入父产出
        assert sub.evidence[0].ref in {
            ref.ref for ref in outcome.envelope.evidence_refs
        }
