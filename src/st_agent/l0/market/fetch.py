"""抓取器协议 + 入库映射（数据库设计 06-API映射契约 的代码侧实现）。

抓取器协议（``baostock`` 包不内置——依赖倒置）：
- 真实抓取器由管道组装（``login → 批量查询 → logout``，05 初始化顺序），
  本模块只定义协议形状；测试与离线路径注入 ``FakeFetcher``
- 协议方法返回 ``(fields, rows)``：``fields`` 为 API 原始字段名列表，
  ``rows`` 为字符串行（BaoStock 全接口返回字符串，06 通用约定）
- ``fetch_error`` / ``fetch_unavailable`` 由同步引擎翻译为网关 ``Egress*``
  体系（02 §6 唯一出口）：不可达 → ``EgressUnavailableError`` → 信封
  ``unavailable``；其余失败 → ``EgressError`` → 信封 ``failed``

入库映射（06 逐接口 字段→列）：
- ``map_calendar`` / ``map_security_basic`` / ``map_all_stock``
- ``map_k_daily`` / ``map_k_period`` / ``map_k_minute``
  （固定 ``adjustflag='3'`` 由抓取器保证；此处只做清洗 + 列映射）
- ``map_adjust_factor`` / ``map_dividend``
  （``或``多值取首值；``divid_operate_date`` 为 NULL 的行返回 ``needs_lookup``
  标记，由引擎按 ``(code, divid_plan_announce_date)`` 比对去重）
- ``map_financial``（六接口按 ``(code, stat_date)`` 合并；``pub_date`` 取最大）
- ``map_performance_express`` / ``map_forecast``
- ``map_industry`` / ``map_constituent``（``index_key`` 由调用方写入）
- ``map_macro_*`` × 5（含 ``mortgate`` → ``mortgage`` 拼写修正、
  月份两位转 INTEGER）
"""

from __future__ import annotations

from typing import Any, Protocol

from st_agent.l0.market.cleaning import (
    clean_int,
    clean_num,
    clean_str,
    normalize_date,
    normalize_minute_ts,
)

__all__ = [
    "FIN_QUARTER_COLUMNS",
    "Fetcher",
    "FetchResult",
    "map_adjust_factor",
    "map_all_stock",
    "map_calendar",
    "map_constituent",
    "map_dividend",
    "map_financial",
    "map_forecast",
    "map_industry",
    "map_k_daily",
    "map_k_minute",
    "map_k_period",
    "map_macro_deposit",
    "map_macro_loan",
    "map_macro_money_month",
    "map_macro_money_year",
    "map_macro_reserve",
    "map_performance_express",
    "map_security_basic",
]

FetchResult = tuple[list[str], list[list[Any]]]
"""抓取器返回：(API 原始字段名, 字符串行)。"""


class Fetcher(Protocol):
    """BaoStock 抓取器协议（真实实现由管道注入；测试注入 Fake）。

    方法名 = 同步任务的源接口（06 文档口径）；参数为各任务窗口推导结果。
    任一方法抛 ``FetchUnavailableError``（不可达）或 ``FetchError``（失败）。
    """

    def calendar(self, start: str, end: str) -> FetchResult: ...
    def security_basic(self) -> FetchResult: ...
    def all_stock(self, day: str) -> FetchResult: ...
    def k_daily(self, code: str, start: str, end: str) -> FetchResult: ...
    def k_period(self, code: str, frequency: str, start: str, end: str) -> FetchResult: ...
    def k_minute(self, code: str, frequency: str, start: str, end: str) -> FetchResult: ...
    def adjust_factor(self, code: str, start: str, end: str) -> FetchResult: ...
    def dividend(self, code: str, year: str) -> FetchResult: ...
    def financial(self, code: str, year: int, quarter: int,
                  which: str) -> FetchResult: ...
    def performance_express(self, code: str, start: str, end: str) -> FetchResult: ...
    def forecast(self, code: str, start: str, end: str) -> FetchResult: ...
    def industry(self) -> FetchResult: ...
    def constituents(self, index_key: str) -> FetchResult: ...
    def macro_deposit(self, start: str, end: str) -> FetchResult: ...
    def macro_loan(self, start: str, end: str) -> FetchResult: ...
    def macro_reserve(self) -> FetchResult: ...
    def macro_money_month(self) -> FetchResult: ...
    def macro_money_year(self) -> FetchResult: ...


