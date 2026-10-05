"""桌面壳的异常面（[`T-UI-002.2`]）。

壳的依赖（pywebview / pystray）收在可选 extra ``[shell]``，**发布/CI 不装**——故壳边模块
惰性导入，缺依赖时**显式**抛，不静默降级成「没有窗口的窗口应用」。同款先例：
:class:`st_agent.ui.errors.DevSurfaceUnavailable`（dev 面被发布构建剔除时的显式失败）。
"""

from __future__ import annotations

__all__ = ["ShellError", "ShellSurfaceUnavailable"]


class ShellError(Exception):
    """桌面壳的基类异常。"""


class ShellSurfaceUnavailable(ShellError):
    """壳的图形依赖不可用（未安装 ``[shell]`` extra，或该平台不支持）。

    消息须点名**缺什么**与**怎么装**——壳无法启动时的唯一线索就是它。
    """
