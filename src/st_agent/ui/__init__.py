"""表现层（跨层工程 `T-UI-*`）——各层的**客户端组合根**，不是第七层（[00 §1.1]；[D-060]）。

骨架构成：无构建静态前端（``web/``）+ 本机回环通信面（:mod:`st_agent.ui.server`）
+ 信封的 JSON 与渲染语义（:mod:`st_agent.ui.envelope`）。本叶范围见任务 `T-UI-001.2`。
"""

from __future__ import annotations

from st_agent.ui.app import WEB_ROOT, UiApp, build_ui
from st_agent.ui.envelope import envelope_payload
from st_agent.ui.errors import DevSurfaceUnavailable, UiError
from st_agent.ui.security import TOKEN_HEADER, RequestGuard, new_token
from st_agent.ui.server import RunningUi, UiServer, serve

__all__ = [
    "TOKEN_HEADER",
    "WEB_ROOT",
    "DevSurfaceUnavailable",
    "RequestGuard",
    "RunningUi",
    "UiApp",
    "UiError",
    "UiServer",
    "build_ui",
    "envelope_payload",
    "new_token",
    "serve",
]