def _col(rows: list[list[Any]], fields: list[str], row: list[Any], name: str) -> Any:
    _ = rows
    try:
        return row[fields.index(name)]
    except ValueError:
        return None


# ───────────────────────── 元信息类 ─────────────────────────

def map_calendar(fields: list[str], rows: list[list[Any]]) -> list[tuple]:
    """``query_trade_dates()`` → ``trade_calendar`` 行（06：字符串转 INTEGER）。"""
    return [
        (normalize_date(_col(rows, fields, r, "calendar_date")),
         clean_int(_col(rows, fields, r, "is_trading_day")))
        for r in rows
    ]


def map_security_basic(fields: list[str], rows: list[list[Any]]) -> list[tuple]:
    """``query_stock_basic()`` → ``security`` 行（空串→NULL，类型转 INTEGER）。"""
    return [
        (clean_str(_col(rows, fields, r, "code")),
         clean_str(_col(rows, fields, r, "code_name")),
         normalize_date(_col(rows, fields, r, "ipoDate")),
         normalize_date(_col(rows, fields, r, "outDate")),
         clean_int(_col(rows, fields, r, "type")),
         clean_int(_col(rows, fields, r, "status")))
        for r in rows
    ]


def map_all_stock(fields: list[str], rows: list[list[Any]]) -> list[tuple]:
    """``query_all_stock(day)`` → ``security`` UPSERT 行（仅 code/code_name）。

    ``tradeStatus`` 为当日快照不落库（历史快照由 ``k_line_daily`` 承载，06）。
    返回 ``(code, code_name)``；调用方按行计数 ``last_row_count``（覆盖对账）。
    """
    return [
        (clean_str(_col(rows, fields, r, "code")),
         clean_str(_col(rows, fields, r, "code_name")))
        for r in rows
    ]


# ───────────────────────── 行情类 ─────────────────────────

def map_k_daily(fields: list[str], rows: list[list[Any]]) -> list[tuple]:
    """K 线日线 → ``k_line_daily`` 行（停牌行照落，``turn`` 空→NULL，06）。"""
    return [
        (clean_str(_col(rows, fields, r, "code")),
         normalize_date(_col(rows, fields, r, "date")),
         clean_num(_col(rows, fields, r, "open")),
         clean_num(_col(rows, fields, r, "high")),
         clean_num(_col(rows, fields, r, "low")),
         clean_num(_col(rows, fields, r, "close")),
         clean_num(_col(rows, fields, r, "preclose")),
         clean_int(_col(rows, fields, r, "volume")),
         clean_num(_col(rows, fields, r, "amount")),
         clean_num(_col(rows, fields, r, "turn")),
         clean_int(_col(rows, fields, r, "tradestatus")),
         clean_num(_col(rows, fields, r, "pctChg")),
         clean_num(_col(rows, fields, r, "peTTM")),
         clean_num(_col(rows, fields, r, "pbMRQ")),
         clean_num(_col(rows, fields, r, "psTTM")),
         clean_num(_col(rows, fields, r, "pcfNcfTTM")),
         clean_int(_col(rows, fields, r, "isST")))
        for r in rows
    ]


def map_k_period(fields: list[str], rows: list[list[Any]], frequency: str) -> list[tuple]:
    """周/月线 → ``k_line_period`` 行（``frequency`` 由调用方写入）。"""
    return [
        (clean_str(_col(rows, fields, r, "code")), frequency,
         normalize_date(_col(rows, fields, r, "date")),
         clean_num(_col(rows, fields, r, "open")),
         clean_num(_col(rows, fields, r, "high")),
         clean_num(_col(rows, fields, r, "low")),
         clean_num(_col(rows, fields, r, "close")),
         clean_int(_col(rows, fields, r, "volume")),
         clean_num(_col(rows, fields, r, "amount")),
         clean_num(_col(rows, fields, r, "turn")),
         clean_num(_col(rows, fields, r, "pctChg")))
        for r in rows
    ]


