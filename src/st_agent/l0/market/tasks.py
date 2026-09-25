"""同步任务注册表（数据库设计 05 同步任务清单的机器可读副本）。

19 个任务逐一登记：``task_key`` / 目标表 / 同步模式 / 触发节奏（人类可读）/
前置依赖 / 默认禁用（分钟线）。``sync_state`` 的种子行由此生成——
任务清单只此一处，05 文档修订时同步此处（反向亦然，收工自检覆盖行数）。

水位语义（05「水位语义」列）：
- ``bs_all_stock``：最近已拉交易日
- ``bs_k_daily``：全库 ``max(trade_date)``
- ``bs_k_period``：各 frequency ``max(trade_date)``（水位串 ``w:<d>,m:<d>``）
- ``bs_k_minute``：各 ``(code, frequency)`` ``max(bar_start)``（存全局最大）
- ``bs_adjust_factor``：全库 ``max(ex_date)``
- ``bs_dividend``：已拉年份（``2024+2025`` 形）
- ``bs_fin_quarter``：已覆盖的最近 ``stat_date``
- ``bs_perf_express`` / ``bs_forecast``：已拉窗口起点
- ``bs_industry`` / 成分：最新 ``update_date``
- 宏表 / 日历 / 主档：全量无水位（``None``）

窗口推导（``window_for``）：增量任务按水位次日续跑；``upsert_window`` 任务
按固定重拉窗口（财务 8 季度 / 快报预告 24 个月 / 分红当年+前 1 年）；
水位缺失（首次）时从基线起点全量（日线 1990-12-19 / 其余 1990-01-01）。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import NamedTuple

__all__ = [
    "SYNC_TASKS",
    "TASK_COUNT",
    "TASK_KEYS",
    "TaskSpec",
    "get_task",
    "last_monday",
    "last_n_quarters",
    "next_day",
    "quarter_end",
    "window_for",
]

_BASELINE_START = "1990-01-01"
_KLINE_BASELINE_START = "1990-12-19"


class TaskSpec(NamedTuple):
    """单个同步任务的登记项（05 任务清单一行的机器可读副本）。"""

    task_key: str
    table_name: str
    mode: str  # full / incremental / snapshot / upsert_window
    schedule_desc: str
    needs: tuple[str, ...] = ()
    """触发前置（行情/财务任务要求日历与全量快照当日已成功，05 时序前置）。"""
    default_disabled: bool = False


_CAL_FIN_NEEDS = ("bs_calendar", "bs_all_stock")

SYNC_TASKS: tuple[TaskSpec, ...] = (
    TaskSpec("bs_calendar", "trade_calendar", "full", "每年初全量；每季度校验"),
    TaskSpec("bs_security_basic", "security", "full", "首次全量；此后每周全量 UPSERT"),
    TaskSpec("bs_all_stock", "security", "incremental", "每交易日（数据可用后）"),
    TaskSpec("bs_k_daily", "k_line_daily", "incremental", "每交易日", _CAL_FIN_NEEDS),
    TaskSpec("bs_k_period", "k_line_period", "incremental",
             "每周（周线）/每月（月线）最后交易日", _CAL_FIN_NEEDS),
    TaskSpec("bs_k_minute", "k_line_minute", "incremental",
             "默认禁用，仅关注池证券按需启用", _CAL_FIN_NEEDS, True),
    TaskSpec("bs_adjust_factor", "adjust_factor", "upsert_window",
             "每周 + 关注池事件驱动", _CAL_FIN_NEEDS),
    TaskSpec("bs_dividend", "dividend", "upsert_window",
             "每周（按年度拉取：当年 + 前 1 年）", _CAL_FIN_NEEDS),
    TaskSpec("bs_fin_quarter", "financial_quarter", "upsert_window",
             "每周（重拉最近 8 个季度）", _CAL_FIN_NEEDS),
    TaskSpec("bs_perf_express", "performance_express", "upsert_window",
             "每周（重拉最近 24 个月）", _CAL_FIN_NEEDS),
    TaskSpec("bs_forecast", "profit_forecast", "upsert_window",
             "每周（重拉最近 24 个月）", _CAL_FIN_NEEDS),
    TaskSpec("bs_industry", "stock_industry", "snapshot", "每周一"),
    TaskSpec("bs_sz50", "index_constituent", "snapshot", "每周一"),
    TaskSpec("bs_hs300", "index_constituent", "snapshot", "每周一"),
    TaskSpec("bs_zz500", "index_constituent", "snapshot", "每周一"),
    TaskSpec("bs_macro_deposit", "macro_deposit_rate", "full", "每周（量小，全量对账）"),
    TaskSpec("bs_macro_loan", "macro_loan_rate", "full", "每周"),
    TaskSpec("bs_macro_reserve", "macro_reserve_ratio", "full", "每周"),
    TaskSpec("bs_money_month", "macro_money_supply_month", "full", "每月"),
    TaskSpec("bs_money_year", "macro_money_supply_year", "full", "每年"),
)

TASK_KEYS: tuple[str, ...] = tuple(t.task_key for t in SYNC_TASKS)
TASK_COUNT: int = len(SYNC_TASKS)

_TASKS_BY_KEY: dict[str, TaskSpec] = {t.task_key: t for t in SYNC_TASKS}


def get_task(task_key: str) -> TaskSpec:
    """取任务登记项（未知 → ``MarketValidationError``，由调用方 import 时补）。"""
    from st_agent.l0.market.errors import MarketValidationError

    try:
        return _TASKS_BY_KEY[task_key]
    except KeyError:
        raise MarketValidationError(
            f"未知同步任务 {task_key!r}；合法任务 = {list(TASK_KEYS)}"
        ) from None


# ───────────────────────── 日期工具（窗口推导） ─────────────────────────

def _today() -> date:
    return datetime.now().astimezone().date()


def quarter_end(year: int, quarter: int) -> str:
    """季度末日（``(2017, 2)`` → ``'2017-06-30'``；非法季度 → ValueError）。"""
    if quarter not in (1, 2, 3, 4):
        raise ValueError(f"非法季度 {quarter!r}（仅 1–4）")
    return f"{year}-{quarter * 3:02d}-{'31' if quarter in (1, 4) else '30'}"


def last_n_quarters(n: int = 8, today: date | None = None) -> list[tuple[int, int]]:
    """最近 n 个季度（ oldest → newest；含当前季度）。"""
    today = today or _today()
    quarter = (today.month - 1) // 3 + 1
    out: list[tuple[int, int]] = []
    year, quarter_ = today.year, quarter
    for _ in range(n):
        out.append((year, quarter_))
        quarter_ -= 1
        if quarter_ == 0:
            quarter_, year = 4, year - 1
    return sorted(out)


def last_monday(today: date | None = None) -> date:
    """最近一个周一（含今日如为周一；板块快照锚，05 时序前置）。"""
    today = today or _today()
    return date.fromordinal(today.toordinal() - today.weekday())


def next_day(day: str) -> str:
    """``YYYY-MM-DD`` 次日（增量续跑起点；非法格式 → ValueError）。"""
    return date.fromisoformat(day).fromordinal(
        date.fromisoformat(day).toordinal() + 1
    ).isoformat()


def window_for(task_key: str, watermark: str | None,
               today: date | None = None) -> tuple[str, str]:
    """推导任务拉取窗口 ``(start, end)``（水位缺失 = 首次 → 基线起点全量）。

    - ``bs_k_daily``：水位次日 → 今日（缺水位 → 1990-12-19 起）
    - ``bs_k_period``：水位串缺失 → 基线起点；否则同日线（周月线仅在周期
      末交易日产生行，拉取窗口仍按日区间给，源端过滤）
    - ``bs_fin_quarter``：忽略水位 → 最近 8 个季度首季末日所在月首日 → 今日
      （upsert_window 重拉覆盖，修正件自愈）
    - ``bs_perf_express`` / ``bs_forecast``：最近 24 个月首月首日 → 今日
    - ``bs_dividend``：水位年份缺失 → 今年与去年 1-1 → 12-31（返回年份串
      由调用方展开；此处返回 ``(start_year, end_year)`` 形日期对）
    - 快照/全量任务：返回 ``(_BASELINE_START, today)``（快照追加新行、
      全量对账覆盖）
    """
    from st_agent.l0.market.errors import MarketValidationError

    spec = get_task(task_key)
    today = today or _today()
    end = today.isoformat()
    if task_key == "bs_k_daily":
        start = next_day(watermark) if watermark else _KLINE_BASELINE_START
        return (start, end)
    if task_key == "bs_k_period":
        return (next_day(watermark.split(",")[-1].split(":")[-1])
                if watermark else _KLINE_BASELINE_START, end)
    if task_key == "bs_fin_quarter":
        years_quarters = last_n_quarters(8, today)
        first_year, first_q = years_quarters[0]
        return (f"{first_year}-{(first_q - 1) * 3 + 1:02d}-01", end)
    if task_key in ("bs_perf_express", "bs_forecast"):
        month = today.month - 23
        year = today.year
        while month <= 0:
            month += 12
            year -= 1
        return (f"{year}-{month:02d}-01", end)
    if task_key == "bs_dividend":
        years = sorted({int(y) for y in (watermark or "").split("+") if y.strip().isdigit()},
                       reverse=True) if watermark else []
        if not years:
            return (f"{today.year - 1}-01-01", f"{today.year}-12-31")
        return (f"{years[-1]}-01-01", f"{years[0]}-12-31")
    if spec.mode in ("full", "snapshot", "incremental", "upsert_window"):
        return (_BASELINE_START, end)
    raise MarketValidationError(f"任务 {task_key!r} 模式 {spec.mode!r} 无窗口规则")
