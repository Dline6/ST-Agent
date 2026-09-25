"""出网审计网关错误类型（02 §6；失败显式化，01 §5）。

- 配置面（fail-fast 异常）：``NetValidationError`` / ``CapabilityExistsError`` /
  ``CapabilityNotFoundError``
- 执行面（运行时失败一律走 ``ResultEnvelope``，不抛异常）：
  目标不可达/离线拦截 → ``unavailable``；超时/取消/发送失败 → ``failed``
- 流式形态（``EgressGateway.stream``）下执行失败以 ``Egress*`` 异常上报
  （审计事件已先行落盘），调用方按需映射为信封；``EgressGateway.execute``
  直接返回信封。
"""

__all__ = [
    "CapabilityExistsError",
    "CapabilityNotFoundError",
    "EgressCancelledError",
    "EgressError",
    "EgressTimeoutError",
    "EgressUnavailableError",
    "NetError",
    "NetValidationError",
]


class NetError(Exception):
    """出网审计子系统错误基类。"""


class NetValidationError(NetError, ValueError):
    """网关调用参数 / 能力登记项非法（发起前校验，不产生审计记录）。"""


class CapabilityExistsError(NetError):
    """同标识能力已登记——更新走 ``update``，不得静默覆盖。"""


class CapabilityNotFoundError(NetError, KeyError):
    """能力标识不存在（调用方传了未登记的 capability_id，属调用方缺陷）。"""


class EgressError(NetError):
    """出网执行失败基类（``stream`` 形态上报；``execute`` 形态映射为信封）。"""


class EgressUnavailableError(EgressError):
    """目标不可达或离线拦截（映射为 ``unavailable`` 信封）。"""


class EgressTimeoutError(EgressError):
    """出网执行超时（映射为 ``failed`` 信封，原因注明超时）。"""


class EgressCancelledError(EgressError):
    """出网执行被取消（映射为 ``failed`` 信封，原因注明取消）。"""