def map_k_minute(fields: list[str], rows: list[list[Any]], frequency: str) -> list[tuple]:
    """分钟线 → ``k_line_minute`` 行（``time`` 归一化，06）。"""
    return [
        (clean_str(_col(rows, fields, r, "code")), frequency,
         normalize_minute_ts(_col(rows, fields, r, "time")),
         clean_num(_col(rows, fields, r, "open")),
         clean_num(_col(rows, fields, r, "high")),
         clean_num(_col(rows, fields, r, "low")),
         clean_num(_col(rows, fields, r, "close")),
         clean_int(_col(rows, fields, r, "volume")),
         clean_num(_col(rows, fields, r, "amount")))
        for r in rows
    ]


def map_adjust_factor(fields: list[str], rows: list[list[Any]]) -> list[tuple]:
    """``query_adjust_factor()`` → ``adjust_factor`` 行。"""
    return [
        (clean_str(_col(rows, fields, r, "code")),
         normalize_date(_col(rows, fields, r, "dividOperateDate")),
         clean_num(_col(rows, fields, r, "foreAdjustFactor")),
         clean_num(_col(rows, fields, r, "backAdjustFactor")),
         clean_num(_col(rows, fields, r, "adjustFactor")))
        for r in rows
    ]


def map_dividend(fields: list[str], rows: list[list[Any]]) -> list[tuple]:
    """``query_dividend_data()`` → ``dividend`` 行（14 列，无 ``dividend_id``）。

    ``divid_operate_date`` 为 NULL 的行——冲突键缺失，调用方须「先查后插」
    （按 ``(code, divid_plan_announce_date)`` 比对去重，06）。
    """
    return [
        (clean_str(_col(rows, fields, r, "code")),
         normalize_date(_col(rows, fields, r, "dividPreNoticeDate")),
         normalize_date(_col(rows, fields, r, "dividAgmPumDate")),
         normalize_date(_col(rows, fields, r, "dividPlanAnnounceDate")),
         normalize_date(_col(rows, fields, r, "dividPlanDate")),
         normalize_date(_col(rows, fields, r, "dividRegistDate")),
         normalize_date(_col(rows, fields, r, "dividOperateDate")),
         normalize_date(_col(rows, fields, r, "dividPayDate")),
         normalize_date(_col(rows, fields, r, "dividStockMarketDate")),
         clean_num(_col(rows, fields, r, "dividCashPsBeforeTax")),
         clean_num(_col(rows, fields, r, "dividCashPsAfterTax")),
         clean_num(_col(rows, fields, r, "dividStocksPs")),
         clean_str(_col(rows, fields, r, "dividCashStock")),
         clean_num(_col(rows, fields, r, "dividReserveToStockPs")))
        for r in rows
    ]


# ───────────────────────── 财务类（六接口合并） ─────────────────────────

FIN_QUARTER_COLUMNS: tuple[str, ...] = (
    "code", "stat_date", "pub_date",
    "roe_avg", "np_margin", "gp_margin", "net_profit", "eps_ttm",
    "mb_revenue", "total_share", "liqa_share",
    "nr_turn_ratio", "nr_turn_days", "inv_turn_ratio", "inv_turn_days",
    "ca_turn_ratio", "asset_turn_ratio",
    "yoy_equity", "yoy_asset", "yoy_ni", "yoy_eps_basic", "yoy_pni",
    "current_ratio", "quick_ratio", "cash_ratio", "yoy_liability",
    "liability_to_asset", "asset_to_equity",
    "ca_to_asset", "nca_to_asset", "tangible_asset_to_asset",
    "ebit_to_interest", "cfo_to_or", "cfo_to_np", "cfo_to_gr",
    "dupont_roe", "dupont_asset_sto_equity", "dupont_asset_turn",
    "dupont_pnitoni", "dupont_nitogr", "dupont_tax_burden",
    "dupont_intburden", "dupont_ebittogr",
)

