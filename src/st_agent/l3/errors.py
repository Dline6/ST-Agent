"""L3 错误类型（05-L3；失败显式化）。

分级与 L1 / L2 同构：``L3Error`` 为基类；``SessionValidationError`` 收非法入参
与损坏记录（fail-fast，不静默补默认）；``SessionNotFoundError`` 收寻址失败
（按 id 取不到会话）；``CommandValidationError`` 收快捷指令注册被拒
（命名 / 描述未过 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md) 中性校验）。
"""

__all__ = [
    "CommandValidationError",
    "L3Error",
    "SessionNotFoundError",
    "SessionValidationError",
]


class L3Error(Exception):
    """L3 对话主入口子系统错误基类。"""


class SessionValidationError(L3Error, ValueError):
    """会话 / 消息 / 查询入参非法，或 ``chat_history`` 分区内的记录损坏。"""


class SessionNotFoundError(L3Error, KeyError):
    """按 ``session_id`` 取不到会话（寻址失败）。"""


class CommandValidationError(L3Error, ValueError):
    """快捷指令注册被拒（命中 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md) 命名校验）。

    ``verdict`` 携命中项与中性修改建议，供调用方改用合规命名而不是盲试。
    """

    def __init__(self, message: str, *, suggestions: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.suggestions = suggestions
