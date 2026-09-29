"""表现层（跨层工程 `T-UI-*`）的错误类型。

本包**不是第七层**——它是各层的客户端组合根（[00 §1.1]；[D-060]）。故错误类型只服务
「装配与传输」面，不承载任何域语义。
"""

from __future__ import annotations

__all__ = ["DevSurfaceUnavailable", "UiError"]


class UiError(RuntimeError):
    """表现层装配 / 传输面错误的基类。"""


class DevSurfaceUnavailable(UiError):
    """请求启用 dev 面，但 dev 面不可用（发布构建已物理剔除）。

    见 [D-060] ④I 的「发布包必须剔除」——剔除后 `dev=True` 必须**显式失败**，
    不得静默降级成一个「看起来能用」的服务。
    """