_FIN_FIELD_MAP: dict[str, tuple[str, str]] = {
    # which → (API 字段, 目标列)，code/stat_date/pubDate 另行处理
    "profit": ("roeAvg", "roe_avg"),
    "profit_npMargin": ("npMargin", "np_margin"),
    "profit_gpMargin": ("gpMargin", "gp_margin"),
    "profit_netProfit": ("netProfit", "net_profit"),
    "profit_epsTTM": ("epsTTM", "eps_ttm"),
    "profit_MBRevenue": ("MBRevenue", "mb_revenue"),
    "profit_totalShare": ("totalShare", "total_share"),
    "profit_liqaShare": ("liqaShare", "liqa_share"),
    "operation_NRTurnRatio": ("NRTurnRatio", "nr_turn_ratio"),
    "operation_NRTurnDays": ("NRTurnDays", "nr_turn_days"),
    "operation_INVTurnRatio": ("INVTurnRatio", "inv_turn_ratio"),
    "operation_INVTurnDays": ("INVTurnDays", "inv_turn_days"),
    "operation_CATurnRatio": ("CATurnRatio", "ca_turn_ratio"),
    "operation_AssetTurnRatio": ("AssetTurnRatio", "asset_turn_ratio"),
    "growth_YOYEquity": ("YOYEquity", "yoy_equity"),
    "growth_YOYAsset": ("YOYAsset", "yoy_asset"),
    "growth_YOYNI": ("YOYNI", "yoy_ni"),
    "growth_YOYEPSBasic": ("YOYEPSBasic", "yoy_eps_basic"),
    "growth_YOYPNI": ("YOYPNI", "yoy_pni"),
    "balance_currentRatio": ("currentRatio", "current_ratio"),
    "balance_quickRatio": ("quickRatio", "quick_ratio"),
    "balance_cashRatio": ("cashRatio", "cash_ratio"),
    "balance_YOYLiability": ("YOYLiability", "yoy_liability"),
    "balance_liabilityToAsset": ("liabilityToAsset", "liability_to_asset"),
    "balance_assetToEquity": ("assetToEquity", "asset_to_equity"),
    "cash_CAToAsset": ("CAToAsset", "ca_to_asset"),
    "cash_NCAToAsset": ("NCAToAsset", "nca_to_asset"),
    "cash_tangibleAssetToAsset": ("tangibleAssetToAsset", "tangible_asset_to_asset"),
    "cash_ebitToInterest": ("ebitToInterest", "ebit_to_interest"),
    "cash_CFOToOR": ("CFOToOR", "cfo_to_or"),
    "cash_CFOToNP": ("CFOToNP", "cfo_to_np"),
    "cash_CFOToGr": ("CFOToGr", "cfo_to_gr"),
    "dupont_dupontROE": ("dupontROE", "dupont_roe"),
    "dupont_dupontAssetStoEquity": ("dupontAssetStoEquity", "dupont_asset_sto_equity"),
    "dupont_dupontAssetTurn": ("dupontAssetTurn", "dupont_asset_turn"),
    "dupont_dupontPnitoni": ("dupontPnitoni", "dupont_pnitoni"),
    "dupont_dupontNitogr": ("dupontNitogr", "dupont_nitogr"),
    "dupont_dupontTaxBurden": ("dupontTaxBurden", "dupont_tax_burden"),
    "dupont_dupontIntburden": ("dupontIntburden", "dupont_intburden"),
    "dupont_dupontEbittogr": ("dupontEbittogr", "dupont_ebittogr"),
}

_FIN_WHICH = ("profit", "operation", "growth", "balance", "cash", "dupont")


