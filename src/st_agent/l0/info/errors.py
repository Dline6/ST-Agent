"""信息面（互补域）错误类型（T-L0-010；失败显式化，01 §5）。

与 ``st_agent.l0.market.errors`` 同构——两个面各自独立，别互相 import：

- 配置面（fail-fast 异常）：``InfoValidationError``（未知域/源、非法参数、
  稿件形态不合口径等发起前校验）
- 执行面（运行时失败一律走 ``ResultEnvelope``，不抛异常）：源不可达 →
  ``unavailable``；抓取/入库失败 → ``failed``（带可查 ``log_ref``）
- 抓取器协议异常（``InfoFetchError`` / ``InfoFetchUnavailableError``）：由
  同步闭包译为网关 ``Egress*`` 体系后经网关映射为信封（02 §6 唯一出口）
"""

__all__ = [
    "InfoError",
    "InfoFetchError",
    "InfoFetchUnavailableError",
    "InfoValidationError",
]


class InfoError(Exception):
    """信息面子系统错误基类。"""


class InfoValidationError(InfoError, ValueError):
    """调用参数非法 / 稿件形态不合口径（发起前校验，不产生审计与状态变更）。"""


class InfoFetchError(InfoError):
    """抓取器执行失败基类（同步闭包译为 ``EgressError`` → ``failed`` 信封）。"""


class InfoFetchUnavailableError(InfoFetchError):
    """源不可达（同步闭包译为 ``EgressUnavailableError`` → ``unavailable`` 信封）。"""
