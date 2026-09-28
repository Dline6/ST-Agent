"""L3 错误类型（05-L3；失败显式化）。

分级与 L1 / L2 同构：``L3Error`` 为基类；``SessionValidationError`` 收非法入参
与损坏记录（fail-fast，不静默补默认）；``SessionNotFoundError`` 收寻址失败
（按 id 取不到会话）；``CommandValidationError`` 收快捷指令注册被拒
（命名 / 描述未过 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md) 中性校验）；
``IntentValidationError`` 收意图协议被拒（生成文案未过 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md)
执行点 2，或确认卡未确认即派发）；``ConfigValidationError`` 收配置草稿被拒
（草稿生成文案未过 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md) 执行点 2，
或草稿形状不合 [05 §5](../../../docs/技术架构-v2/05-L3-对话主入口.md) 的契约）。
"""

__all__ = [
    "CommandValidationError",
    "ConfigValidationError",
    "IntentValidationError",
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


class IntentValidationError(L3Error, ValueError):
    """意图协议被拒。

    两类来源：① 生成文案（确认卡条目 / 追问问句 / 方向候选）命中
    [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md) 执行点 2 ``check_output``
    ——**阻断渲染**；② 意图确认卡未经用户确认即被派发。
    ``findings`` 携命中项，供调用方改写而不是盲试。
    """

    def __init__(self, message: str, *, findings: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.findings = findings


class ConfigValidationError(L3Error, ValueError):
    """配置草稿被拒（[05 §5](../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

    两类来源：① 草稿的生成文案（未澄清项的问句 / 来源标签）命中
    [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md) 执行点 2 ``check_output``
    ——**阻断渲染**；② 草稿形状不合契约（目标缺失 / 参数未在目标声明体内 /
    工作流草稿携带身份字段）。
    ``findings`` 携命中项，供调用方改写而不是盲试。
    """

    def __init__(self, message: str, *, findings: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.findings = findings

