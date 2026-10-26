"""同步数据集注册表（[D-132]）。

一个数据集是用户在面板上勾选的单位；它可对应一条或多条底层任务，并声明：

- 是否逐码（``target_kind``）；
- 可选指标组（K 线四组 / 财务六组；其余整取）；
- 时间口径（日期区间 / 季度区间 / 年份区间 / 无）；
- 覆盖账本的单元、变体、合并间隔与刷新视界；
- 是否高级 / 默认关闭。

**只做声明，不取数**——取数与写入仍由 ``sync.py`` 执行。字段→列的投影也在这里
定义，``sync.py`` 只消费 :func:`selected_columns`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from st_agent.l0.info.sources import INFO_TASKS
from st_agent.l0.market.fetch import FIN_QUARTER_COLUMNS
from st_agent.l0.market.tasks import SYNC_TASKS

__all__ = [
    "DATASETS",
    "DATASET_IDS",
    "DatasetSpec",
    "FULL_COLUMNS",
    "IndicatorGroup",
    "columns_for_groups",
    "dataset_for_task",
    "get_dataset",
    "normalize_groups",
    "selected_columns",
    "validate_datasets",
]

#: 数据集 → 映射函数返回的完整列序（部分写入按此投影）。
FULL_COLUMNS: dict[str, tuple[str, ...]] = {
    "k_daily": (
        "code", "trade_date", "open", "high", "low", "close", "preclose",
        "volume", "amount", "turn", "trade_status", "pct_chg",
        "pe_ttm", "pb_mrq", "ps_ttm", "pcf_ncf_ttm", "is_st",
    ),
    "k_period": (
        "code", "frequency", "trade_date", "open", "high", "low", "close",
        "volume", "amount", "turn", "pct_chg",
    ),
    "k_minute": (
        "code", "frequency", "bar_start", "open", "high", "low", "close",
        "volume", "amount",
    ),
    "fin_quarter": FIN_QUARTER_COLUMNS,
}


@dataclass(frozen=True)
class IndicatorGroup:
    key: str
    label: str
    default: bool = True


@dataclass(frozen=True)
class DatasetSpec:
    dataset_id: str
    label: str
    description: str
    task_keys: tuple[str, ...]
    target_kind: str  # target / global
    category: str
    indicator_groups: tuple[IndicatorGroup, ...] = ()
    time_kind: str = "none"  # date_range / quarter_range / year_range / none
    default_time: dict[str, Any] = field(default_factory=dict)
    coverage_unit: str = "none"  # day / quarter / year / none
    coverage_variants: tuple[str, ...] = ()
    merge_gap: int = 0
    refresh_kind: str = ""  # trading_days / periods / years
    refresh_value: int = 0
    advanced: bool = False
    default_disabled: bool = False


_PRICE = IndicatorGroup("price", "价格")
_VOLUME = IndicatorGroup("volume", "成交")
_VALUATION = IndicatorGroup("valuation", "估值")
_ST_FLAG = IndicatorGroup("st_flag", "ST 标记")
_FIN_GROUPS = (
    IndicatorGroup("profit", "盈利"),
    IndicatorGroup("operation", "营运"),
    IndicatorGroup("growth", "成长"),
    IndicatorGroup("balance", "偿债"),
    IndicatorGroup("cash", "现金流"),
    IndicatorGroup("dupont", "杜邦"),
)

#: 指标组 → 可写列（主键列始终另加）。
FIN_COLUMNS_BY_GROUP: dict[str, tuple[str, ...]] = {
    "profit": ("roe_avg", "np_margin", "gp_margin", "net_profit", "eps_ttm",
               "mb_revenue", "total_share", "liqa_share"),
    "operation": ("nr_turn_ratio", "nr_turn_days", "inv_turn_ratio", "inv_turn_days",
                  "ca_turn_ratio", "asset_turn_ratio"),
    "growth": ("yoy_equity", "yoy_asset", "yoy_ni", "yoy_eps_basic", "yoy_pni"),
    "balance": ("current_ratio", "quick_ratio", "cash_ratio", "yoy_liability",
                "liability_to_asset", "asset_to_equity"),
    "cash": ("ca_to_asset", "nca_to_asset", "tangible_asset_to_asset",
             "ebit_to_interest", "cfo_to_or", "cfo_to_np", "cfo_to_gr"),
    "dupont": ("dupont_roe", "dupont_asset_sto_equity", "dupont_asset_turn",
               "dupont_pnitoni", "dupont_nitogr", "dupont_tax_burden",
               "dupont_intburden", "dupont_ebittogr"),
}

KLINE_DAILY_COLUMNS: dict[str, tuple[str, ...]] = {
    "price": ("open", "high", "low", "close", "preclose", "pct_chg"),
    "volume": ("volume", "amount", "turn", "trade_status"),
    "valuation": ("pe_ttm", "pb_mrq", "ps_ttm", "pcf_ncf_ttm"),
    "st_flag": ("is_st",),
}
KLINE_PERIOD_COLUMNS: dict[str, tuple[str, ...]] = {
    "price": ("open", "high", "low", "close", "pct_chg"),
    "volume": ("volume", "amount", "turn"),
}
KLINE_MINUTE_COLUMNS: dict[str, tuple[str, ...]] = {
    "price": ("open", "high", "low", "close"),
    "volume": ("volume", "amount"),
}

_DAY_TIME = {"kind": "date_range", "mode": "last_years", "n": 1}
_FIN_TIME = {"kind": "quarter_range", "mode": "last_n", "n": 8}
_NONE_TIME = {"kind": "none"}


DATASETS: tuple[DatasetSpec, ...] = (
    # ── 基础主档 ──
    DatasetSpec(
        "calendar", "交易日历", "A 股交易日与休市日，1990 年至今",
        ("bs_calendar",), "global", "basic", time_kind="none",
        default_time=_NONE_TIME,
    ),
    DatasetSpec(
        "security", "证券主档", "股票 / 指数 / 可转债 / ETF 的基本资料与上市状态",
        ("bs_security_basic",), "global", "basic", time_kind="none",
        default_time=_NONE_TIME,
    ),
    DatasetSpec(
        "st_list", "ST 名单（名称匹配）", "BaoStock 名称查询快照，供 ST 标的解析",
        ("bs_st_list",), "global", "basic", time_kind="none",
        default_time=_NONE_TIME,
    ),
    DatasetSpec(
        "daily_listing", "每日在市证券", "指定交易日的在市证券清单与名称",
        ("bs_all_stock",), "global", "basic", time_kind="date_range",
        default_time={"kind": "date_range", "mode": "today"},
    ),
    # ── 行情 ──
    DatasetSpec(
        "k_daily", "日线行情", "全市场日 K 线，含 ST 标记、估值与成交指标",
        ("bs_k_daily",), "target", "quotes",
        indicator_groups=(_PRICE, _VOLUME, _VALUATION, _ST_FLAG),
        time_kind="date_range", default_time=_DAY_TIME,
        coverage_unit="day", merge_gap=7,
        refresh_kind="trading_days", refresh_value=5,
    ),
    DatasetSpec(
        "k_period", "周线 / 月线行情", "周线与月线的开高低收与成交",
        ("bs_k_period",), "target", "quotes",
        indicator_groups=(_PRICE, _VOLUME),
        time_kind="date_range", default_time=_DAY_TIME,
        coverage_unit="day", coverage_variants=("w", "m"), merge_gap=0,
        refresh_kind="periods", refresh_value=2,
    ),
    DatasetSpec(
        "k_minute", "分钟线行情", "5 / 15 / 30 / 60 分钟线，仅关注池或指定代码",
        ("bs_k_minute",), "target", "quotes",
        indicator_groups=(_PRICE, _VOLUME),
        time_kind="date_range",
        default_time={"kind": "date_range", "mode": "last_days", "n": 30},
        coverage_unit="day", coverage_variants=("5", "15", "30", "60"),
        merge_gap=0, refresh_kind="trading_days", refresh_value=1,
        advanced=True, default_disabled=True,
    ),
    # ── 公司行为 ──
    DatasetSpec(
        "adjust_factor", "复权因子", "前复权 / 后复权因子，用于复权价格推导",
        ("bs_adjust_factor",), "target", "corporate_action",
        time_kind="date_range",
        default_time={"kind": "date_range", "mode": "full"},
        coverage_unit="day", merge_gap=31,
        refresh_kind="years", refresh_value=1,
    ),
    DatasetSpec(
        "dividend", "分红送转", "每股分红、送转与除权除息日",
        ("bs_dividend",), "target", "corporate_action",
        time_kind="year_range",
        default_time={"kind": "year_range", "mode": "last_n", "n": 3},
        coverage_unit="year", merge_gap=0,
        refresh_kind="years", refresh_value=2,
    ),
    # ── 财务 ──
    DatasetSpec(
        "fin_quarter", "季度财务报表", "盈利、营运、成长、偿债、现金流与杜邦指标",
        ("bs_fin_quarter",), "target", "financial",
        indicator_groups=_FIN_GROUPS,
        time_kind="quarter_range", default_time=_FIN_TIME,
        coverage_unit="quarter", merge_gap=0,
        refresh_kind="periods", refresh_value=8,
    ),
    DatasetSpec(
        "perf_express", "业绩快报", "公司业绩快报的总资产、净资产、EPS 等",
        ("bs_perf_express",), "target", "financial", time_kind="date_range",
        default_time={"kind": "date_range", "mode": "last_years", "n": 2},
        coverage_unit="quarter", merge_gap=0,
        refresh_kind="periods", refresh_value=8,
    ),
    DatasetSpec(
        "forecast", "业绩预告", "预增 / 预亏 / 扭亏等业绩预告",
        ("bs_forecast",), "target", "financial", time_kind="date_range",
        default_time={"kind": "date_range", "mode": "last_years", "n": 2},
        coverage_unit="quarter", merge_gap=0,
        refresh_kind="periods", refresh_value=8,
    ),
    # ── 板块 ──
    DatasetSpec(
        "industry", "行业分类", "申万等行业分类每周快照",
        ("bs_industry",), "global", "sector", time_kind="none",
        default_time=_NONE_TIME,
    ),
    DatasetSpec(
        "sz50", "上证50 成分", "上证50 指数成分每周快照",
        ("bs_sz50",), "global", "sector", time_kind="none",
        default_time=_NONE_TIME,
    ),
    DatasetSpec(
        "hs300", "沪深300 成分", "沪深300 指数成分每周快照",
        ("bs_hs300",), "global", "sector", time_kind="none",
        default_time=_NONE_TIME,
    ),
    DatasetSpec(
        "zz500", "中证500 成分", "中证500 指数成分每周快照",
        ("bs_zz500",), "global", "sector", time_kind="none",
        default_time=_NONE_TIME,
    ),
    # ── 宏观 ──
    DatasetSpec("macro_deposit", "存款基准利率", "央行存款基准利率",
                 ("bs_macro_deposit",), "global", "macro", time_kind="none",
                 default_time=_NONE_TIME),
    DatasetSpec("macro_loan", "贷款基准利率", "央行贷款基准利率",
                 ("bs_macro_loan",), "global", "macro", time_kind="none",
                 default_time=_NONE_TIME),
    DatasetSpec("macro_reserve", "存款准备金率", "央行存款准备金率",
                 ("bs_macro_reserve",), "global", "macro", time_kind="none",
                 default_time=_NONE_TIME),
    DatasetSpec("money_month", "货币供应量（月度）", "M0 / M1 / M2 月度数据",
                 ("bs_money_month",), "global", "macro", time_kind="none",
                 default_time=_NONE_TIME),
    DatasetSpec("money_year", "货币供应量（年度）", "M0 / M1 / M2 年度数据",
                 ("bs_money_year",), "global", "macro", time_kind="none",
                 default_time=_NONE_TIME),
    # ── 信息面 ──
    DatasetSpec("announcement_cninfo", "公告（巨潮主源）", "沪深全市场公告，权威源优先",
                 ("info_announcement_cninfo",), "global", "announcement",
                 time_kind="none", default_time=_NONE_TIME),
    DatasetSpec("announcement_szse", "公告（深交所备源）", "仅深市公告，主源不可用时兜底",
                 ("info_announcement_szse",), "global", "announcement",
                 time_kind="none", default_time=_NONE_TIME, advanced=True),
    DatasetSpec("announcement_em", "公告（东财备源）", "仅沪市公告，主源不可用时兜底",
                 ("info_announcement_em",), "global", "announcement",
                 time_kind="none", default_time=_NONE_TIME, advanced=True),
    DatasetSpec("dragon_tiger_em", "龙虎榜（东财主源）", "全市场上榜记录与席位明细",
                 ("info_dragon_tiger_em",), "global", "dragon_tiger",
                 time_kind="none", default_time=_NONE_TIME),
    DatasetSpec("dragon_tiger_szse", "龙虎榜（深交所 · 深市）", "深市上榜记录与官方席位明细",
                 ("info_dragon_tiger_szse",), "global", "dragon_tiger",
                 time_kind="none", default_time=_NONE_TIME, advanced=True),
    DatasetSpec("dragon_tiger_sse", "龙虎榜（上交所 · 沪市）", "沪市上榜记录与官方席位明细",
                 ("info_dragon_tiger_sse",), "global", "dragon_tiger",
                 time_kind="none", default_time=_NONE_TIME, advanced=True),
    DatasetSpec("shareholder_em", "股东户数（东财主源）", "最新一期股东户数与户均持股",
                 ("info_shareholder_num_em",), "global", "shareholder_sentiment",
                 time_kind="none", default_time=_NONE_TIME),
    DatasetSpec("shareholder_cninfo", "股东户数（巨潮备源）", "按报告期回补更早季末",
                 ("info_shareholder_num_cninfo",), "global", "shareholder_sentiment",
                 time_kind="none", default_time=_NONE_TIME, advanced=True),
    DatasetSpec("sentiment_qa_sse", "投资者问答（沪市全市场）", "上证 e 互动全市场问答流",
                 ("info_sentiment_qa_sse",), "global", "shareholder_sentiment",
                 time_kind="none", default_time=_NONE_TIME),
    DatasetSpec("sentiment_qa_irm", "投资者问答（深市关注池）", "互动易按关注池采集，默认关闭",
                 ("info_sentiment_qa_irm",), "global", "shareholder_sentiment",
                 time_kind="none", default_time=_NONE_TIME,
                 advanced=True, default_disabled=True),
    DatasetSpec("sentiment_hot", "市场热度榜", "同花顺 / 东财热度榜快照",
                 ("info_sentiment_hot",), "global", "shareholder_sentiment",
                 time_kind="none", default_time=_NONE_TIME),
)

DATASET_IDS: tuple[str, ...] = tuple(d.dataset_id for d in DATASETS)
_BY_ID: dict[str, DatasetSpec] = {d.dataset_id: d for d in DATASETS}
_BY_TASK: dict[str, DatasetSpec] = {
    key: d for d in DATASETS for key in d.task_keys
}


def dataset_for_task(task_key: str) -> DatasetSpec:
    """任务键 → 所属数据集；未知任务 → ``MarketValidationError``。"""
    from st_agent.l0.market.errors import MarketValidationError

    try:
        return _BY_TASK[task_key]
    except KeyError:
        raise MarketValidationError(
            f"任务 {task_key!r} 未登记到任何数据集"
        ) from None


def get_dataset(dataset_id: str) -> DatasetSpec:
    from st_agent.l0.market.errors import MarketValidationError

    try:
        return _BY_ID[dataset_id]
    except KeyError:
        raise MarketValidationError(
            f"未知数据集 {dataset_id!r}；合法值 = {list(DATASET_IDS)}"
        ) from None


def normalize_groups(dataset_id: str, groups: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    """校验并规范化指标组；无组数据集恒为 ``("all",)``，缺省取全部默认组。"""
    spec = get_dataset(dataset_id)
    if not spec.indicator_groups:
        return ("all",)
    allowed = tuple(g.key for g in spec.indicator_groups)
    if not groups:
        return tuple(g.key for g in spec.indicator_groups if g.default)
    selected = tuple(dict.fromkeys(str(g) for g in groups))
    unknown = [g for g in selected if g not in allowed]
    if unknown:
        from st_agent.l0.market.errors import MarketValidationError

        raise MarketValidationError(
            f"数据集 {dataset_id!r} 不支持的指标组 {unknown}；合法值 = {list(allowed)}"
        )
    return tuple(g for g in allowed if g in selected)


def columns_for_groups(dataset_id: str, groups: tuple[str, ...]) -> tuple[str, ...] | None:
    """选中组对应的可写列；``None`` 表示整取（不投影）。"""
    if dataset_id == "k_daily":
        cols = ("code", "trade_date")
        for group in groups:
            cols += KLINE_DAILY_COLUMNS.get(group, ())
        return tuple(dict.fromkeys(cols))
    if dataset_id == "k_period":
        cols = ("code", "frequency", "trade_date")
        for group in groups:
            cols += KLINE_PERIOD_COLUMNS.get(group, ())
        return tuple(dict.fromkeys(cols))
    if dataset_id == "k_minute":
        cols = ("code", "frequency", "bar_start")
        for group in groups:
            cols += KLINE_MINUTE_COLUMNS.get(group, ())
        return tuple(dict.fromkeys(cols))
    if dataset_id == "fin_quarter":
        cols = ("code", "stat_date", "pub_date")
        for group in groups:
            cols += FIN_COLUMNS_BY_GROUP.get(group, ())
        return tuple(dict.fromkeys(cols))
    return None


def selected_columns(dataset_id: str, groups: list[str] | tuple[str, ...] | None) -> tuple[str, ...] | None:
    return columns_for_groups(dataset_id, normalize_groups(dataset_id, groups))


def validate_datasets() -> list[str]:
    """目录完整性自检（空列表＝通过）；每个任务恰好归一个数据集。"""
    problems: list[str] = []
    task_keys = [t.task_key for t in SYNC_TASKS] + [t.task_key for t in INFO_TASKS]
    covered = [key for d in DATASETS for key in d.task_keys]
    duplicates = sorted({key for key in covered if covered.count(key) > 1})
    if duplicates:
        problems.append(f"任务被多个数据集重复覆盖：{duplicates}")
    missing = sorted(set(task_keys) - set(covered))
    if missing:
        problems.append(f"任务未进入任何数据集：{missing}")
    extra = sorted(set(covered) - set(task_keys))
    if extra:
        problems.append(f"数据集登记了不存在的任务：{extra}")
    ids = [d.dataset_id for d in DATASETS]
    if len(ids) != len(set(ids)):
        problems.append("数据集 id 重复")
    for d in DATASETS:
        if not d.label or not d.description:
            problems.append(f"数据集 {d.dataset_id} 缺少中文名或说明")
        if d.target_kind not in ("target", "global"):
            problems.append(f"数据集 {d.dataset_id} 的 target_kind 非法")
        if d.time_kind not in ("date_range", "quarter_range", "year_range", "none"):
            problems.append(f"数据集 {d.dataset_id} 的 time_kind 非法")
        if d.coverage_unit not in ("day", "quarter", "year", "none"):
            problems.append(f"数据集 {d.dataset_id} 的 coverage_unit 非法")
    return problems
