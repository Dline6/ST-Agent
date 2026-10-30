"""T-AGT-010.1 / T-AGT-010.2 测试：本地数据原语（``local_sql`` / ``data_status``）。

GWT 对照：
- 010.1 GWT-1：``SELECT`` 返回列与行，SQL 原文 / 绑定参数 / ``as_of`` / 行数可回溯
- 010.1 GWT-2：写语句、DDL、多语句 → ``validation_failed`` 且不执行
- 010.1 GWT-3：超行数 / 执行时限 → 显式截断或中止，并给出继续方式
- 010.1 GWT-4：库缺失 → ``unavailable`` 并标注首次同步未完成
- 010.2 GWT-1：逐任务 / 逐表行数与最后成功时间
- 010.2 GWT-2：未同步 / 部分同步 → 区分「已知空集」与「数据缺失」
- 010.2 GWT-3：覆盖账本 → 返回缺口标的
"""

from __future__ import annotations

from pathlib import Path

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.exec.command import CommandRunner
from st_agent.l0.file_workspace import FileWorkspace
from st_agent.l0.info.docs import LocalDocSource
from st_agent.l0.market.db import MarketDb
from st_agent.l0.secrets import CredentialVault
from st_agent.l0.storage import Store
from st_agent.l1.builtin import (
    BUILTIN_PRIMITIVES,
    CommandAccess,
    ResultAccess,
    SyncPort,
    WebAccess,
    install_builtins,
)
from st_agent.l1.runner import RUN_PREFIX, SkillRunner
from st_agent.l1.runner.models import SkillRun
from st_agent.l1.skills import SkillRegistry

PASS = "correct horse battery staple"
LOCAL_SQL = "sk_local_sql_v1.0"
DATA_STATUS = "sk_data_status_v1.0"


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def db(store: Store) -> MarketDb:
    market = MarketDb(store)
    market.init_db()
    return market


@pytest.fixture()
def runner(store: Store, db: MarketDb) -> SkillRunner:
    registry = SkillRegistry(store)
    pipeline = SkillRunner(store, registry)
    install_builtins(
        registry, pipeline,
        context={
            "market": db,
            "docs": LocalDocSource(db, store),
            "sync": SyncPort(),
            "files": FileWorkspace(store),
            "web": WebAccess(store, CredentialVault(store)),
            "commands": CommandAccess(store, CommandRunner(store)),
            "results": ResultAccess(store),
        },
    )
    return pipeline


def _run(runner: SkillRunner, skill_id: str, values=None, inputs=None):
    return runner.run(skill_id, values=values, inputs=inputs)


def _seed_market(db: MarketDb) -> None:
    with db.transact() as con:
        con.execute("INSERT INTO data_source(source_id, name) VALUES ('baostock', 'BaoStock')")
        con.execute(
            "INSERT INTO security(code, code_name, type, status) "
            "VALUES ('sz.000001', '平安银行', 1, 1)"
        )
        for day, close in (("2026-10-20", 10.0), ("2026-10-21", 10.5),
                           ("2026-10-22", 10.8), ("2026-10-23", 11.0),
                           ("2026-10-26", 11.2)):
            con.execute(
                "INSERT INTO k_line_daily(code, trade_date, close, is_st) "
                "VALUES ('sz.000001', ?, ?, 0)",
                (day, close),
            )
        con.execute(
            "INSERT INTO sync_state(task_key, table_name, source_id, mode, schedule_desc,"
            " watermark, last_success_at, last_status) VALUES"
            " ('bs_k_daily', 'k_line_daily', 'baostock', 'incremental', '每交易日',"
            " '2026-10-26', '2026-10-26T18:00:00+08:00', 'ok')"
        )
        con.execute(
            "INSERT INTO sync_state(task_key, table_name, source_id, mode, schedule_desc,"
            " watermark, last_success_at, last_status) VALUES"
            " ('bs_k_period', 'k_line_period', 'baostock', 'incremental', '每周',"
            " '2026-10-23', '2026-10-23T18:00:00+08:00', 'ok')"
        )
        con.execute(
            "INSERT INTO sync_state(task_key, table_name, source_id, mode, schedule_desc,"
            " last_status, last_error) VALUES"
            " ('bs_k_minute', 'k_line_minute', 'baostock', 'incremental', '按需',"
            " 'failed', '接口超时')"
        )
        for code, status in (("sz.000001", "ok"), ("sz.000002", "partial"),
                             ("sz.000003", "failed")):
            con.execute(
                "INSERT INTO sync_coverage(code, dataset_id, variant, unit, span_start,"
                " span_end, groups, status, source_id, checked_at)"
                " VALUES (?, 'k_daily', '', 'day', '2026-10-20', '2026-10-20', 'all',"
                " ?, 'baostock', '2026-10-26T18:00:00+08:00')",
                (code, status),
            )
        con.commit()


