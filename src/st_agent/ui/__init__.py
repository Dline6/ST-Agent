"""表现层（跨层工程 `T-UI-*`）——各层的**客户端组合根**，不是第七层（[00 §1.1]；[D-060]）。

骨架构成：无构建静态前端（``web/``）+ 本机回环通信面（:mod:`st_agent.ui.server`）
+ 信封的 JSON 与渲染语义（:mod:`st_agent.ui.envelope`）。本叶范围见任务 `T-UI-001.2`。

**再导出改为惰性**（PEP 562，[T-UI-002.3]）：桌面壳以 `st_agent.ui.shell` 的名字导入本包，
若这里急切 import `ui.app` / `ui.server`，壳就会顺带拖进 `ui.envelope → l3` 乃至整个后端——
「壳不含业务逻辑」会在**打包产物**上落空。故按下表按需取。
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - 仅供类型标注与静态检查
    from st_agent.ui.app import WEB_ROOT, UiApp, build_ui
    from st_agent.ui.envelope import envelope_payload
    from st_agent.ui.errors import DevSurfaceUnavailable, UiError
    from st_agent.ui.neutrality_gate import GateVerdict, NeutralityGate
    from st_agent.ui.registry import REGISTRY, ComponentSpec, slot_gaps, spec_for
    from st_agent.ui.security import TOKEN_HEADER, RequestGuard, new_token
    from st_agent.ui.server import RunningUi, UiServer, serve

__all__ = [
    "REGISTRY",
    "TOKEN_HEADER",
    "WEB_ROOT",
    "ComponentSpec",
    "DevSurfaceUnavailable",
    "GateVerdict",
    "NeutralityGate",
    "RequestGuard",
    "RunningUi",
    "UiApp",
    "UiError",
    "UiServer",
    "build_ui",
    "envelope_payload",
    "new_token",
    "serve",
    "slot_gaps",
    "spec_for",
]

#: 再导出名 → 定义它的模块（惰性 import 的来源）。
_LAZY: dict[str, str] = {
    "REGISTRY": "st_agent.ui.registry",
    "ComponentSpec": "st_agent.ui.registry",
    "slot_gaps": "st_agent.ui.registry",
    "spec_for": "st_agent.ui.registry",
    "TOKEN_HEADER": "st_agent.ui.security",
    "RequestGuard": "st_agent.ui.security",
    "new_token": "st_agent.ui.security",
    "WEB_ROOT": "st_agent.ui.app",
    "UiApp": "st_agent.ui.app",
    "build_ui": "st_agent.ui.app",
    "envelope_payload": "st_agent.ui.envelope",
    "DevSurfaceUnavailable": "st_agent.ui.errors",
    "UiError": "st_agent.ui.errors",
    "GateVerdict": "st_agent.ui.neutrality_gate",
    "NeutralityGate": "st_agent.ui.neutrality_gate",
    "RunningUi": "st_agent.ui.server",
    "UiServer": "st_agent.ui.server",
    "serve": "st_agent.ui.server",
}


def __getattr__(name: str) -> Any:
    """按 :data:`_LAZY` 惰性取再导出名（未知名照样 `AttributeError`）。"""
    try:
        module_name = _LAZY[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    return getattr(import_module(module_name), name)


def __dir__() -> list[str]:
    return sorted(set(__all__) | set(globals()))
