"""执行沙箱子系统错误类型（03 §1.5；失败显式化，01 §5）。

- 配置/调用方缺陷（fail-fast 异常）：``SandboxValidationError``
- 越界拦截：**不是异常**——拦截结果是 ``GuardVerdict``（含警示与
  ``BehaviorViolation`` 事件），由调用方转为 ``ResultEnvelope.validation_failed``
  （与 .2 流水线一致：运行时不抛裸异常）。唯一例外是流式出口
  ``SandboxedGateway.stream``（其契约为「产块/抛异常」），越界抛
  ``SandboxViolationError``。
"""

__all__ = [
    "SandboxError",
    "SandboxValidationError",
    "SandboxViolationError",
]


class SandboxError(Exception):
    """执行沙箱子系统错误基类。"""


class SandboxValidationError(SandboxError, ValueError):
    """调用方缺陷（trace_id 缺失、权限声明非法、路径入参非法等）。"""


class SandboxViolationError(SandboxError):
    """越界拦截的异常形态（仅供「产块/抛异常」契约的流式出口使用）。"""
