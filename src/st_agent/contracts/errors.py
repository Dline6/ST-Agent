"""契约级校验失败。

供各契约模块的校验器抛出（pydantic 会包装为 ``ValidationError``，
异常链中保留本类型）；上层应映射为 ResultEnvelope 的 ``validation_failed``
分支（01 §5），不得静默吞掉（失败显式化，架构总览 §6）。
"""

__all__ = ["ContractViolation"]


class ContractViolation(ValueError):
    """违反平台共享契约（01-平台共享契约）的结构/语义约束。"""
