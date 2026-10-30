"""本地命令执行面的错误类型（T-AGT-016.1；01 §10 / 02 §7）。

- 配置面（fail-fast 异常）：``CommandConfigError``——条目取值非法或落盘损坏
  **显式抛**，不静默回退默认值。
- 执行面：进程无法启动 → ``CommandError``；原语把它映射为 ``unavailable``。
  命令自身非零退出**不是**错误（那是可观察结果），只有超时 / 启动失败才是。
"""

from __future__ import annotations

__all__ = ["CommandConfigError", "CommandError"]


class CommandError(Exception):
    """命令执行面错误（启动失败 / 参数不可用）。"""


class CommandConfigError(CommandError):
    """``local-exec/*`` 条目取值非法或落盘损坏——显式抛，不静默回退默认值。"""