def map_financial(which: str, fields: list[str],
                  rows: list[list[Any]]) -> list[dict[str, Any]]:
    """单个财务接口 → 部分列字典（``(code, stat_date)`` 键 + 该接口列）。

    六接口结果由引擎按 ``(code, stat_date)`` 合并 UPSERT；``pub_date`` 取
    六接口最大值（03 合并契约）。``which`` 非法 → ``MarketValidationError``。
    """
    from st_agent.l0.market.errors import MarketValidationError

    if which not in _FIN_WHICH:
        raise MarketValidationError(
            f"未知财务接口 {which!r}；合法 = {list(_FIN_WHICH)}"
        )
    out: list[dict[str, Any]] = []
    for row in rows:
        record: dict[str, Any] = {
            "code": clean_str(_col(rows, fields, row, "code")),
            "stat_date": normalize_date(_col(rows, fields, row, "statDate")),
            "pub_date": normalize_date(_col(rows, fields, row, "pubDate")),
        }
        for key, (api_field, column) in _FIN_FIELD_MAP.items():
            prefix, _, _ = key.partition("_")
            if prefix == which:
                record[column] = clean_num(_col(rows, fields, row, api_field))
        out.append(record)
    return out


# ───────────────────────── 公司报告类 ─────────────────────────

def map_performance_express(fields: list[str], rows: list[list[Any]]) -> list[tuple]:
    """业绩快报 → ``performance_express`` 行（同 stat_date 多次更新取最新覆盖）。"""
    return [
        (clean_str(_col(rows, fields, r, "code")),
         normalize_date(_col(rows, fields, r, "performanceExpStatDate")),
         normalize_date(_col(rows, fields, r, "performanceExpPubDate")),
         normalize_date(_col(rows, fields, r, "performanceExpUpdateDate")),
         clean_num(_col(rows, fields, r, "performanceExpressTotalAsset")),
         clean_num(_col(rows, fields, r, "performanceExpressNetAsset")),
         clean_num(_col(rows, fields, r, "performanceExpressEPSChgPct")),
         clean_num(_col(rows, fields, r, "performanceExpressROEWa")),
         clean_num(_col(rows, fields, r, "performanceExpressEPSDiluted")),
         clean_num(_col(rows, fields, r, "performanceExpressGRYOY")),
         clean_num(_col(rows, fields, r, "performanceExpressOPYOY")))
        for r in rows
    ]


def map_forecast(fields: list[str], rows: list[list[Any]]) -> list[tuple]:
    """业绩预告 → ``profit_forecast`` 行（类型文本原样，不枚举归一，03）。"""
    return [
        (clean_str(_col(rows, fields, r, "code")),
         normalize_date(_col(rows, fields, r, "profitForcastExpStatDate")),
         normalize_date(_col(rows, fields, r, "profitForcastExpPubDate")),
         clean_str(_col(rows, fields, r, "profitForcastType")),
         clean_str(_col(rows, fields, r, "profitForcastAbstract")),
         clean_num(_col(rows, fields, r, "profitForcastChgPctUp")),
         clean_num(_col(rows, fields, r, "profitForcastChgPctDwn")))
        for r in rows
    ]


# ───────────────────────── 板块类 ─────────────────────────

def map_industry(fields: list[str], rows: list[list[Any]]) -> list[tuple]:
    """``query_stock_industry()`` → ``stock_industry`` 快照行。"""
    return [
        (clean_str(_col(rows, fields, r, "code")),
         normalize_date(_col(rows, fields, r, "updateDate")),
         clean_str(_col(rows, fields, r, "code_name")),
         clean_str(_col(rows, fields, r, "industry")),
         clean_str(_col(rows, fields, r, "industryClassification")))
        for r in rows
    ]


def map_constituent(fields: list[str], rows: list[list[Any]],
                    index_key: str) -> list[tuple]:
    """成分接口 → ``index_constituent`` 快照行（``index_key`` 由调用方写入）。"""
    return [
        (index_key,
         clean_str(_col(rows, fields, r, "code")),
         normalize_date(_col(rows, fields, r, "updateDate")),
         clean_str(_col(rows, fields, r, "code_name")))
        for r in rows
    ]


# ───────────────────────── 宏观类 ─────────────────────────

