"""BaoStock 真实抓取器（T-L0-005.1；``Fetcher`` 协议的联网实现）。

会话纪律（05 初始化顺序 + 02 §6 唯一出口）：
- ``login() → 批量 query → logout()`` 由本类统一管理；登录失败 → 调用方
  见 ``FetchUnavailableError``（同步引擎译为信封 ``unavailable``）
- 每次 query 的 ``error_code != '0'`` → ``FetchError``（→ 信封 ``failed``），
  **错误码原文随异常带出**（T-L0-017.2 GWT-1：只留 ``error_msg`` 时，调用侧
  无从区分「网络/会话类可重试」与「调用方参数类不可重试」）
- 可重试判定按源端语义分类（``RETRYABLE_ERROR_CODES``），由本模块负责——
  同步引擎只读 ``FetchError.retryable``，不解析错误码
- 行内容永不进网关审计（引擎按次 sender 只收字节数）；本类只返回
  ``(fields, rows)`` 字符串形态（06 通用约定）
- **静默截断防护**（T-L0-017.2 GWT-5）：源端分批取数，翻页收发失败时客户端
  ``next()`` 返回 ``False`` 而 ``error_code`` 仍为 ``0``，半截结果集与正常收尾
  **同形**（2026-10-04 真网实测：``sz.000001`` 首次只返 4000 行＝恰 2 页，
  重测两次均 8664 行）。形态判据（``fetch.truncation_suspect``）与复核动作
  **不在本类**——复核即多抓一次，须由同步引擎逐次经网关留审计
  （02 §6 唯一出口：不留"隐形的第二次 socket 调用"）
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

__all__ = [
    "NETWORK_ERROR_CODES",
    "RETRYABLE_ERROR_CODES",
    "SESSION_ERROR_CODES",
    "BaoStockFetcher",
    "is_retryable_error",
]

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

# ───────────────────────── 错误码分类（源端语义，T-L0-017.2 GWT-2） ─────────────────────────
# 取值与注释抄自 baostock 客户端常量表 ``common/contants.py``（**不是**本品自定）。
# 分类只回答一件事：**换个时刻重试是否可能成功**。参数类错误重试无意义，
# 且拿重试掩盖调用方缺陷会让真正的 bug 变成"偶发失败"。

NETWORK_ERROR_CODES = frozenset({
    "10002001",  # 网络错误
    "10002002",  # 网络连接失败
    "10002003",  # 网络连接超时
    "10002004",  # 网络接收时连接断开
    "10002005",  # 网络发送失败
    "10002006",  # 网络发送超时
    "10002007",  # 网络接收错误
    "10002008",  # 网络接收超时
})
"""瞬时网络类——重试可能成功（真网实测的连接强断即落在这一类）。"""

SESSION_ERROR_CODES = frozenset({
    "10001001",  # 用户未登陆
    "10001003",  # 获取用户信息失败
    "10001005",  # 账号登陆数达到上限
    "10001010",  # 用户登出失败
})
"""会话类——重建会话（``reconnect``）后重试可能成功。"""

RETRYABLE_ERROR_CODES = NETWORK_ERROR_CODES | SESSION_ERROR_CODES
"""可重试错误码全集；其余（含参数类 ``1000400x``、权限类 ``10001006``）一律不重试。"""


def is_retryable_error(error_code: str | None) -> bool:
    """该错误码是否值得重试（``None`` / 未知码 → ``False``，不臆断为可重试）。"""
    return error_code in RETRYABLE_ERROR_CODES


# ───────────────────────── 静默截断防护 ─────────────────────────
# 形态判据与复核动作在同步引擎侧（``fetch.truncation_suspect`` / ``sync`` 的
# ``_via_gateway``）——复核即多抓一次，必须逐次经网关留审计，不能藏在本类里。


class BaoStockFetcher:
    """真实抓取器（上下文管理器：``with BaoStockFetcher() as f`` 自动登入登出）。

    非上下文用法：``open()`` / ``close()`` 手动管理；``close`` 幂等。
    连接被源端强制关闭时由调用方（同步引擎的重试策略）调 ``reconnect()`` 重建会话。
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
            raise FetchUnavailableError(
                f"BaoStock 登录异常：{exc}", retryable=True
            ) from exc
        if getattr(result, "error_code", "1") != "0":
            code = getattr(result, "error_code", None)
            raise FetchUnavailableError(
                f"BaoStock 登录失败（error_code={code}）："
                f"{getattr(result, 'error_msg', result)}",
                error_code=code, retryable=is_retryable_error(code),
            )
        self._open = True

    def reconnect(self) -> None:
        """重建会话（登出后重登；幂等；失败 → ``FetchUnavailableError``）。

        用于「连接被源端强断」后继续取数——一次断连不该毁掉整批 / 整任务。
        """
        self.close()
        self.open()

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
        return self._fetch(self._bs.query_trade_dates, start_date=start,
                           end_date=end)

    def security_basic(self) -> FetchResult:
        return self._fetch(self._bs.query_stock_basic)

    def all_stock(self, day: str) -> FetchResult:
        return self._fetch(self._bs.query_all_stock, day=day)

    # ───────────────────────── 行情类 ─────────────────────────

    def k_daily(self, code: str, start: str, end: str) -> FetchResult:
        return self._fetch(self._bs.query_history_k_data_plus, code,
                           _K_DAILY_FIELDS, start_date=start, end_date=end,
                           frequency="d", adjustflag="3")

    def k_period(self, code: str, frequency: str, start: str, end: str) -> FetchResult:
        return self._fetch(self._bs.query_history_k_data_plus, code,
                           _K_PERIOD_FIELDS, start_date=start, end_date=end,
                           frequency=frequency, adjustflag="3")

    def k_minute(self, code: str, frequency: str, start: str, end: str) -> FetchResult:
        return self._fetch(self._bs.query_history_k_data_plus, code,
                           _K_MINUTE_FIELDS, start_date=start, end_date=end,
                           frequency=frequency, adjustflag="3")

    def adjust_factor(self, code: str, start: str, end: str) -> FetchResult:
        return self._fetch(self._bs.query_adjust_factor, code=code,
                           start_date=start, end_date=end)

    def dividend(self, code: str, year: str) -> FetchResult:
        return self._fetch(self._bs.query_dividend_data, code=code,
                           year=year, yearType="report")

    def financial(self, code: str, year: int, quarter: int,
                  which: str) -> FetchResult:
        from st_agent.l0.market.errors import MarketValidationError

        try:
            method = _FIN_METHODS[which]
        except KeyError:
            raise MarketValidationError(
                f"未知财务接口 {which!r}；合法 = {sorted(_FIN_METHODS)}"
            ) from None
        return self._fetch(getattr(self._bs, method), code=code,
                           year=year, quarter=quarter)

    def performance_express(self, code: str, start: str,
                            end: str) -> FetchResult:
        return self._fetch(self._bs.query_performance_express_report, code,
                           start_date=start, end_date=end)

    def forecast(self, code: str, start: str, end: str) -> FetchResult:
        return self._fetch(self._bs.query_forecast_report, code,
                           start_date=start, end_date=end)

    # ───────────────────────── 板块类 ─────────────────────────

    def industry(self) -> FetchResult:
        return self._fetch(self._bs.query_stock_industry)

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
        return self._fetch(getattr(self._bs, method))

    # ───────────────────────── 宏观类 ─────────────────────────

    def macro_deposit(self, start: str, end: str) -> FetchResult:
        return self._fetch(self._bs.query_deposit_rate_data,
                           start_date=start, end_date=end)

    def macro_loan(self, start: str, end: str) -> FetchResult:
        return self._fetch(self._bs.query_loan_rate_data,
                           start_date=start, end_date=end)

    def macro_reserve(self) -> FetchResult:
        return self._fetch(self._bs.query_required_reserve_ratio_data,
                           yearType="0")

    def macro_money_month(self) -> FetchResult:
        return self._fetch(self._bs.query_money_supply_data_month)

    def macro_money_year(self) -> FetchResult:
        return self._fetch(self._bs.query_money_supply_data_year)

    # ───────────────────────── 内部工具 ─────────────────────────

    def _fetch(self, func, *args: Any, **kwargs: Any) -> FetchResult:
        """抓一次（调用 + 取结果集）——本类所有公开取数方法的唯一出口。

        截断复核不在此处：由同步引擎在网关侧逐次发起（见模块 docstring）。
        """
        return self._drain(self._call(func, *args, **kwargs))

    def _call(self, func, *args: Any, **kwargs: Any):
        """调一次 baostock 接口（未登录先登；抛裸异常 → ``FetchError``）。

        裸异常（socket 层）一律标可重试：这一类是网络故障而非调用方缺陷。
        """
        if not self._open:
            self.open()
        try:
            return func(*args, **kwargs)
        except (FetchError, FetchUnavailableError):
            raise
        except Exception as exc:
            raise FetchError(
                f"BaoStock 接口调用失败：{exc}", retryable=True
            ) from exc

    @staticmethod
    def _drain(rs) -> FetchResult:
        """取结果集（``error_code`` 非 0 → ``FetchError``；逐行字符串）。"""
        code = getattr(rs, "error_code", "1")
        if code != "0":
            raise FetchError(
                f"BaoStock 查询失败（error_code={code}）："
                f"{getattr(rs, 'error_msg', rs)}",
                error_code=code, retryable=is_retryable_error(code),
            )
        fields = list(getattr(rs, "fields", []))
        rows: list[list[Any]] = []
        try:
            while rs.next():
                rows.append([str(cell) for cell in rs.get_row_data()])
        except FetchError:
            raise
        except Exception as exc:
            raise FetchError(f"BaoStock 结果集读取失败：{exc}",
                             retryable=True) from exc
        return (fields, rows)
