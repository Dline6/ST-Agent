"""BaoStock 真实抓取器（T-L0-005.1；``Fetcher`` 协议的联网实现）。

会话纪律（05 初始化顺序 + 02 §6 唯一出口）：
- ``login() → 批量 query → logout()`` 由本类统一管理；登录失败 → 调用方
  见 ``FetchUnavailableError``（同步引擎译为信封 ``unavailable``）
- 每次 query 的 ``error_code != '0'`` → ``FetchError``（→ 信封 ``failed``）
- 行内容永不进网关审计（引擎按次 sender 只收字节数）；本类只返回
  ``(fields, rows)`` 字符串形态（06 通用约定）
- 复权口径：K 线固定 ``adjustflag='3'``（只落不复权，复权经视图推导）
- 准备金率固定 ``yearType='0'``（公告日口径，04 契约要点）

依赖说明：``baostock`` 为可选联网依赖（``pip install baostock``），
缺失时构造即抛 ``FetchUnavailableError``（调用方走 ``unavailable`` 显式分支）。
本模块不 import pandas（``get_row_data`` 逐行取数即可）。
"""

from __future__ import annotations

from typing import Any

from st_agent.l0.market.errors import FetchError, FetchUnavailableError
from st_agent.l0.market.fetch import FetchResult

__all__ = ["BaoStockFetcher"]

_K_DAILY_FIELDS = (
    "date,code,open,high,low,close,preclose,volume,amount,adjustflag,"
    "turn,tradestatus,pctChg,peTTM,pbMRQ,psTTM,pcfNcfTTM,isST"
)
_K_PERIOD_FIELDS = (
    "date,code,open,high,low,close,volume,amount,adjustflag,turn,pctChg"
)
_K_MINUTE_FIELDS = (
    "date,time,code,open,high,low,close,volume,amount,adjustflag"
)

_FIN_METHODS = {
    "profit": "query_profit_data",
    "operation": "query_operation_data",
    "growth": "query_growth_data",
    "balance": "query_balance_data",
    "cash": "query_cash_flow_data",
    "dupont": "query_dupont_data",
}


