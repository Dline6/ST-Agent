"""T-L1-004.3 测试：主动服务 Bundle 执行器 + 未来函数检测（03 §2.2）。

数据面用真实 `MarketDb` 灌入 25 个交易日的离线样本（覆盖「连续 N 日低于面值 /
成交额枯竭」的倒计时口径）。需要上游而描述体无依赖的 Skill（`data-aggregate`、
`strategy-design` 的信号定义）在**执行器级**注入 `SkillContext.upstream` 验证接线。

GWT 对照（任务文件 5 条）：
- GWT-1 六个执行器逐个产出（`data-aggregate` 无上游时为 `empty`、上游就绪时 `ok`）
- GWT-2 依赖联动按声明生效（上游失败 → `dependency_failed`；上游成功 → 载荷可用）
- GWT-3 未来函数检测（合法信号通过 / 未来函数报错并指出字段）
- GWT-4 无输入不编造（显式 `empty`；组合来源被显式标注）
- GWT-5 参数生效（`lookahead_days` / `max_results` / `shock` / `format`）
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.schema_check import check_payload
from st_agent.l0.market import MarketDb
from st_agent.l0.storage import Store
from st_agent.l1.runner import SkillContext, SkillRunner
from st_agent.l1.skills import SkillRegistry
from st_agent.l1.skills.official import install_official_pack
from st_agent.l1.skills.official import ambient, cognition
from st_agent.l1.skills.official.signal_check import check_future_function

PASS = "correct horse battery staple"
LAST_SUCCESS = "2026-09-25T08:00:00+00:00"

AMBIENT_BASES = (
    "sk_stock_watch",
    "sk_data_aggregate",
    "sk_risk_alert",
    "sk_opportunity_mine",
    "sk_portfolio_stress_test",
    "sk_strategy_design",
)

RISK_SCAN_ID = "sk_delisting_risk_scan_v1.0"
FUNDAMENTAL_ID = "sk_fundamental_screening_v1.0"
AGGREGATE_ID = "sk_data_aggregate_v1.0"

DAYS = 25


def _trading_days(count: int) -> tuple[str, ...]:
    """以 2026-09-25 为最新交易日向前取 ``count`` 个工作日。"""
    cursor = date(2026, 9, 25)
    out: list[str] = []
    while len(out) < count:
        if cursor.weekday() < 5:
            out.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    return tuple(reversed(out))


DATES = _trading_days(DAYS)

#: code -> (名称, 收盘价序列, 成交额序列, 换手率)
SERIES = {
    "sh.600001": ("甲材料", [0.90] * DAYS, [5.0e6] * DAYS, 1.0),
    "sh.600002": ("乙化工", [5.00] * (DAYS - 3) + [0.95] * 3, [5.0e6] * DAYS, 1.0),
    "sz.000003": ("丙科技", [8.00] * DAYS, [5.0e6] * (DAYS - 5) + [5.0e5] * 5, 12.0),
    "sh.600004": ("丁机械", [5.00] * DAYS, [5.0e6] * DAYS, 1.0),
    "sh.600010": ("己医药", [20.0] * DAYS, [5.0e6] * DAYS, 1.0),
    "sh.600012": ("辛电子", [30.0] * DAYS, [5.0e6] * DAYS, 1.0),
}

#: 最新交易日涨跌幅覆盖（构造「异动」触发样本）
LAST_DAY_PCT = {"sh.600004": 8.0}

#: (code, roe_avg, np_margin, net_profit, liability_to_asset, yoy_ni)
FIN = (
    ("sh.600001", 1.0, -5.0, -1.0e7, 50.0, -20.0),
    ("sh.600002", 2.0, -3.0, -5.0e6, 60.0, -15.0),
    ("sz.000003", 3.0, -2.0, -3.0e6, 55.0, -10.0),
    ("sh.600004", 12.0, 15.0, 1.0e7, 40.0, 20.0),
    ("sh.600010", 15.0, 20.0, 5.0e8, 30.0, 30.0),
    ("sh.600012", 25.0, 25.0, 6.0e8, 35.0, 50.0),
)


def _seed(db: MarketDb) -> None:
    daily_rows = []
    for code, (name, closes, amounts, turn) in SERIES.items():
        for index, day in enumerate(DATES):
            close = closes[index]
            daily_rows.append(
                (code, day, close, close, close, close, close, amounts[index], turn,
                 LAST_DAY_PCT.get(code, 1.0) if index == DAYS - 1 else 1.0, 1)
            )
    with db.transact() as con:
        con.execute(
            "INSERT INTO data_source(source_id, name, manual_ref)"
            " VALUES ('baostock', 'BaoStock', NULL)"
        )
        con.execute(
            "INSERT INTO sync_state(task_key, table_name, source_id, mode, schedule_desc,"
            " watermark, last_success_at, last_row_count, last_status)"
            " VALUES ('bs_k_daily', 'k_line_daily', 'baostock', 'incremental', '每日',"
            " ?, ?, ?, 'ok')",
            (DATES[-1], LAST_SUCCESS, len(daily_rows)),
        )
        con.executemany(
            "INSERT INTO security(code, code_name, ipo_date, out_date, type, status)"
            " VALUES (?, ?, '2010-01-01', NULL, 1, 1)",
            [(code, name) for code, (name, *_rest) in SERIES.items()],
        )
        con.executemany(
            "INSERT INTO k_line_daily(code, trade_date, open, high, low, close, preclose,"
            " volume, amount, turn, trade_status, pct_chg, pe_ttm, pb_mrq, is_st)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 1000000, ?, ?, 1, ?, 12.0, 1.5, ?)",
            daily_rows,
        )
        con.executemany(
            "INSERT INTO financial_quarter(code, stat_date, pub_date, roe_avg, np_margin,"
            " net_profit, liability_to_asset, yoy_ni, mb_revenue)"
            " VALUES (?, '2026-06-30', '2026-08-20', ?, ?, ?, ?, ?, 5.0e8)",
            FIN,
        )
        con.execute(
            "INSERT INTO profit_forecast(code, stat_date, pub_date, forecast_type, abstract)"
            " VALUES ('sh.600010', '2026-09-30', ?, '预增', '预计本期净利润同比增长')",
            (DATES[-1],),
        )
        con.commit()


def _open(tmp_path: Path, *, init: bool = True, seed: bool = True):
    store = Store.create(tmp_path / "root", PASS)
    db = MarketDb(store)
    if init:
        db.init_db()
    if seed:
        _seed(db)
    return store, db


def _packed(store: Store, db: MarketDb):
    """两 Bundle 全量装载（本任务完成「官方 Pack 装载入口」的完整性口径）。"""
    registry = SkillRegistry(store)
    runner = SkillRunner(store, registry)
    install_official_pack(registry, runner, market_query=db)
    return registry, runner


def _pack_permissions(registry: SkillRegistry) -> tuple[str, ...]:
    return tuple(sorted({p for d in registry.list_all() for p in d.permissions}))


def _skill_id(registry: SkillRegistry, base: str) -> str:
    return next(d.skill_id for d in registry.list_all() if d.skill_id.startswith(f"{base}_v"))


def _run(registry: SkillRegistry, runner: SkillRunner, base: str, values: dict | None = None):
    return runner.run(
        _skill_id(registry, base), values or {},
        approved_permissions=_pack_permissions(registry),
    )


def _ctx(upstream: dict[str, ResultEnvelope] | None = None) -> SkillContext:
    return SkillContext(
        skill_id="sk_probe_v1.0",
        skill_run_id="run_0000000000000000000",
        trace_id="tr_0000000000000000000",
        upstream=upstream or {},
    )


def _direct(registry, base, source, upstream=None, params=None) -> ResultEnvelope:
    """执行器级调用（用于验证「有依赖但描述体未声明」的上游接线）。

    参数经 ``registry.validate_call_params`` 合并默认值——与 runner 内口径一致。
    """
    merged = registry.validate_call_params(_skill_id(registry, base), dict(params or {}))
    factory = {**cognition.EXECUTORS, **ambient.EXECUTORS}[base]
    return factory(source)(_ctx(upstream), merged)


@pytest.fixture()
def packed(tmp_path: Path):
    store, db = _open(tmp_path)
    registry, runner = _packed(store, db)
    return registry, runner, db


@pytest.fixture()
def registry(packed):
    return packed[0]


@pytest.fixture()
def runner(packed):
    return packed[1]


@pytest.fixture()
def db(packed):
    return packed[2]


# ───────────────────────── GWT-1 六个执行器逐个产出 ─────────────────────────


class TestGwt1SixExecutors:
    @pytest.mark.parametrize(
        "base",
        [b for b in AMBIENT_BASES if b != "sk_data_aggregate"],
    )
    def test_five_run_ok_and_schema_compliant(self, registry, runner, base):
        skill_id = _skill_id(registry, base)
        out = _run(registry, runner, base)
        assert out.envelope.status == "ok", f"{base}: {out.envelope.reason}"
        check = check_payload(registry.get(skill_id).output_schema, out.envelope.data)
        assert check.passed, f"{base} 输出不合 output_schema：{check.describe()}"

    def test_data_aggregate_without_upstream_is_empty(self, registry, runner):
        out = _run(registry, runner, "sk_data_aggregate")
        assert out.envelope.status == "empty"
        assert "无上游数据可聚合" in out.envelope.reason

    def test_data_aggregate_with_upstream_is_ok(self, db, registry, runner):
        scanned = _run(registry, runner, "sk_delisting_risk_scan").envelope
        out = _direct(
            registry, "sk_data_aggregate", db,
            {RISK_SCAN_ID: scanned}, {"format": "table"},
        )
        assert out.status == "ok"
        assert out.data["sources"] == [RISK_SCAN_ID]
        assert out.data["cards"][0]["kind"] == "table"
        assert out.data["cards"][0]["payload"]["rows"]

    def test_full_pack_has_no_uncovered_base(self, registry):
        from st_agent.l1.skills.official import uncovered_bases

        assert uncovered_bases() == ()


# ───────────────────────── GWT-2 依赖联动 ─────────────────────────


class TestGwt2DependencyLinkage:
    def test_risk_alert_consumes_upstream_triggers(self, registry, runner):
        scanned = _run(registry, runner, "sk_delisting_risk_scan").envelope
        upstream_codes = {t["code"] for t in scanned.data["triggers"]}
        out = _run(registry, runner, "sk_risk_alert")
        assert out.envelope.status == "ok"
        assert {a["code"] for a in out.envelope.data["alerts"]} <= upstream_codes

    def test_opportunity_mine_consumes_upstream_pool(self, registry, runner):
        screened = _run(registry, runner, "sk_fundamental_screening").envelope
        out = _run(registry, runner, "sk_opportunity_mine")
        assert out.envelope.status == "ok"
        assert out.envelope.data["source_pool_size"] == len(screened.data["pool"])

    def test_strategy_design_declares_missing_signal_spec(self, registry, runner):
        out = _run(registry, runner, "sk_strategy_design")
        assert out.envelope.status == "ok"
        check = out.envelope.data["report"]["signal_check"]
        assert check["passed"] is None
        assert "未提供信号定义" in check["reason"]

    def test_upstream_failure_makes_downstream_dependency_failed(self, tmp_path):
        store, db = _open(tmp_path, init=False, seed=False)
        registry, runner = _packed(store, db)
        out = _run(registry, runner, "sk_risk_alert")
        assert out.envelope.status == "dependency_failed"
        assert "依赖失败" in out.envelope.reason


# ───────────────────────── GWT-3 未来函数检测 ─────────────────────────


class TestGwt3FutureFunction:
    def test_legal_spec_passes(self):
        check = check_future_function(
            {"fields": ["close", "roe_avg"],
             "align": {"close": "trade_date", "roe_avg": "pub_date"}}
        )
        assert check.passed is True
        assert check.issues == ()

    def test_financial_field_aligned_to_stat_date_is_future_function(self):
        check = check_future_function(
            {"fields": ["roe_avg"], "align": {"roe_avg": "stat_date"}}
        )
        assert check.passed is False
        issue = check.issues[0]
        assert issue.kind == "future_function"
        assert issue.field == "roe_avg"
        assert "pub_date" in issue.detail and "stat_date" in issue.detail

    def test_missing_align_and_unknown_field_are_reported(self):
        missing = check_future_function({"fields": ["close"], "align": {}})
        assert missing.passed is False
        assert missing.issues[0].kind == "missing_align"
        unknown = check_future_function(
            {"fields": ["brand_new_field"], "align": {"brand_new_field": "trade_date"}}
        )
        assert unknown.passed is False
        assert unknown.issues[0].kind == "unknown_field"

    def test_absent_spec_is_not_passed(self):
        check = check_future_function(None)
        assert check.passed is None
        assert check.reason

    def test_executor_reports_future_function_from_upstream(self, db, registry, runner):
        scanned = _run(registry, runner, "sk_delisting_risk_scan").envelope
        spec = {"fields": ["roe_avg"], "align": {"roe_avg": "stat_date"}}
        out = _direct(
            registry, "sk_strategy_design", db,
            {AGGREGATE_ID: ResultEnvelope.ok({"signal_spec": spec})},
        )
        assert out.status == "ok"
        check = out.data["report"]["signal_check"]
        assert check["passed"] is False
        assert check["issues"][0]["field"] == "roe_avg"

        ok_out = _direct(
            registry, "sk_strategy_design", db,
            {AGGREGATE_ID: ResultEnvelope.ok(
                {"signal_spec": {"fields": ["close"], "align": {"close": "trade_date"}}}
            )},
        )
        assert ok_out.data["report"]["signal_check"]["passed"] is True

    def test_executor_marks_spec_not_provided(self, db, registry):
        out = _direct(registry, "sk_strategy_design", db)
        assert out.data["report"]["signal_check"]["passed"] is None


# ───────────────────────── GWT-4 无输入不编造 ─────────────────────────


class TestGwt4NoFabrication:
    def test_risk_alert_empty_upstream_is_empty(self, db, registry):
        empty = ResultEnvelope.ok({"triggers": [], "scanned": 0})
        out = _direct(registry, "sk_risk_alert", db, {RISK_SCAN_ID: empty})
        assert out.status == "empty"
        assert "无预警维度可评估" in out.reason

    def test_opportunity_mine_empty_pool_is_empty(self, db, registry):
        empty = ResultEnvelope.ok({"pool": [], "scanned": 0})
        out = _direct(registry, "sk_opportunity_mine", db, {FUNDAMENTAL_ID: empty})
        assert out.status == "empty"
        assert "无候选可挖掘" in out.reason

    def test_data_aggregate_without_ok_upstream_is_empty(self, db, registry):
        out = _direct(
            registry, "sk_data_aggregate", db,
            {RISK_SCAN_ID: ResultEnvelope.empty("上游无结果")},
        )
        assert out.status == "empty"

    def test_portfolio_source_is_marked_not_user_supplied(self, registry, runner):
        out = _run(registry, runner, "sk_portfolio_stress_test")
        assert out.envelope.status == "ok"
        assert out.envelope.data["portfolio_source"] == "local-cache-equal-weight"
        assert out.envelope.data["holdings"] == len(SERIES)


# ───────────────────────── GWT-5 参数生效 ─────────────────────────


class TestGwt5Params:
    def test_lookahead_days_narrows_alerts(self, registry, runner):
        wide = _run(registry, runner, "sk_risk_alert", {"lookahead_days": 30})
        narrow = _run(registry, runner, "sk_risk_alert", {"lookahead_days": 5})
        assert wide.envelope.status == "ok"
        assert len(wide.envelope.data["alerts"]) > len(narrow.envelope.data["alerts"])
        assert all(a["remaining_days"] <= 5 for a in narrow.envelope.data["alerts"])

    def test_max_results_caps_candidates(self, registry, runner):
        one = _run(registry, runner, "sk_opportunity_mine", {"max_results": 1})
        two = _run(registry, runner, "sk_opportunity_mine", {"max_results": 2})
        assert len(one.envelope.data["candidates"]) == 1
        assert len(two.envelope.data["candidates"]) == 2
        assert one.envelope.data["candidates"][0]["code"] == "sh.600012"

    def test_shock_scales_scenarios(self, registry, runner):
        mild = _run(registry, runner, "sk_portfolio_stress_test", {"shock": 0.2})
        hard = _run(registry, runner, "sk_portfolio_stress_test", {"shock": 0.4})
        assert hard.envelope.data["scenarios"][1]["impact_pct"] == round(
            mild.envelope.data["scenarios"][1]["impact_pct"] * 2, 4
        )

    def test_format_switches_card_kind(self, db, registry, runner):
        scanned = _run(registry, runner, "sk_delisting_risk_scan").envelope
        for fmt in ("table", "trend", "brief"):
            out = _direct(
                registry, "sk_data_aggregate", db,
                {RISK_SCAN_ID: scanned}, {"format": fmt},
            )
            assert out.data["cards"][0]["kind"] == fmt

    def test_frequency_minutes_reflected(self, registry, runner):
        out = _run(registry, runner, "sk_stock_watch", {"frequency_minutes": 15})
        assert out.envelope.data["frequency_minutes"] == 15


# ───────────────────────── 去拟人化（承 .2 的 GWT-4 口径） ─────────────────────────


class TestNeutrality:
    def test_ambient_outputs_pass_neutrality(self, registry, runner):
        from st_agent.contracts.neutrality import NeutralityGuard

        guard = NeutralityGuard()
        for base in AMBIENT_BASES:
            out = _run(registry, runner, base)
            if out.envelope.data is None:
                continue
            verdict = guard.check_output(json.dumps(out.envelope.data, ensure_ascii=False))
            assert verdict.passed, (
                f"{base} 输出命中拟人化：{[f.matched for f in verdict.findings]}"
            )


# ───────────────────────── 装载完整性（本任务使官方 Pack 满员） ─────────────────────────


class TestPackComplete:
    def test_all_twelve_skills_runnable(self, registry, runner):
        """12 个官方 Skill 全部有执行器：跑的结论一律不是「未注册执行器」。"""
        for skill_id in (d.skill_id for d in registry.list_all()):
            out = runner.run(
                skill_id, {}, approved_permissions=_pack_permissions(registry)
            )
            assert "未注册执行器" not in (out.envelope.reason or ""), skill_id

    def test_cognition_and_ambient_tables_are_disjoint(self):
        assert not set(cognition.EXECUTORS) & set(ambient.EXECUTORS)
