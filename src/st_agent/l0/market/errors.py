"""数据源缓存错误类型（02 §5；失败显式化，01 §5）。

- 配置面（fail-fast 异常）：``MarketValidationError``（未知任务、非法窗口、
  分钟线缺关注池等发起前校验）
- 执行面（运行时失败一律走 ``ResultEnvelope``，不抛异常）：
  源不可达/离线拦截/库缺失 → ``unavailable``；抓取失败/入库失败 →
  ``failed``（带可查 ``log_ref``）；上游主档缺失 → ``failed``（显式标注
  前置依赖）；非法 SQL → ``validation_failed``
- 抓取器协议异常（``FetchError`` / ``FetchUnavailableError``）：由同步闭包
  翻译为网关 ``Egress*`` 体系后经网关映射为信封（02 §6 唯一出口）。
"""

__all__ = [
    "FetchError",
    "FetchUnavailableError",
    "MarketError",
    "MarketValidationError",
]


class MarketError(Exception):
    """数据源缓存子系统错误基类。"""


class MarketValidationError(MarketError, ValueError):
    """同步/查询调用参数非法（发起前校验，不产生审计记录与状态变更）。"""


class FetchError(MarketError):
    """抓取器执行失败基类（同步闭包译为 ``EgressError`` → ``failed`` 信封）。

    :param error_code: 源端错误码原文（无则 ``None``）。**保留它是为了可判定**——
    只留 ``error_msg`` 时，「网络/会话类可重试」与「调用方参数类不可重试」在调用侧
    无从区分（T-L0-017.2 GWT-1）。
    :param retryable: 该错误是否值得重试（由抓取器按源端语义判定，见
        ``live.RETRYABLE_ERROR_CODES``）；同步引擎只读此标记，不解析错误码。
    """

    def __init__(self, message: str, *, error_code: str | None = None,
                 retryable: bool = False) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable


class FetchUnavailableError(FetchError):
    """数据源不可达（同步闭包译为 ``EgressUnavailableError`` → ``unavailable`` 信封）。

    与 :class:`FetchError` 的区别在**信封落点**（``unavailable`` vs ``failed``），
    不在可否重试——登录失败、连接被拒这类瞬时故障同属可重试面（``retryable=True``），
    重试用尽后仍按原语义抛本异常，信封口径不变。
    """
