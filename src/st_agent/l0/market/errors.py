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
    """抓取器执行失败基类（同步闭包译为 ``EgressError`` → ``failed`` 信封）。"""


class FetchUnavailableError(FetchError):
    """数据源不可达（同步闭包译为 ``EgressUnavailableError`` → ``unavailable`` 信封）。"""
