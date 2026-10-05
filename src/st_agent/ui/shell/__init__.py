"""桌面壳（[`T-UI-002.2`]；[D-060]/[D-076]）——表现层的**原生呈现面**。

壳只承载**原生窗口 / 托盘 / 自启 / 原生通知接线**，**不含任何业务逻辑**，也**不提供任何
壳专有通道**（无 JS 桥 / IPC）：窗口只是本机回环面的一个客户端，数据一律由页面自己经
HTTP 取。进程边界见 [00 §1.1](../../../../docs/技术架构-v2/00-架构总览.md)——壳与后端是
两个进程，后端是主体。

包内分层：`backend`（拉起 / 握手 / 回收）· `autostart`（三平台登录项）· `notify`（最小
通知接线）· `passphrase`（口令索取 / 记忆 / 回传）· `app`（装配与生命周期，**全部语义在此**）·
`window` / `tray`（pywebview / pystray 薄边，惰性导入）。图形依赖收在 extra ``[shell]``、
口令记忆收在 ``[keyring]``，本包在**未装**它们时仍可导入（只有真正开窗 / 装托盘 / 弹口令框
时才抛 :class:`ShellSurfaceUnavailable`）。
"""

from __future__ import annotations

from st_agent.ui.shell.app import Shell, run_shell
from st_agent.ui.shell.autostart import LoginItem, login_item
from st_agent.ui.shell.backend import BackendHandle, BackendSpec, start_backend
from st_agent.ui.shell.errors import ShellCancelled, ShellError, ShellSurfaceUnavailable
from st_agent.ui.shell.notify import notify_command, send_notification
from st_agent.ui.shell.passphrase import (
    KeyringVault,
    NativePassphrasePrompt,
    PassphraseRequest,
    PassphraseResolver,
    build_vault,
)
from st_agent.ui.shell.tray import MenuItem

__all__ = [
    "BackendHandle",
    "BackendSpec",
    "KeyringVault",
    "LoginItem",
    "MenuItem",
    "NativePassphrasePrompt",
    "PassphraseRequest",
    "PassphraseResolver",
    "Shell",
    "ShellCancelled",
    "ShellError",
    "ShellSurfaceUnavailable",
    "build_vault",
    "login_item",
    "notify_command",
    "run_shell",
    "send_notification",
    "start_backend",
]