class BaoStockFetcher:
    """真实抓取器（上下文管理器：``with BaoStockFetcher() as f`` 自动登入登出）。

    非上下文用法：``open()`` / ``close()`` 手动管理；``close`` 幂等。
    """

    def __init__(self) -> None:
        try:
            import baostock as bs
        except ImportError as exc:
            raise FetchUnavailableError(
                "baostock 包未安装（pip install baostock 后重试）"
            ) from exc
        self._bs = bs
        self._open = False

    # ───────────────────────── 会话管理 ─────────────────────────

    def __enter__(self) -> "BaoStockFetcher":
        self.open()
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def open(self) -> None:
        """登录（失败 → ``FetchUnavailableError``，不抛裸异常）。"""
        try:
            result = self._bs.login()
        except Exception as exc:
            raise FetchUnavailableError(f"BaoStock 登录异常：{exc}") from exc
        if getattr(result, "error_code", "1") != "0":
            raise FetchUnavailableError(
                f"BaoStock 登录失败：{getattr(result, 'error_msg', result)}"
            )
        self._open = True

    def close(self) -> None:
        """登出（幂等；异常吞掉——同步结果不依赖登出成败）。"""
        if not self._open:
            return
        self._open = False
        try:
            self._bs.logout()
        except Exception:
            pass

    # ───────────────────────── 元信息类 ─────────────────────────

    def calendar(self, start: str, end: str) -> FetchResult:
        rs = self._call(self._bs.query_trade_dates,
                        start_date=start, end_date=end)
        return self._drain(rs)

    def security_basic(self) -> FetchResult:
        rs = self._call(self._bs.query_stock_basic)
        return self._drain(rs)

    def all_stock(self, day: str) -> FetchResult:
        rs = self._call(self._bs.query_all_stock, day=day)
        return self._drain(rs)

    # ───────────────────────── 行情类 ─────────────────────────

    def k_daily(self, code: str, start: str, end: str) -> FetchResult:
        rs = self._call(self._bs.query_history_k_data_plus, code,
                        _K_DAILY_FIELDS, start_date=start, end_date=end,
                        frequency="d", adjustflag="3")
        return self._drain(rs)

    def k_period(self, code: str, frequency: str, start: str, end: str) -> FetchResult:
        rs = self._call(self._bs.query_history_k_data_plus, code,
                        _K_PERIOD_FIELDS, start_date=start, end_date=end,
                        frequency=frequency, adjustflag="3")
        return self._drain(rs)

    def k_minute(self, code: str, frequency: str, start: str, end: str) -> FetchResult:
        rs = self._call(self._bs.query_history_k_data_plus, code,
                        _K_MINUTE_FIELDS, start_date=start, end_date=end,
                        frequency=frequency, adjustflag="3")
        return self._drain(rs)

    def adjust_factor(self, code: str, start: str, end: str) -> FetchResult:
        rs = self._call(self._bs.query_adjust_factor, code=code,
                        start_date=start, end_date=end)
        return self._drain(rs)

    def dividend(self, code: str, year: str) -> FetchResult:
        rs = self._call(self._bs.query_dividend_data, code=code,
                        year=year, yearType="report")
        return self._drain(rs)

    def financial(self, code: str, year: int, quarter: int,
                  which: str) -> FetchResult:
        from st_agent.l0.market.errors import MarketValidationError

        try:
            method = _FIN_METHODS[which]
        except KeyError:
            raise MarketValidationError(
                f"未知财务接口 {which!r}；合法 = {sorted(_FIN_METHODS)}"
            ) from None
        rs = self._call(getattr(self._bs, method), code=code,
                        year=year, quarter=quarter)
        return self._drain(rs)

    def performance_express(self, code: str, start: str,
                            end: str) -> FetchResult:
        rs = self._call(self._bs.query_performance_express_report, code,
                        start_date=start, end_date=end)
        return self._drain(rs)

    def forecast(self, code: str, start: str, end: str) -> FetchResult:
        rs = self._call(self._bs.query_forecast_report, code,
                        start_date=start, end_date=end)
        return self._drain(rs)

    # ───────────────────────── 板块类 ─────────────────────────

    def industry(self) -> FetchResult:
        rs = self._call(self._bs.query_stock_industry)
        return self._drain(rs)

    def constituents(self, index_key: str) -> FetchResult:
        from st_agent.l0.market.errors import MarketValidationError

        methods = {"sz50": "query_sz50_stocks", "hs300": "query_hs300_stocks",
                   "zz500": "query_zz500_stocks"}
        try:
            method = methods[index_key]
        except KeyError:
            raise MarketValidationError(
                f"未知指数 {index_key!r}；合法 = {sorted(methods)}"
            ) from None
        rs = self._call(getattr(self._bs, method))
        return self._drain(rs)

    # ───────────────────────── 宏观类 ─────────────────────────

    def macro_deposit(self, start: str, end: str) -> FetchResult:
        rs = self._call(self._bs.query_deposit_rate_data,
                        start_date=start, end_date=end)
        return self._drain(rs)

    def macro_loan(self, start: str, end: str) -> FetchResult:
        rs = self._call(self._bs.query_loan_rate_data,
                        start_date=start, end_date=end)
        return self._drain(rs)

    def macro_reserve(self) -> FetchResult:
        rs = self._call(self._bs.query_required_reserve_ratio_data,
                        yearType="0")
        return self._drain(rs)

    def macro_money_month(self) -> FetchResult:
        rs = self._call(self._bs.query_money_supply_data_month)
        return self._drain(rs)

    def macro_money_year(self) -> FetchResult:
        rs = self._call(self._bs.query_money_supply_data_year)
        return self._drain(rs)

    # ───────────────────────── 内部工具 ─────────────────────────

    def _call(self, func, *args: Any, **kwargs: Any):
        """调一次 baostock 接口（未登录先登；抛裸异常 → ``FetchError``）。"""
        if not self._open:
            self.open()
        try:
            return func(*args, **kwargs)
        except (FetchError, FetchUnavailableError):
            raise
        except Exception as exc:
            raise FetchError(f"BaoStock 接口调用失败：{exc}") from exc

    @staticmethod
    def _drain(rs) -> FetchResult:
        """取结果集（``error_code`` 非 0 → ``FetchError``；逐行字符串）。"""
        if getattr(rs, "error_code", "1") != "0":
            raise FetchError(
                f"BaoStock 查询失败：{getattr(rs, 'error_msg', rs)}"
            )
        fields = list(getattr(rs, "fields", []))
        rows: list[list[Any]] = []
        try:
            while rs.next():
                rows.append([str(cell) for cell in rs.get_row_data()])
        except FetchError:
            raise
        except Exception as exc:
            raise FetchError(f"BaoStock 结果集读取失败：{exc}") from exc
        return (fields, rows)