# ───────────────────────── 010.1 · local_sql ─────────────────────────


class TestLocalSql:
    def test_select_returns_columns_and_rows_with_traceable_sql(self, store, db, runner):
        _seed_market(db)
        sql = "SELECT code, trade_date, close FROM k_line_daily ORDER BY trade_date"
        out = _run(runner, LOCAL_SQL, {"sql": sql})

        assert out.envelope.status == "ok"
        data = out.envelope.data
        assert data["columns"] == ["code", "trade_date", "close"]
        assert len(data["rows"]) == 5
        assert data["row_count"] == 5
        assert data["sql"] == sql
        assert data["as_of"]
        assert [step.step_type for step in out.trace.steps] == ["skill_run"]

        record = SkillRun.model_validate_json(
            store.get("execution_log", f"{RUN_PREFIX}{out.skill_run_id}.json")
        )
        assert record.params["sql"] == sql
        assert record.envelope.data["row_count"] == 5

        other = _run(runner, LOCAL_SQL, {"sql": "SELECT 1 AS n"})
        assert other.trace.steps[0].input_digest != out.trace.steps[0].input_digest

    def test_bound_parameters_use_the_object_input_channel(self, db, runner):
        _seed_market(db)
        out = _run(
            runner, LOCAL_SQL,
            {"sql": "SELECT close FROM k_line_daily WHERE trade_date = ?"},
            {"params": ["2026-10-22"]},
        )
        assert out.envelope.status == "ok"
        assert out.envelope.data["rows"] == [{"close": 10.8}]
        assert out.envelope.data["params"] == ["2026-10-22"]

    @pytest.mark.parametrize("sql", [
        "INSERT INTO security(code) VALUES ('sz.999999')",
        "UPDATE security SET name = 'x'",
        "CREATE TABLE injected (x INTEGER)",
        "SELECT 1; DELETE FROM security",
    ])
    def test_writes_and_multi_statements_are_rejected_without_executing(self, db, runner, sql):
        before = db.tables()
        out = _run(runner, LOCAL_SQL, {"sql": sql})
        assert out.envelope.status == "validation_failed"
        assert db.tables() == before
        assert db.query("SELECT code FROM security WHERE code = 'sz.999999'").status == "empty"

    def test_row_limit_truncates_explicitly_and_gives_a_way_to_continue(self, db, runner):
        _seed_market(db)
        out = _run(
            runner, LOCAL_SQL,
            {"sql": "SELECT trade_date FROM k_line_daily ORDER BY trade_date", "max_rows": 2},
        )
        assert out.envelope.status == "ok"
        data = out.envelope.data
        assert len(data["rows"]) == 2
        assert data["row_count"] == 2
        assert data["truncated"] is True
        assert data["limit"] == 2
        assert "LIMIT / OFFSET" in data["hint"]

    def test_timeout_aborts_with_a_narrowing_hint(self, runner):
        out = _run(
            runner, LOCAL_SQL,
            {
                "sql": (
                    "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) "
                    "SELECT count(*) FROM c"
                ),
                "timeout_ms": 100,
            },
        )
        assert out.envelope.status == "failed"
        assert "执行时限" in (out.envelope.reason or "")
        assert "WHERE" in (out.envelope.reason or "")
        assert out.envelope.log_ref

    def test_missing_database_is_unavailable(self, store):
        registry = SkillRegistry(store)
        pipeline = SkillRunner(store, registry)
        install_builtins(
            registry, pipeline,
            context={
                "market": MarketDb(store),
                "docs": LocalDocSource(MarketDb(store), store),
                "sync": SyncPort(),
                "files": FileWorkspace(store),
                "web": WebAccess(store, CredentialVault(store)),
                "commands": CommandAccess(store, CommandRunner(store)),
                "results": ResultAccess(store),
            },
        )
        out = _run(pipeline, LOCAL_SQL, {"sql": "SELECT 1"})
        assert out.envelope.status == "unavailable"
        assert "首次同步未完成" in (out.envelope.reason or "")

    def test_empty_sql_is_rejected(self, runner):
        out = _run(runner, LOCAL_SQL)
        assert out.envelope.status == "validation_failed"


