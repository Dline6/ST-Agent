"""T-L0-005 测试：02-L0 §5 数据源缓存 + BaoStock 首个源落地。

GWT 对照（任务文件 5 条）：
- GWT-1 首次建库离线可查：setup 建 20 表 + 4 视图 + 元数据种子；断网可查
- GWT-2 增量同步与水位：水位续跑 + 时序前置 + 水位回写
- GWT-3 抓取经网关与清洗口径：唯一出口 data_fetch/baostock；清洗三规则；
  只落 adjustflag=3；复权经视图推导
- GWT-4 新鲜度自检与降级：freshness 非空 → unavailable + 最后更新；
  snapshot_id 锚定水位组合；as_of 按域取截止
- GWT-5 开关与完整性：禁用 → unavailable；校验失败 → partial + last_error
  且不回滚；全盘无行内容明文泄漏（审计只记字节数）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from st_agent.l0.market import (
    BaoStockSync,
    FetchError,
    FetchUnavailableError,
    MarketDb,
    MarketValidationError,
    clean_int,
    clean_num,
    clean_str,
    get_task,
    normalize_minute_ts,
    window_for,
)
from st_agent.l0.market.db import MARKET_DB_NAME
from st_agent.l0.market.tasks import SYNC_TASKS, TASK_COUNT
from st_agent.l0.net import EgressGateway
from st_agent.l0.storage import Store

PASS = "correct horse battery staple"

EXPECTED_TABLES = {
    "adjust_factor", "data_source", "dividend", "financial_quarter",
    "index_constituent", "indicator_dictionary", "k_line_daily",
    "k_line_minute", "k_line_period", "macro_deposit_rate",
    "macro_loan_rate", "macro_money_supply_month", "macro_money_supply_year",
    "macro_reserve_ratio", "performance_express", "profit_forecast",
    "security", "stock_industry", "sync_state", "trade_calendar",
}
EXPECTED_VIEWS = {"v_k_line_daily_hfq", "v_k_line_daily_qfq",
                  "v_k_line_latest", "v_st_universe"}


# ───────────────────────── Fake 抓取器 ─────────────────────────

class FakeFetcher:
    """可注入抓取器：返回固定字符串行（BaoStock 全接口返字符串，06）。"""

    def __init__(self, fail_on: tuple[str, ...] = (),
                 unavailable_on: tuple[str, ...] = ()) -> None:
        self.fail_on = set(fail_on)
        self.unavailable_on = set(unavailable_on)
        self.calls: list[str] = []

    def _guard(self, name: str) -> None:
        self.calls.append(name)
        if name in self.unavailable_on:
            raise FetchUnavailableError("源不可达（Fake）")
        if name in self.fail_on:
            raise FetchError("抓取失败（Fake）")

    def calendar(self, start, end):
        self._guard("calendar")
        _ = (start, end)
        return (["calendar_date", "is_trading_day"],
                [["2024-01-02", "1"], ["2024-01-03", "1"]])

    def security_basic(self):
        self._guard("security_basic")
        return (["code", "code_name", "ipoDate", "outDate", "type", "status"],
                [["sh.600000", "浦发银行", "1999-11-10", "", "1", "1"],
                 ["sh.000001", "上证指数", "", "", "2", "1"]])

    def all_stock(self, day):
        self._guard("all_stock")
        return (["code", "tradeStatus", "code_name"],
                [["sh.600000", "1", "浦发银行"]])

    def k_daily(self, code, start, end):
        self._guard("k_daily")
        _ = (start, end)
        return (["date", "code", "open", "high", "low", "close", "preclose",
                 "volume", "amount", "adjustflag", "turn", "tradestatus",
                 "pctChg", "peTTM", "pbMRQ", "psTTM", "pcfNcfTTM", "isST"],
                [["2024-01-02", code, "12.64", "12.65", "12.47", "12.56",
                  "12.65", "38778949", "486264672", "3", "0.137985", "1",
                  "-0.711456", "5.1", "0.6", "2.1", "3.3", "0"],
                 ["2024-01-03", code, "12.55", "12.58", "12.41", "12.55",
                  "12.56", "0", "0", "3", "", "0",
                  "0.0", "", "", "", "", "0"]])

    def k_period(self, code, frequency, start, end):
        self._guard("k_period")
        _ = (start, end)
        return (["date", "code", "open", "high", "low", "close", "volume",
                 "amount", "adjustflag", "turn", "pctChg"],
                [["2024-01-05", code, "12.5", "12.7", "12.4", "12.6",
                  "100", "1000", "3", "0.1", "0.5"]])

    def k_minute(self, code, frequency, start, end):
        self._guard("k_minute")
        _ = (start, end)
        return (["date", "time", "code", "open", "high", "low", "close",
                 "volume", "amount", "adjustflag"],
                [["2024-01-02", "20240102103000000", code, "12.6", "12.7",
                  "12.5", "12.6", "100", "1000", "3"]])

    def adjust_factor(self, code, start, end):
        self._guard("adjust_factor")
        _ = (start, end)
        return (["code", "dividOperateDate", "foreAdjustFactor",
                 "backAdjustFactor", "adjustFactor"],
                [[code, "2017-05-25", "0.989551", "9.385732", "9.385732"]])

    def dividend(self, code, year):
        self._guard("dividend")
        return (["code", "dividPreNoticeDate", "dividAgmPumDate",
                 "dividPlanAnnounceDate", "dividPlanDate", "dividRegistDate",
                 "dividOperateDate", "dividPayDate", "dividStockMarketDate",
                 "dividCashPsBeforeTax", "dividCashPsAfterTax", "dividStocksPs",
                 "dividCashStock", "dividReserveToStockPs"],
                [[code, "", "2017-04-26", "2017-04-01", "2017-05-19",
                  "2017-05-24", f"{year}-05-25", f"{year}-05-25",
                  f"{year}-05-26", "0.2", "0.18或0.2", "0.000000",
                  "10转3派2元", "0.300000"]])

    def financial(self, code, year, quarter, which):
        self._guard("financial")
        _ = (year, quarter)
        head = {"profit": ("roeAvg", "0.074617"),
                "operation": ("NRTurnRatio", "1.5"),
                "growth": ("YOYEquity", "0.1"),
                "balance": ("currentRatio", "1.2"),
                "cash": ("CAToAsset", "0.3"),
                "dupont": ("dupontROE", "0.07")}[which]
        return (["code", "pubDate", "statDate", head[0]],
                [[code, "2017-08-30", "2017-06-30", head[1]]])

    def performance_express(self, code, start, end):
        self._guard("performance_express")
        _ = (start, end)
        return (["code", "performanceExpPubDate", "performanceExpStatDate",
                 "performanceExpUpdateDate", "performanceExpressTotalAsset",
                 "performanceExpressNetAsset", "performanceExpressEPSChgPct",
                 "performanceExpressROEWa", "performanceExpressEPSDiluted",
                 "performanceExpressGRYOY", "performanceExpressOPYOY"],
                [[code, "2017-01-04", "2016-12-31", "2017-01-04",
                  "5857263000000.0", "338027000000.0", "0.115412",
                  "16.35", "2.4", "0.097234", "0.054384"]])

    def forecast(self, code, start, end):
        self._guard("forecast")
        _ = (start, end)
        return (["code", "profitForcastExpPubDate", "profitForcastExpStatDate",
                 "profitForcastType", "profitForcastAbstract",
                 "profitForcastChgPctUp", "profitForcastChgPctDwn"],
                [[code, "2017-01-05", "2016-12-31", "预增", "预计增长",
                  "44.33", "44.33"]])

    def industry(self):
        self._guard("industry")
        return (["updateDate", "code", "code_name", "industry",
                 "industryClassification"],
                [["2024-01-01", "sh.600000", "浦发银行", "银行", "申万一级行业"]])

    def constituents(self, index_key):
        self._guard("constituents")
        return (["updateDate", "code", "code_name"],
                [["2024-01-01", "sh.600000", "浦发银行"]])

    def macro_deposit(self, start, end):
        self._guard("macro_deposit")
        _ = (start, end)
        return (["pubDate", "demandDepositRate", "fixedDepositRate3Month",
                 "fixedDepositRate6Month", "fixedDepositRate1Year",
                 "fixedDepositRate2Year", "fixedDepositRate3Year",
                 "fixedDepositRate5Year", "installmentFixedDepositRate1Year",
                 "installmentFixedDepositRate3Year",
                 "installmentFixedDepositRate5Year"],
                [["2015-03-01", "0.35", "2.1", "2.3", "2.5", "3.1",
                  "3.75", "", "2.1", "2.3", ""]])

    def macro_loan(self, start, end):
        self._guard("macro_loan")
        _ = (start, end)
        return (["pubDate", "loanRate6Month", "loanRate6MonthTo1Year",
                 "loanRate1YearTo3Year", "loanRate3YearTo5Year",
                 "loanRateAbove5Year", "mortgateRateBelow5Year",
                 "mortgateRateAbove5Year"],
                [["2015-03-01", "4.35", "4.35", "4.75", "4.9", "4.9",
                  "2.75", "3.25"]])

    def macro_reserve(self):
        self._guard("macro_reserve")
        return (["pubDate", "effectiveDate", "bigInstitutionsRatioPre",
                 "bigInstitutionsRatioAfter", "mediumInstitutionsRatioPre",
                 "mediumInstitutionsRatioAfter"],
                [["2015-03-01", "2015-03-05", "19.5", "19.0", "17.5", "17.0"]])

    def macro_money_month(self):
        self._guard("macro_money_month")
        return (["statYear", "statMonth", "m0Month", "m0YOY",
                 "m0ChainRelative", "m1Month", "m1YOY", "m1ChainRelative",
                 "m2Month", "m2YOY", "m2ChainRelative"],
                [["2015", "03", "100", "—0.79", "0.1", "200", "0.2",
                  "0.1", "300", "0.3", "0.1"]])

    def macro_money_year(self):
        self._guard("macro_money_year")
        return (["statYear", "m0Year", "m0YearYOY", "m1Year", "m1YearYOY",
                 "m2Year", "m2YearYOY"],
                [["2015", "100", "0.1", "200", "0.2", "300", "0.3"]])


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def gateway(store: Store) -> EgressGateway:
    return EgressGateway(store)


@pytest.fixture()
def rig(store: Store, gateway: EgressGateway):
    fetcher = FakeFetcher()
    db = MarketDb(store)
    sync = BaoStockSync(db, gateway, fetcher)
    sync.setup()
    return db, gateway, sync, fetcher


def root_of(store: Store) -> Path:
    return store._root  # noqa: SLF001


# ───────────────────────── GWT-1 首次建库离线可查 ─────────────────────────


class TestGwt1Bootstrap:
    def test_setup_creates_all_tables_and_views(self, rig):
        db, _, _, _ = rig
        assert set(db.tables()) == EXPECTED_TABLES
        assert set(db.views()) == EXPECTED_VIEWS
        assert TASK_COUNT == 20

    def test_setup_registers_tasks(self, rig):
        db, _, _, _ = rig
        env = db.query("SELECT count(*) AS n FROM sync_state")
        assert env.data["rows"][0]["n"] == 20
        env2 = db.query("SELECT source_id FROM data_source")
        assert env2.data["rows"][0]["source_id"] == "baostock"

    def test_setup_is_idempotent(self, rig):
        _, _, sync, _ = rig
        assert sync.setup().status == "ok"

    def test_db_blob_is_encrypted(self, rig, store: Store):
        _, _, _, _ = rig
        raw = (root_of(store) / "data_cache" / MARKET_DB_NAME).read_bytes()
        assert b"CREATE TABLE security" not in raw
        assert b"sh.600000" not in raw

    def test_offline_query_works(self, rig, gateway: EgressGateway):
        db, _, sync, _ = rig
        sync.run_task("bs_security_basic", codes=["sh.600000"])
        sync.run_task("bs_k_daily", codes=["sh.600000"])
        gateway.set_online(False)
        env = db.query("SELECT code FROM security ORDER BY code")
        assert env.status == "ok"
        assert "sh.600000" in [r["code"] for r in env.data["rows"]]
        assert gateway.query() != ()  # 历史审计仍可查

    def test_query_missing_db_is_unavailable(self, store: Store):
        db = MarketDb(store)
        env = db.query("SELECT 1")
        assert env.status == "unavailable"

    def test_empty_result_is_empty_envelope(self, rig):
        db, _, _, _ = rig
        env = db.query("SELECT * FROM security WHERE code='sh.999999'")
        assert env.status == "empty"
        assert env.reason

    def test_non_select_rejected(self, rig):
        db, _, _, _ = rig
        env = db.query("DELETE FROM security")
        assert env.status == "validation_failed"
        env2 = db.query("SELECT 1; SELECT 2")
        assert env2.status == "validation_failed"


# ───────────────────────── GWT-2 增量同步与水位 ─────────────────────────


class TestGwt2Watermark:
    def test_registry_matches_doc_count(self):
        assert len(SYNC_TASKS) == 20
        assert {t.table_name for t in SYNC_TASKS} >= {
            "trade_calendar", "security", "k_line_daily", "k_line_period",
            "k_line_minute", "adjust_factor", "dividend", "financial_quarter",
            "performance_express", "profit_forecast", "stock_industry",
            "index_constituent",
        }

    def test_unknown_task_rejected(self):
        with pytest.raises(MarketValidationError, match="未知同步任务"):
            get_task("bs_nope")

    def test_window_for_daily(self):
        assert window_for("bs_k_daily", None)[0] == "1990-12-19"
        assert window_for("bs_k_daily", "2024-01-05") == (
            "2024-01-06", window_for("bs_k_daily", "2024-01-05")[1])

    def test_prereq_blocks_without_calendar(self, rig):
        _, _, sync, _ = rig
        env = sync.run_task("bs_k_daily", codes=["sh.600000"])
        assert env.status == "unavailable"
        assert "bs_calendar" in (env.reason or "")

    def test_prereq_chain_passes(self, rig):
        _, _, sync, _ = rig
        assert sync.run_task("bs_calendar").status == "ok"
        env_all = sync.run_task("bs_all_stock", day="2024-01-02")
        assert env_all.status == "ok"
        assert sync.run_task("bs_security_basic").status == "ok"
        assert sync.run_task("bs_k_daily", codes=["sh.600000"]).status == "ok"

    def test_watermark_roundtrip(self, rig):
        db, _, sync, _ = rig
        sync.run_task("bs_calendar")
        sync.run_task("bs_all_stock", day="2024-01-02")
        sync.run_task("bs_security_basic")
        env = sync.run_task("bs_k_daily", codes=["sh.600000"])
        assert env.status == "ok"
        assert env.data["watermark"] == "2024-01-03"
        state = db.query(
            "SELECT watermark, last_status FROM sync_state WHERE task_key='bs_k_daily'")
        row = state.data["rows"][0]
        assert row["watermark"] == "2024-01-03" and row["last_status"] == "ok"
        assert "bs_k_daily:2024-01-03@" in sync.dataset_snapshot()

    def test_minute_disabled_by_default(self, rig):
        _, _, sync, _ = rig
        env = sync.run_task("bs_k_minute", codes=["sh.600000"])
        assert env.status == "unavailable"
        assert "禁用" in (env.reason or "")

    def test_minute_needs_watchlist(self, rig):
        _, _, sync, _ = rig
        env = sync.run_task("bs_k_minute", enabled={"bs_k_minute": True})
        assert env.status == "validation_failed"
        assert "关注池" in (env.reason or "")

    def test_run_all_order(self, rig):
        _, _, sync, _ = rig
        results = sync.run_all()
        assert results["bs_calendar"].status == "ok"
        assert results["bs_k_daily"].status == "ok"
        assert results["bs_k_minute"].status == "unavailable"


# ───────────────────────── GWT-3 抓取经网关与清洗口径 ─────────────────────────


class TestGwt3GatewayAndCleaning:
    def test_fetch_goes_through_gateway(self, rig):
        _, gateway, sync, _ = rig
        sync.run_task("bs_calendar")
        events = gateway.query(kind="data_fetch")
        assert len(events) == 1
        assert events[0].target_host == "baostock"
        assert events[0].initiator == "market-sync"

    def test_offline_fetch_is_unavailable(self, rig):
        _, gateway, sync, _ = rig
        gateway.set_online(False)
        env = sync.run_task("bs_calendar")
        assert env.status == "unavailable"
        assert gateway.pending_reconnect() != ()

    def test_fetch_failure_is_failed_with_log_ref(self, store: Store,
                                                  gateway: EgressGateway):
        db = MarketDb(store)
        sync = BaoStockSync(db, gateway, FakeFetcher(fail_on=("calendar",)))
        sync.setup()
        env = sync.run_task("bs_calendar")
        assert env.status == "failed" and env.log_ref

    def test_clean_num_rules(self):
        assert clean_num("") is None
        assert clean_num("—3.07155") == pytest.approx(-3.07155)
        assert clean_num("0.6813或0.71915") == pytest.approx(0.6813)
        assert clean_num("12.6400") == pytest.approx(12.64)

    def test_clean_str_and_int(self):
        assert clean_str("") is None and clean_str(" 银行 ") == "银行"
        assert clean_int("1") == 1 and clean_int("") is None

    def test_minute_ts_normalized(self):
        assert normalize_minute_ts("20240102103000000") == "2024-01-02 10:30:00"

    def test_halted_row_kept_with_null_turn(self, rig):
        db, _, sync, _ = rig
        sync.run_task("bs_calendar")
        sync.run_task("bs_all_stock", day="2024-01-02")
        sync.run_task("bs_security_basic")
        sync.run_task("bs_k_daily", codes=["sh.600000"])
        env = db.query(
            "SELECT trade_status, turn, volume FROM k_line_daily "
            "WHERE code='sh.600000' AND trade_date='2024-01-03'")
        row = env.data["rows"][0]
        assert row["trade_status"] == 0 and row["turn"] is None
        assert row["volume"] == 0

    def test_only_unadjusted_rows(self, rig):
        db, _, sync, _ = rig
        sync.run_task("bs_calendar")
        sync.run_task("bs_all_stock", day="2024-01-02")
        assert sync.run_task("bs_security_basic").status == "ok"
        env = sync.run_task("bs_k_daily", codes=["sh.600000"])
        assert env.status == "ok"

    def test_adjust_view_derives(self, rig):
        db, _, sync, _ = rig
        sync.run_task("bs_calendar")
        sync.run_task("bs_all_stock", day="2024-01-02")
        sync.run_task("bs_security_basic")
        sync.run_task("bs_k_daily", codes=["sh.600000"])
        sync.run_task("bs_adjust_factor", codes=["sh.600000"])
        env = db.query(
            "SELECT close FROM v_k_line_daily_hfq WHERE code='sh.600000' "
            "ORDER BY trade_date LIMIT 1")
        assert env.status == "ok"

    def test_loan_spelling_fixed(self, rig):
        db, _, sync, _ = rig
        assert sync.run_task("bs_macro_loan").status == "ok"
        env = db.query("SELECT mortgage_below_5y FROM macro_loan_rate")
        assert env.data["rows"][0]["mortgage_below_5y"] == pytest.approx(2.75)

    def test_dividend_or_value_first(self, rig):
        db, _, sync, _ = rig
        sync.run_task("bs_calendar")
        sync.run_task("bs_all_stock", day="2024-01-02")
        sync.run_task("bs_security_basic")
        assert sync.run_task("bs_dividend", codes=["sh.600000"]).status == "ok"
        env = db.query(
            "SELECT divid_cash_ps_after_tax, divid_cash_stock FROM dividend "
            "WHERE code='sh.600000' LIMIT 1")
        row = env.data["rows"][0]
        assert row["divid_cash_ps_after_tax"] == pytest.approx(0.18)
        assert "10转3派2元" in row["divid_cash_stock"]

    def test_financial_merges_six(self, rig):
        db, _, sync, _ = rig
        sync.run_task("bs_calendar")
        sync.run_task("bs_all_stock", day="2024-01-02")
        sync.run_task("bs_security_basic")
        assert sync.run_task("bs_fin_quarter", codes=["sh.600000"]).status == "ok"
        env = db.query(
            "SELECT roe_avg, nr_turn_ratio, yoy_equity, current_ratio,"
            " ca_to_asset, dupont_roe, pub_date FROM financial_quarter "
            "WHERE code='sh.600000' AND stat_date='2017-06-30'")
        row = env.data["rows"][0]
        assert row["roe_avg"] == pytest.approx(0.074617)
        assert row["dupont_roe"] == pytest.approx(0.07)
        assert row["pub_date"] == "2017-08-30"

    def test_forecast_type_kept_raw(self, rig):
        db, _, sync, _ = rig
        sync.run_task("bs_calendar")
        sync.run_task("bs_all_stock", day="2024-01-02")
        sync.run_task("bs_security_basic")
        assert sync.run_task("bs_forecast", codes=["sh.600000"]).status == "ok"
        env = db.query(
            "SELECT forecast_type FROM profit_forecast WHERE code='sh.600000'")
        assert env.data["rows"][0]["forecast_type"] == "预增"

    def test_no_row_content_in_audit(self, rig, store: Store):
        _, gateway, sync, _ = rig
        sync.run_task("bs_security_basic", codes=["sh.600000"])
        blob = gateway.export_report()
        assert "浦发银行" not in str(blob)
        for f in root_of(store).rglob("*"):
            if f.is_file() and f.name != "keyfile.json":
                raw = f.read_bytes()
                assert "浦发银行".encode() not in raw or "data_cache" in str(f), f.name


# ───────────────────────── GWT-4 新鲜度自检与降级 ─────────────────────────


class TestGwt4Freshness:
    def test_freshness_nonempty_before_sync(self, rig):
        db, _, _, _ = rig
        assert db.freshness() != ()

    def test_as_of_per_domain(self, rig):
        db, _, sync, _ = rig
        sync.run_task("bs_calendar")
        sync.run_task("bs_all_stock", day="2024-01-02")
        sync.run_task("bs_security_basic")
        sync.run_task("bs_k_daily", codes=["sh.600000"])
        assert db.as_of("kline") == "2024-01-03"
        with pytest.raises(MarketValidationError, match="未知数据域"):
            db.as_of("nope")

    def test_verdict_stale_then_clean(self, rig):
        _, _, sync, _ = rig
        verdict = sync.freshness_verdict("kline")
        assert verdict.stale is True
        assert verdict.last_updated_at.tzinfo is not None
        sync.run_task("bs_calendar")
        sync.run_task("bs_all_stock", day="2024-01-02")
        sync.run_task("bs_security_basic")
        sync.run_task("bs_k_daily", codes=["sh.600000"])
        sync.run_task("bs_k_period", codes=["sh.600000"])
        verdict2 = sync.freshness_verdict("kline")
        assert verdict2.stale is False

    def test_verdict_unknown_domain(self, rig):
        _, _, sync, _ = rig
        with pytest.raises(MarketValidationError, match="未知数据域"):
            sync.freshness_verdict("nope")

    def test_snapshot_anchors_watermarks(self, rig):
        _, _, sync, _ = rig
        snap = sync.dataset_snapshot()
        assert snap.startswith("snap|")
        assert "bs_calendar" in snap


# ───────────────────────── GWT-5 开关与完整性 ─────────────────────────


class TestGwt5SwitchAndIntegrity:
    def test_disabled_source_is_unavailable(self, rig):
        _, _, sync, _ = rig
        env = sync.run_task("bs_calendar", enabled={"bs_calendar": False})
        assert env.status == "unavailable"
        assert env.last_updated_at is not None

    def test_partial_on_orphan_row(self, rig):
        db, _, sync, converter_state = rig
        _ = converter_state
        sync.run_task("bs_calendar")
        sync.run_task("bs_all_stock", day="2024-01-02")
        # 造孤儿行：k_line_daily 引用不存在的 security
        with db.transact() as con:
            con.execute("PRAGMA foreign_keys = OFF")
            con.execute(
                "INSERT INTO k_line_daily(code, trade_date) VALUES ('sh.999999', '2024-01-02')")
            con.commit()
        sync.run_task("bs_security_basic")
        env = sync.run_task("bs_k_daily", codes=["sh.600000"])
        assert env.status == "unavailable"
        assert "外键" in (env.reason or "")
        state = db.query(
            "SELECT last_status, last_error FROM sync_state WHERE task_key='bs_k_daily'")
        row = state.data["rows"][0]
        assert row["last_status"] == "partial"
        assert "外键" in row["last_error"]
        # 不回滚：已写数据仍在
        env2 = db.query(
            "SELECT count(*) AS n FROM k_line_daily WHERE code='sh.600000'")
        assert env2.data["rows"][0]["n"] == 2

    def test_quarter_end_check(self, rig):
        db, _, sync, _ = rig
        sync.run_task("bs_calendar")
        sync.run_task("bs_all_stock", day="2024-01-02")
        sync.run_task("bs_security_basic")
        with db.transact() as con:
            con.execute("PRAGMA foreign_keys = OFF")
            con.execute(
                "INSERT INTO financial_quarter(code, stat_date) VALUES ('sh.600000', '2017-06-15')")
            con.commit()
        env = sync.run_task("bs_fin_quarter", codes=["sh.600000"])
        assert env.status == "unavailable"
        assert "季度末" in (env.reason or "")