def map_macro_deposit(fields: list[str], rows: list[list[Any]]) -> list[tuple]:
    """存款利率 → ``macro_deposit_rate`` 行。"""
    return [
        (normalize_date(_col(rows, fields, r, "pubDate")),
         clean_num(_col(rows, fields, r, "demandDepositRate")),
         clean_num(_col(rows, fields, r, "fixedDepositRate3Month")),
         clean_num(_col(rows, fields, r, "fixedDepositRate6Month")),
         clean_num(_col(rows, fields, r, "fixedDepositRate1Year")),
         clean_num(_col(rows, fields, r, "fixedDepositRate2Year")),
         clean_num(_col(rows, fields, r, "fixedDepositRate3Year")),
         clean_num(_col(rows, fields, r, "fixedDepositRate5Year")),
         clean_num(_col(rows, fields, r, "installmentFixedDepositRate1Year")),
         clean_num(_col(rows, fields, r, "installmentFixedDepositRate3Year")),
         clean_num(_col(rows, fields, r, "installmentFixedDepositRate5Year")))
        for r in rows
    ]


def map_macro_loan(fields: list[str], rows: list[list[Any]]) -> list[tuple]:
    """贷款利率 → ``macro_loan_rate`` 行（``mortgate`` 拼写修正→``mortgage``）。"""
    return [
        (normalize_date(_col(rows, fields, r, "pubDate")),
         clean_num(_col(rows, fields, r, "loanRate6Month")),
         clean_num(_col(rows, fields, r, "loanRate6MonthTo1Year")),
         clean_num(_col(rows, fields, r, "loanRate1YearTo3Year")),
         clean_num(_col(rows, fields, r, "loanRate3YearTo5Year")),
         clean_num(_col(rows, fields, r, "loanRateAbove5Year")),
         clean_num(_col(rows, fields, r, "mortgateRateBelow5Year")),
         clean_num(_col(rows, fields, r, "mortgateRateAbove5Year")))
        for r in rows
    ]


def map_macro_reserve(fields: list[str], rows: list[list[Any]]) -> list[tuple]:
    """准备金率 → ``macro_reserve_ratio`` 行（公告日口径，04）。"""
    return [
        (normalize_date(_col(rows, fields, r, "pubDate")),
         normalize_date(_col(rows, fields, r, "effectiveDate")),
         clean_num(_col(rows, fields, r, "bigInstitutionsRatioPre")),
         clean_num(_col(rows, fields, r, "bigInstitutionsRatioAfter")),
         clean_num(_col(rows, fields, r, "mediumInstitutionsRatioPre")),
         clean_num(_col(rows, fields, r, "mediumInstitutionsRatioAfter")))
        for r in rows
    ]


def map_macro_money_month(fields: list[str], rows: list[list[Any]]) -> list[tuple]:
    """货币供应月度 → ``macro_money_supply_month`` 行（月份两位转 INTEGER）。"""
    return [
        (clean_int(_col(rows, fields, r, "statYear")),
         clean_int(_col(rows, fields, r, "statMonth")),
         clean_num(_col(rows, fields, r, "m0Month")),
         clean_num(_col(rows, fields, r, "m0YOY")),
         clean_num(_col(rows, fields, r, "m0ChainRelative")),
         clean_num(_col(rows, fields, r, "m1Month")),
         clean_num(_col(rows, fields, r, "m1YOY")),
         clean_num(_col(rows, fields, r, "m1ChainRelative")),
         clean_num(_col(rows, fields, r, "m2Month")),
         clean_num(_col(rows, fields, r, "m2YOY")),
         clean_num(_col(rows, fields, r, "m2ChainRelative")))
        for r in rows
    ]


def map_macro_money_year(fields: list[str], rows: list[list[Any]]) -> list[tuple]:
    """货币供应年度 → ``macro_money_supply_year`` 行。"""
    return [
        (clean_int(_col(rows, fields, r, "statYear")),
         clean_num(_col(rows, fields, r, "m0Year")),
         clean_num(_col(rows, fields, r, "m0YearYOY")),
         clean_num(_col(rows, fields, r, "m1Year")),
         clean_num(_col(rows, fields, r, "m1YearYOY")),
         clean_num(_col(rows, fields, r, "m2Year")),
         clean_num(_col(rows, fields, r, "m2YearYOY")))
        for r in rows
    ]
