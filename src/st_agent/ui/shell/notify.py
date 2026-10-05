"""原生通知的**最小接线**（[`T-UI-002.2`]；[D-076] ④）。

本模块只做两件事：① 给出「系统通知」这一**原生能力入口**；② 用一次**测试通知**
证明它在桌面上可达。**业务通知链**（信号 → 渠道 → 投递留痕，[07 §3]）归
[`T-L5-002`](../../tasks/T-L5-002-渠道适配器投递编排与升级链.md)——本模块不做去重 /
频控 / 升级链，也不留投递痕迹，故**不得**被当作触达渠道使用。

三条平台路径都是**本地子进程**（无第三方模块、无网络 IO）：

| 平台 | 手段 |
|---|---|
| Windows | PowerShell 的 WinRT toast（``Windows.UI.Notifications``，系统自带） |
| macOS | ``osascript -e 'display notification …'``（非 bundle 进程的署名可能显示为解释器） |
| Linux | ``notify-send``（需桌面通知守护进程；无则抛错，不静默） |
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Sequence
from typing import Any

__all__ = ["notify_command", "send_notification"]

_RUNNER_TIMEOUT = 15.0


def notify_command(title: str, body: str, *, system: str | None = None) -> list[str]:
    """产出「发一条系统通知」的**本地命令**（纯函数：平台分支可逐条断言）。"""
    system = sys.platform if system is None else system
    if system.startswith("win"):
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                _powershell_toast(title, body)]
    if system == "darwin":
        return ["osascript", "-e",
                f"display notification {_applescript(body)} with title {_applescript(title)}"]
    return ["notify-send", title, body]


def send_notification(
    title: str,
    body: str,
    *,
    system: str | None = None,
    runner: Callable[[Sequence[str]], Any] | None = None,
) -> None:
    """执行通知命令；``runner`` 可注入（用例断言命令、不真弹窗）。

    非零退出即抛 :class:`subprocess.CalledProcessError`——通知发不出去是**显式**失败，
    不静默（[00 §6] 失败显式化）。
    """
    command = notify_command(title, body, system=system)
    run = _run if runner is None else runner
    run(command)


def _run(command: Sequence[str]) -> None:
    subprocess.run(
        list(command), check=True, timeout=_RUNNER_TIMEOUT,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _applescript(text: str) -> str:
    """AppleScript 字符串字面量（反斜杠与双引号转义）。"""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _powershell_literal(text: str) -> str:
    """PowerShell 单引号字面量（单引号翻倍即转义，无变量展开）。"""
    return "'" + text.replace("'", "''") + "'"


def _powershell_toast(title: str, body: str) -> str:
    """WinRT toast 的 PowerShell 片段（两段文本由**单引号字面量**写入，无注入面）。"""
    return (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
        "ContentType = WindowsRuntime] | Out-Null; "
        "$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
        "$texts = $xml.GetElementsByTagName('text'); "
        f"$texts.Item(0).AppendChild($xml.CreateTextNode({_powershell_literal(title)})) | Out-Null; "
        f"$texts.Item(1).AppendChild($xml.CreateTextNode({_powershell_literal(body)})) | Out-Null; "
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml); "
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('ST Agent')"
        ".Show($toast)"
    )
