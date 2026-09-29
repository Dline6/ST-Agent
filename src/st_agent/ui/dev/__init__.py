"""表现层的 **dev 面**（浏览器直开 + 六态示例）。

本子包在发布构建里被 ``[tool.setuptools.packages.find] exclude`` **物理剔除**
（[D-060] ④I：dev 可达性只在开发 / 验收期开启，发布包必须剔除）。故骨架服务在
``dev=True`` 却加载不到本子包时**显式失败**，不静默降级。

本子包**不含任何域逻辑**——它只是「同一套 UI 的浏览器可达性 + 六态走查入口」。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from st_agent.ui.dev.samples import (
    DESCRIPTION_KINDS,
    SAMPLE_STATUSES,
    sample_description,
    sample_envelope,
)

if TYPE_CHECKING:  # pragma: no cover - 仅供类型标注
    from st_agent.ui.server import RunningUi

__all__ = [
    "DESCRIPTION_KINDS",
    "SAMPLE_STATUSES",
    "browser_url",
    "dev_script_tag",
    "sample_description",
    "sample_envelope",
    "web_root",
]

_DEV_WEB = Path(__file__).resolve().parent / "web"


def web_root() -> Path:
    """dev 静态资产根（服务端在 ``/dev/`` 前缀下解析）。"""
    return _DEV_WEB


def dev_script_tag() -> str:
    """注入 `index.html` 的 dev 脚本标签（仅 dev 开启时；见 `DEV_SCRIPT_MARKER`）。"""
    return '<script type="module" src="/dev/devpanel.js"></script>'


def browser_url(running: "RunningUi") -> str:
    """带令牌的浏览器地址。

    令牌走 URL **fragment**——fragment 不发送到服务端、不进 ``Referer``；
    前端只在内存里持有它，不落任何浏览器存储。
    """
    return f"{running.base_url}/#t={running.token}"