# ───────────────────────── 010.2 · data_status ─────────────────────────


class TestDataStatus:
    def test_reports_tasks_tables_and_freshness(self, db, runner):
        _seed_market(db)
        out = _run(runner, DATA_STATUS)
        assert out.envelope.status == "ok"
        data = out.envelope.data
        assert data["snapshot_id"].startswith("snap_")
        assert data["as_of"]["kline"] == "2026-10-26"

        tasks = {row["task_key"]: row for row in data["tasks"]}
        assert tasks["bs_k_daily"]["state"] == "synced"
        assert tasks["bs_k_daily"]["last_success_at"] == "2026-10-26T18:00:00+08:00"
        assert tasks["bs_calendar"]["state"] == "never_synced"
        assert tasks["bs_k_minute"]["state"] == "incomplete"

        tables = {row["table"]: row for row in data["tables"]}
        assert tables["k_line_daily"] == {"table": "k_line_daily", "rows": 5, "state": "present"}
        assert tables["k_line_period"]["state"] == "known_empty"
        assert tables["trade_calendar"]["state"] == "missing"

        freshness = {row["task_key"]: row for row in data["freshness"]}
        assert freshness["bs_k_minute"]["last_error"] == "接口超时"

    def test_coverage_ledger_returns_gap_codes(self, db, runner):
        _seed_market(db)
        data = _run(runner, DATA_STATUS).envelope.data
        entry = next(row for row in data["coverage"] if row["dataset_id"] == "k_daily")
        assert entry["code_count"] == 3
        assert entry["covered_code_count"] == 1
        assert entry["gap_codes"] == ["sz.000002", "sz.000003"]
        assert data["gap_codes"] == ["sz.000002", "sz.000003"]
        assert data["gap_count"] == 2

    def test_dataset_filter_and_empty_ledger_are_explicit(self, db, runner):
        _seed_market(db)
        data = _run(runner, DATA_STATUS, {"dataset": "fin_quarter"}).envelope.data
        assert data["coverage"] == []
        assert any("fin_quarter" in note for note in data["notes"])

    def test_missing_database_is_unavailable(self, store):
        registry = SkillRegistry(store)
        pipeline = SkillRunner(store, registry)
        install_builtins(
            registry, pipeline,
            context={
                "market": MarketDb(store),
                "docs": LocalDocSource(MarketDb(store), store),
                "sync": SyncPort(),
                "files": FileWorkspace(store),
                "web": WebAccess(store, CredentialVault(store)),
                "commands": CommandAccess(store, CommandRunner(store)),
                "results": ResultAccess(store),
            },
        )
        out = _run(pipeline, DATA_STATUS)
        assert out.envelope.status == "unavailable"
        assert "首次同步未完成" in (out.envelope.reason or "")

    def test_unsynced_database_marks_data_as_missing_not_known_empty(self, db, runner):
        data = _run(runner, DATA_STATUS).envelope.data
        assert {row["state"] for row in data["tables"]} == {"missing"}
        assert any("sync_state 为空" in note for note in data["notes"])


# ───────────────────────── 安装面 ─────────────────────────


def test_seed_table_contains_the_data_control_and_file_primitives():
    assert [seed.base for seed in BUILTIN_PRIMITIVES] == [
        "sk_local_sql", "sk_data_status", "sk_data_sync", "sk_data_fetch",
        "sk_docs", "sk_plan_update", "sk_ask_user",
        "sk_file_read", "sk_file_glob", "sk_file_grep", "sk_file_write",
        "sk_web_search", "sk_web_fetch",
        "sk_run_command", "sk_result_read",
    ]


def test_runtime_installs_builtins_with_the_official_pack(store, db):
    from st_agent.l1.runtime import build_l1_runtime

    runtime = build_l1_runtime(store, market_query=db)
    names = {entry.name for entry in runtime.skills.tool_catalog()}
    assert {
        "sk_local_sql", "sk_data_status", "sk_data_sync", "sk_data_fetch", "sk_docs",
    } <= names
    assert runtime.skills.get(LOCAL_SQL).source == "builtin"
    assert runtime.skills.get(DATA_STATUS).source == "builtin"
