"""桌面壳的异常面（[`T-UI-002.2`]）。

壳的依赖（pywebview / pystray）收在可选 extra ``[shell]``，**发布/CI 不装**——故壳边模块
惰性导入，缺依赖时**显式**抛，不静默降级成「没有窗口的窗口应用」。同款先例：
:class:`st_agent.ui.errors.DevSurfaceUnavailable`（dev 面被发布构建剔除时的显式失败）。
"""

from __future__ import annotations

__all__ = ["ShellCancelled", "ShellError", "ShellSurfaceUnavailable"]


class ShellError(Exception):
    """桌面壳的基类异常。"""


class ShellCancelled(Exception):
    """用户在口令交互里取消（[`T-UI-002.4.2`]）——**用户的决定**，不是错误。

    刻意**不继承** :class:`ShellError`：两者收场不同（取消＝中性说明 + 退出码 0；
    启动失败＝点名原因 + 非零退出码）。混成一族会让调用方只能靠消息文本区分。
    """


class ShellSurfaceUnavailable(ShellError):
    """壳的图形依赖不可用（未安装 ``[shell]`` extra，或该平台不支持）。

    消息须点名**缺什么**与**怎么装**——壳无法启动时的唯一线索就是它。
    """
