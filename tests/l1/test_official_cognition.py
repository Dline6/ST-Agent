"""T-L1-004.2 测试：公共认知 Bundle 执行器（03 §2.1）。

数据面用**真实** `MarketDb`（`Store` + `data_cache` 分区 + 内置 `schema.sql`）灌入
离线样本行，因此用例同时校验 SQL 与 schema 的一致性——不是靠匹配 SQL 文本的 Fake。

GWT 对照（任务文件 5 条）：
- GWT-1 六个执行器逐个产出 `ok` 且过各自 `output_schema`
- GWT-2 `st-list-sync` 的 diff 双向（新增 / 移除），无变更与无数据走 `empty`
- GWT-3 `delisting-risk-scan` 可解释（触发原因 + 证据引用）
- GWT-4 六个 Skill 的输出过 `NeutralityGuard.check_output`
- GWT-5 数据面缺失 / 空结果不编造（状态与原因原样透出）
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from st_agent.contracts.neutrality import NeutralityGuard
from st_agent.contracts.schema_check import check_payload
from st_agent.l0.market import MarketDb
from st_agent.l0.storage import Store
from st_agent.l1.runner import SkillRunner
from st_agent.l1.skills import SkillRegistry
from st_agent.l1.skills.official import install_official_pack
from st_agent.l1.skills.official.cognition import EXECUTORS

PASS = "correct horse battery staple"
LAST_SUCCESS = "2026-09-25T08:00:00+00:00"

D1, D2, D3 = "2026-09-23", "2026-09-24", "2026-09-25"

COGNITION_BASES = (
    "sk_st_list_sync",
    "sk_delisting_risk_scan",
    "sk_unhat_eligibility_check",
    "sk_sector_heatmap",
    "sk_sentiment_flow_analysis",
    "sk_fundamental_screening",
)

#: (code, 名称, 三日 ST 标记, D3 收盘价, 三日换手率%, 三日成交额元)
DAILY = (
    ("sh.600001", "甲材料", (0, 0, 1), 5.00, (1.0, 1.2, 1.1), (1.0e6, 1.1e6, 1.2e6)),
    ("sh.600002", "乙化工", (1, 1, 1), 0.80, (1.0, 1.0, 9.0), (1.0e6, 1.0e6, 1.2e6)),
    ("sh.600004", "丁机械", (0, 0, 1), 3.00, (0.5, 0.6, 0.7), (1.0e6, 1.0e6, 6.0e6)),
    ("sz.000003", "丙科技", (0, 1, 0), 12.0, (2.0, 2.0, 2.0), (1.0e6, 1.0e6, 1.0e6)),
    ("sz.000005", "戊能源", (0, 1, 0), 8.00, (1.5, 1.5, 1.5), (1.0e6, 1.0e6, 1.0e6)),
    ("sh.600010", "己医药", (0, 0, 0), 20.0, (1.0, 1.0, 1.0), (1.0e6, 1.0e6, 1.0e6)),
    ("sz.000011", "庚食品", (0, 0, 0), 15.0, (1.0, 1.0, 1.0), (1.0e6, 1.0e6, 1.0e6)),
    ("sh.600012", "辛电子", (0, 0, 0), 30.0, (1.0, 1.0, 1.0), (1.0e6, 1.0e6, 1.0e6)),
)

#: (code, stat_date, pub_date, roe_avg, np_margin, net_profit, mb_revenue, 资产负债率, yoy_ni)
FIN = (
    ("sh.600001", "2026-06-30", "2026-08-20", 6.0, 5.0, 1.0e7, 3.0e8, 40.0, 5.0),
    ("sh.600002", "2026-06-30", "2026-08-20", -12.0, -30.0, -5.0e7, 8.0e7, 85.0, -60.0),
    ("sh.600004", "2026-06-30", "2026-08-20", -2.0, -1.0, -1.0e6, 1.5e8, 55.0, -10.0),
    ("sh.600010", "2026-06-30", "2026-08-20", 15.0, 20.0, 5.0e8, 5.0e9, 30.0, 30.0),
    ("sz.000011", "2026-06-30", "2026-08-20", 5.0, 10.0, 2.0e8, 2.0e9, 45.0, 12.0),
    ("sh.600012", "2026-06-30", "2026-08-20", 20.0, 25.0, 6.0e8, 4.0e9, 35.0, -10.0),
    ("sh.600001", "2024-12-31", "2025-04-20", 5.0, 4.0, 2.0e7, 2.5e8, 42.0, 3.0),
    ("sh.600001", "2025-12-31", "2026-04-20", 6.0, 5.0, 1.0e7, 3.0e8, 40.0, 5.0),
    ("sh.600002", "2024-12-31", "2025-04-20", -8.0, -20.0, -3.0e7, 1.0e8, 80.0, -40.0),
    ("sh.600002", "2025-12-31", "2026-04-20", -12.0, -30.0, -5.0e7, 8.0e7, 85.0, -60.0),
    ("sh.600004", "2025-12-31", "2026-04-20", -2.0, -1.0, -1.0e6, 1.5e8, 55.0, -10.0),
)

#: (code, stat_date, pub_date, update_date, total_asset, net_asset)
EXPRESS = (
    ("sh.600001", "2026-06-30", "2026-08-20", "2026-08-20", 5.0e8, 3.0e8),
    ("sh.600002", "2026-06-30", "2026-08-20", "2026-08-20", 1.0e8, -2.0e7),
    ("sh.600004", "2026-06-30", "2026-08-20", "2026-08-20", 2.0e8, 5.0e7),
)

#: (code, stat_date, pub_date, forecast_type, abstract)
FORECAST = (("sh.600002", "2026-09-30", "2026-09-20", "预亏", "预计本期净利润为负"),)

INDUSTRY = {
    "sh.600001": "材料", "sh.600002": "化工", "sh.600004": "机械",
    "sz.000003": "电子", "sz.000005": "能源", "sh.600010": "医药",
    "sz.000011": "食品", "sh.600012": "电子",
}

INDUSTRY_DATE = "2026-09-22"


def _seed(db: MarketDb, st_override: dict[str, tuple[int, int, int]] | None = None) -> None:
    flags = {row[0]: row[2] for row in DAILY}
    if st_override:
        flags.update(st_override)
    names = {row[0]: row[1] for row in DAILY}
    daily_rows = []
    for code, _name, _st, close, turns, amounts in DAILY:
        st = flags[code]
        for idx, date in enumerate((D1, D2, D3)):
            daily_rows.append(
                (code, date, close, close, close, close, close,
                 amounts[idx], turns[idx], 1.0, st[idx])
            )
    with db.transact() as con:
        con.execute(
            "INSERT INTO data_source(source_id, name, manual_ref)"
            " VALUES ('baostock', 'BaoStock', NULL)"
        )
        con.executemany(
            "INSERT INTO sync_state(task_key, table_name, source_id, mode, schedule_desc,"
            " watermark, last_success_at, last_row_count, last_status)"
            " VALUES (?, ?, 'baostock', ?, ?, ?, ?, ?, 'ok')",
            [
                ("bs_k_daily", "k_line_daily", "incremental", "每日", D3,
                 LAST_SUCCESS, len(daily_rows)),
                ("bs_financial", "financial_quarter", "upsert_window", "季度", None,
                 LAST_SUCCESS, len(FIN)),
            ],
        )
        con.executemany(
            "INSERT INTO security(code, code_name, ipo_date, out_date, type, status)"
            " VALUES (?, ?, '2010-01-01', NULL, 1, 1)",
            [(code, names[code]) for code in names],
        )
        con.executemany(
            "INSERT INTO k_line_daily(code, trade_date, open, high, low, close, preclose,"
            " volume, amount, turn, trade_status, pct_chg, pe_ttm, pb_mrq, is_st)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 1000000, ?, ?, 1, ?, 12.0, 1.5, ?)",
            daily_rows,
        )
        con.executemany(
            "INSERT INTO financial_quarter(code, stat_date, pub_date, roe_avg, np_margin,"
            " net_profit, mb_revenue, liability_to_asset, yoy_ni)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            FIN,
        )
        con.executemany(
            "INSERT INTO performance_express(code, stat_date, pub_date, update_date,"
            " total_asset, net_asset) VALUES (?, ?, ?, ?, ?, ?)",
            EXPRESS,
        )
        con.executemany(
            "INSERT INTO profit_forecast(code, stat_date, pub_date, forecast_type, abstract)"
            " VALUES (?, ?, ?, ?, ?)",
            FORECAST,
        )
        con.executemany(
            "INSERT INTO stock_industry(code, update_date, code_name, industry,"
            " industry_classification) VALUES (?, ?, ?, ?, '申万一级行业')",
            [(code, INDUSTRY_DATE, names[code], industry) for code, industry in INDUSTRY.items()],
        )
        con.commit()


def _open(tmp_path: Path, *, st_override=None, init: bool = True, seed: bool = True):
    """建库（可选灌样本）→ ``(Store, MarketDb)``。"""
    store = Store.create(tmp_path / "root", PASS)
    db = MarketDb(store)
    if init:
        db.init_db()
    if seed:
        _seed(db, st_override)
    return store, db


def _packed(store: Store, db: MarketDb):
    """同一 Store 上装配注册表 + 运行器，并把公共认知 6 个执行器装上。"""
    registry = SkillRegistry(store)
    runner = SkillRunner(store, registry)
    install_official_pack(registry, runner, market_query=db, executors=EXECUTORS)
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


@pytest.fixture()
def packed(tmp_path: Path):
    store, db = _open(tmp_path)
    return _packed(store, db)


@pytest.fixture()
def registry(packed):
    return packed[0]


@pytest.fixture()
def runner(packed):
    return packed[1]


# ───────────────────────── GWT-1 六个执行器逐个产出 ─────────────────────────


class TestGwt1SixExecutors:
    def test_all_six_ok_and_schema_compliant(self, registry, runner):
        for base in COGNITION_BASES:
            skill_id = _skill_id(registry, base)
            out = _run(registry, runner, base)
            assert out.envelope.status == "ok", f"{base}: {out.envelope.reason}"
            check = check_payload(registry.get(skill_id).output_schema, out.envelope.data)
            assert check.passed, f"{base} 输出不合 output_schema：{check.describe()}"


# ───────────────────────── GWT-2 名单变更 diff ─────────────────────────


class TestGwt2ListDiff:
    def test_added_and_removed_both_directions(self, registry, runner):
        out = _run(registry, runner, "sk_st_list_sync")
        assert out.envelope.status == "ok"
        diff = out.envelope.data["diff"]
        assert diff["added"] == ["sh.600001", "sh.600004"]
        assert diff["removed"] == ["sz.000003", "sz.000005"]
        assert diff["counts"] == {"added": 2, "removed": 2, "total": 3}
        assert diff["as_of_date"] == D3
        assert diff["previous_date"] == D2

    def test_no_change_is_empty(self, tmp_path):
        store, db = _open(
            tmp_path,
            st_override={
                "sh.600001": (0, 1, 1),
                "sh.600004": (0, 1, 1),
                "sz.000003": (0, 0, 0),
                "sz.000005": (0, 0, 0),
            },
        )
        registry, runner = _packed(store, db)
        out = _run(registry, runner, "sk_st_list_sync")
        assert out.envelope.status == "empty"
        assert "无变更" in out.envelope.reason

    def test_exchange_without_st_rows_is_empty(self, registry, runner):
        out = _run(registry, runner, "sk_st_list_sync", {"exchange": "bj"})
        assert out.envelope.status == "empty"
        assert "无 ST 名单数据" in out.envelope.reason


# ───────────────────────── GWT-3 退市风险可解释 ─────────────────────────


class TestGwt3RiskScanExplainable:
    def test_triggers_carry_reason_detail_and_evidence(self, registry, runner):
        out = _run(registry, runner, "sk_delisting_risk_scan")
        assert out.envelope.status == "ok"
        data = out.envelope.data
        assert data["risk_level"] == "high"
        assert data["scanned"] == 3
        top = data["triggers"][0]
        assert top["code"] == "sh.600002"
        assert "面值风险" in top["reason"] and "盈利风险" in top["reason"]
        assert top["detail"] and "低于面值" in top["detail"]
        assert out.envelope.evidence_refs
        assert out.envelope.evidence_refs[0].kind == "dataset_snapshot_id"
        assert out.envelope.evidence_refs[0].ref.startswith("snap_")

    def test_threshold_moves_level_bar(self, registry, runner):
        default = _run(registry, runner, "sk_delisting_risk_scan")
        levels = {t["code"]: t["risk_level"] for t in default.envelope.data["triggers"]}
        assert levels["sh.600004"] == "low"  # 0.3 < 默认阈值 0.8/2

        relaxed = _run(registry, runner, "sk_delisting_risk_scan", {"threshold": 0.5})
        levels = {t["code"]: t["risk_level"] for t in relaxed.envelope.data["triggers"]}
        assert levels["sh.600004"] == "medium"  # 0.3 ≥ 0.5/2
        assert levels["sh.600002"] == "high"

    def test_scanned_but_no_trigger_is_empty(self, tmp_path):
        store, db = _open(tmp_path)
        with db.transact() as con:
            con.execute("DELETE FROM profit_forecast")
            con.execute("UPDATE k_line_daily SET close = 5.0 WHERE code = 'sh.600002'")
            con.execute(
                "UPDATE financial_quarter SET net_profit = 1.0e7, liability_to_asset = 40.0"
            )
            con.commit()
        registry, runner = _packed(store, db)
        out = _run(registry, runner, "sk_delisting_risk_scan")
        assert out.envelope.status == "empty"
        assert "未命中" in out.envelope.reason


# ───────────────────────── GWT-4 去拟人化 ─────────────────────────


class TestGwt4Neutrality:
    def test_all_six_outputs_pass_neutrality(self, registry, runner):
        guard = NeutralityGuard()
        for base in COGNITION_BASES:
            payload = _run(registry, runner, base).envelope.data
            verdict = guard.check_output(json.dumps(payload, ensure_ascii=False))
            assert verdict.passed, (
                f"{base} 输出命中拟人化：{[f.matched for f in verdict.findings]}"
            )


# ───────────────────────── GWT-5 数据面缺失 / 空结果 ─────────────────────────


class TestGwt5NoFabrication:
    def test_missing_db_is_unavailable(self, tmp_path):
        store, db = _open(tmp_path, init=False, seed=False)
        registry, runner = _packed(store, db)
        for base in COGNITION_BASES:
            out = _run(registry, runner, base)
            if base == "sk_delisting_risk_scan":
                # 该 Skill 声明了上游依赖；上游不可用 → 下游显式标注，不自行续跑
                assert out.envelope.status == "dependency_failed", out.envelope.reason
                assert "依赖失败" in out.envelope.reason
            else:
                assert out.envelope.status == "unavailable", f"{base}: {out.envelope.status}"
                assert out.envelope.last_updated_at is not None
            assert out.envelope.data is None

    def test_empty_db_is_empty(self, tmp_path):
        store, db = _open(tmp_path, seed=False)
        registry, runner = _packed(store, db)
        for base in COGNITION_BASES:
            out = _run(registry, runner, base)
            assert out.envelope.status == "empty", f"{base}: {out.envelope.status}"
            assert out.envelope.reason
            assert out.envelope.data is None
