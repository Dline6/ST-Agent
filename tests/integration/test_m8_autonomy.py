"""`T-INT-009` · M8 集成关卡：自主可跑的跨层装配用例。

只测**装配关系与跨层数据流**（单叶行为已由各自套件覆盖）：GWT 锚在
[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md) 的四段循环与
[02 §5](../../docs/技术架构-v2/02-L0-本地优先基座.md) 的只读查询 / 数据现状面。

**全离线**：出网只换掉 HTTP 发送口（`llm_post`），`LlmClient` / transport / 出网网关 /
能力协商 / 启动探测**都是真的**；真端点路径在 `tests/live/`。**出厂配置**一次都不放宽
（不调 `set_tier` / `set_grading`）——这正是 GWT-1 要证的那件事。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from rig import PASS, MarketData, RecordingSender
from rig_m1 import DeterministicUnderstander
from rig_m5 import ENV, ScriptedEndpoint
from rig_m8 import (
    SECTOR_QUERY,
    SECTOR_SQL,
    STALE_AS_OF,
    TASK_TEXT,
    seeded_m8,
    seed_sector_market_db,
    unseeded_m8,
)

from st_agent.__main__ import build_ambient_runtime
from st_agent.app import M5Runtime, TurnResult
from st_agent.l1.runner.models import SkillRun
from st_agent.l3.intent import IntentDraft
from st_agent.l3.runtime import load_agent_run

SQL_AS_OF = "2026-09-26T08:00:00+08:00"
"""种子「全库最后更新时间 T」（`sync_state.last_success_at` 的最大值，关卡 A3）。"""


# ───────────────────────────── 共用小工具 ─────────────────────────────


def _investigate(rig, text: str = TASK_TEXT):
    """走一次 `investigate` 全流程（意图 → 确认 → 派发），返回派发产出。"""
    turn = rig.m8.chat.post(text)
    assert isinstance(turn, TurnResult), turn
    assert turn.needs_confirmation, "investigate 意图应产出确认卡"
    return rig.m8.chat.confirm_and_dispatch(turn.session_id)


def _skill_run(rig, skill_run_id: str) -> SkillRun:
    raw = rig.m8.store.get("execution_log", f"skill-run/{skill_run_id}.json")
    return SkillRun.model_validate_json(raw)


def _skill_run_ids(rig) -> list[str]:
    prefix = "skill-run/"
    return [
        name[len(prefix):-len(".json")]
        for name in rig.m8.store.list_files("execution_log")
        if name.startswith(prefix)
    ]


def _count_skill(rig, base: str) -> int:
    return sum(
        1 for run_id in _skill_run_ids(rig)
        if _skill_run(rig, run_id).skill_id.startswith(f"{base}_")
    )


# ─────────────── GWT-1 · 出厂配置下自主走通一条本地数据问题 ───────────────


class TestGwt1AutonomousLocalQuestion:
    def test_factory_config_runs_freshness_then_sql_without_any_confirmation(
        self, tmp_path: Path
    ):
        """Given 出厂配置与已同步数据，When 问「ST 股票热门板块」，Then 循环自主
        走完 数据现状 → SQL 聚合 → 证据包，**读操作 0 次确认**。"""
        rig = seeded_m8(
            tmp_path / "root",
            script=[("sk_data_status", {}), SECTOR_QUERY],
        )

        # 出厂口径：运行期档 autonomous + 两条只读原语在清单里 autonomous-ok（D-143）
        assert rig.m8.l6.agent_authorization.tier() == "autonomous"

        outcome = _investigate(rig)
        record = outcome.investigation.report.record

        # 两步都由模型按工具目录选出，且都真的经 L1 执行成功——0 次确认
        assert outcome.envelope.status == "ok"
        assert [step.tool_name for step in record.steps] == [
            "sk_data_status", "sk_local_sql",
        ]
        assert [step.status for step in record.steps] == ["ok", "ok"]
        assert record.termination == "finished"
        assert record.pending is None
        assert len(rig.endpoint.loop_calls) == 3          # 两步 + 结束轮

        # 第一步**先判数据新旧**：现状里带着数据域截止日
        status_run = _skill_run(rig, record.steps[0].skill_run_id)
        assert status_run.envelope.data["as_of"]["kline"] == STALE_AS_OF
        assert status_run.envelope.data["last_updated_at"] == SQL_AS_OF

        # 第二步是真 SQL 聚合：按行业统计 ST 标的与均涨幅（种子见 rig_m8）
        sql_run = _skill_run(rig, record.steps[1].skill_run_id)
        assert sql_run.envelope.data["rows"] == [
            {"industry": "银行", "st_count": 2, "avg_pct_chg": 1.5},
            {"industry": "电子", "st_count": 1, "avg_pct_chg": 5.0},
            {"industry": "医药", "st_count": 1, "avg_pct_chg": -1.0},
        ]

        # 证据包：一步一条表行，成功步即引用清单
        slots = outcome.envelope.data.slots
        rows = slots["rows"]
        assert [row["tool_name"] for row in rows] == ["sk_data_status", "sk_local_sql"]
        assert [row["status"] for row in rows] == ["ok", "ok"]
        assert {ref.ref for ref in outcome.envelope.evidence_refs} == {
            step.skill_run_id for step in record.steps
        }

    def test_production_entry_assembles_the_same_chain(self, tmp_path: Path):
        """生产入口（`build_ambient_runtime`）装配出的同一链路照样自主跑通。"""
        root = tmp_path / "root"
        seed_sector_market_db(root, PASS)
        endpoint = ScriptedEndpoint(script=[("sk_data_status", {}), SECTOR_QUERY])

        runtime = build_ambient_runtime(
            root, PASS, market_query=MarketData(), sender=RecordingSender(),
            llm_env=ENV, llm_post=endpoint,
            understander=DeterministicUnderstander(
                [("分析", IntentDraft(intent="investigate", target=TASK_TEXT))]
            ),
            dotenv_path=root.parent / "test.env",
            bootstrap_file=root.parent / "test-bootstrap.json",
        )

        assert isinstance(runtime, M5Runtime)
        turn = runtime.chat.post(TASK_TEXT)
        outcome = runtime.chat.confirm_and_dispatch(turn.session_id)

        assert outcome.envelope.status == "ok"
        assert [
            step.tool_name for step in outcome.investigation.report.record.steps
        ] == ["sk_data_status", "sk_local_sql"]


# ──────────────────── GWT-2 · 数据陈旧 / 缺失：显式、不装完成 ────────────────────


class TestGwt2StaleOrMissingData:
    def test_stale_data_carries_its_cutoff_into_the_next_round(self, tmp_path: Path):
        """Given 数据陈旧，Then 截止日一步不漏地带给模型与产出，不把它说成最新。"""
        rig = seeded_m8(
            tmp_path / "root",
            script=[("sk_data_status", {}), SECTOR_QUERY],
            finish=f"本地数据截止 {STALE_AS_OF}；如需最新结果，请先在设置页同步市场数据。",
        )

        outcome = _investigate(rig)
        record = outcome.investigation.report.record

        # 数据现状与 SQL 结果都标着**同一个**截止日（陈旧是客观字段，不是模型措辞）
        status_run = _skill_run(rig, record.steps[0].skill_run_id)
        sql_run = _skill_run(rig, record.steps[1].skill_run_id)
        assert status_run.envelope.data["as_of"]["kline"] == STALE_AS_OF
        assert status_run.envelope.data["last_updated_at"] == SQL_AS_OF
        assert sql_run.envelope.data["as_of"] == SQL_AS_OF
        assert datetime.fromisoformat(SQL_AS_OF) < datetime.now().astimezone()

        # 截止日确实进了下一轮模型可见上下文（否则「判新旧」只是空谈）
        assert STALE_AS_OF in rig.endpoint.loop_calls[1]["prompt"]
        # 收尾文本原样进产出的 answer 槽（措辞由脚本代表，见任务 A4）
        slots = outcome.envelope.data.slots
        assert slots["answer"] == (
            f"本地数据截止 {STALE_AS_OF}；如需最新结果，请先在设置页同步市场数据。"
        )
        assert all(row["status"] == "ok" for row in slots["rows"])

    def test_missing_database_is_explicit_and_no_evidence_is_faked(self, tmp_path: Path):
        """Given 数据缺失，Then 两处都显式不可用、点名下一步（首次同步），且不产假证据。"""
        rig = unseeded_m8(
            tmp_path / "root",
            script=[("sk_data_status", {}), SECTOR_QUERY],
        )

        outcome = _investigate(rig)
        record = outcome.investigation.report.record

        # 两步都跑过了（不是静默跳过），但都如实标 unavailable
        assert [step.tool_name for step in record.steps] == [
            "sk_data_status", "sk_local_sql",
        ]
        assert [step.status for step in record.steps] == ["unavailable", "unavailable"]

        # 产出照实：表行非 ok、逐步原因点名「首次同步未完成」，且引用清单为空
        slots = outcome.envelope.data.slots
        rows = slots["rows"]
        assert [row["status"] for row in rows] == ["unavailable", "unavailable"]
        notes = {item["index"]: item["note"] for item in slots["step_notes"]}
        assert "首次同步未完成" in notes[1]
        assert "首次同步未完成" in notes[2]
        assert outcome.envelope.evidence_refs == ()

        # 原因也进了下一轮上下文（模型据此给出下一步）
        assert "首次同步未完成" in rig.endpoint.loop_calls[1]["prompt"]


# ────────────── GWT-3 · 需确认的一步交还后可一键续跑 ──────────────


class TestGwt3HandbackAndResume:
    def test_handback_then_one_click_resume_does_not_replay_prior_steps(
        self, tmp_path: Path
    ):
        """Given 某步被判需确认，Then 交还并可一键续跑；前序证据不重放。"""
        rig = seeded_m8(
            tmp_path / "root",
            script=[("sk_data_status", {}), ("sk_sector_heatmap", {"top_n": 5})],
        )

        first = _investigate(rig)
        record = first.investigation.report.record

        # 交还**显式**：已执行的一步留着，未执行的那步与原因一并给出（不是假装完成）
        slots = first.envelope.data.slots
        assert first.envelope.status == "ok"
        assert slots["termination"] == "下一步待用户确认"
        assert "下一步" in slots["termination_reason"]
        assert slots["pending"]["tool_name"] == "sk_sector_heatmap"
        assert [row["status"] for row in slots["rows"]] == ["ok"]
        assert record.termination == "needs_confirmation"
        assert record.pending is not None
        assert record.pending.tool_name == "sk_sector_heatmap"
        assert [step.tool_name for step in record.steps] == ["sk_data_status"]

        # 一键续跑：确认那一步后继续（动作归派发总线的鸭子 `resume` 端口）
        payload = rig.m8.chat.turn({
            "action": "resume",
            "agent_run_id": first.investigation.agent_run_id,
            "decision": "approve",
            "session_id": first.investigation.session_id,
        })
        assert payload["reply"].status == "ok"
        assert payload["agent_continuation"] is None

        resumed = load_agent_run(
            rig.m8.store, payload["reply"].data.slots["agent_run_id"]
        )
        assert resumed.resumed_from == first.investigation.agent_run_id
        assert [step.tool_name for step in resumed.steps] == [
            "sk_data_status", "sk_sector_heatmap",
        ]
        assert [step.status for step in resumed.steps] == ["ok", "ok"]

        # 前序不重放：数据现状只执行过一次，确认步也只执行过一次
        assert _count_skill(rig, "sk_data_status") == 1
        assert _count_skill(rig, "sk_sector_heatmap") == 1


# ────────────── GWT-4 · 全链路 Trace 携 SQL 原文、参数与 as_of ──────────────


class TestGwt4TraceIsDrillable:
    def test_every_trace_step_resolves_to_sql_params_and_as_of(self, tmp_path: Path):
        """Given 全链路，Then 沿 Trace 的 `ref` 下钻，SQL 原文 / 绑定参数 / as_of 齐全。"""
        rig = seeded_m8(
            tmp_path / "root",
            script=[("sk_data_status", {}), SECTOR_QUERY],
        )

        outcome = _investigate(rig)
        trace = outcome.investigation.trace

        assert [step.step_type for step in trace.steps] == ["skill_run", "skill_run"]
        runs = [_skill_run(rig, step.ref) for step in trace.steps]

        assert runs[0].skill_id.startswith("sk_data_status_")
        sql_run = runs[1]
        assert sql_run.skill_id.startswith("sk_local_sql_")
        assert sql_run.params["sql"].strip() == SECTOR_SQL.strip()
        assert sql_run.inputs["params"] == [1]
        assert sql_run.envelope.data["as_of"] == SQL_AS_OF

        # 证据包的表行与链逐步对位（每条都指向真实存在的 `skill-run/` 记录）
        rows = outcome.envelope.data.slots["rows"]
        assert [row["log_ref"] for row in rows] == [
            f"skill-run/{step.ref}.json" for step in trace.steps
        ]
