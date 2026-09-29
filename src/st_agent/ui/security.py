"""本机回环服务的请求守卫（[00 §1.1]；[D-060] ③ 取 F）。

校验分两档，因为**初始文档导航无法携带自定义请求头**（浏览器打开 `#t=<token>` 的地址时
只会发一个裸 GET）：

- **静态壳**（`/` 与资产）：只校 ``Host``（挡 **DNS rebinding**）与存在的 ``Origin``。
  壳里没有任何用户数据——数据全在 ``/api/*`` 之后。
- **数据面**（``/api/*``）：再加 ``X-ST-Token`` —— 令牌经 URL fragment 下发、由前端
  读出后放进请求头，故只有真拿到该地址的页面才用得上数据面。

本服务**不回任何 ``Access-Control-Allow-*`` 头**：自定义头 ``X-ST-Token`` 使跨源请求
必然触发 CORS 预检，而预检拿不到许可头即失败——这是令牌之外的第二道跨源闸门。
"""

from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

__all__ = ["TOKEN_HEADER", "GuardVerdict", "RequestGuard", "new_token", "resolve_static"]

TOKEN_HEADER = "X-ST-Token"
"""启动令牌的请求头名（[D-060] ③F）。"""


def new_token() -> str:
    """生成一次启动期令牌（进程级；重启即换）。"""
    return secrets.token_urlsafe(32)


@dataclass(frozen=True)
class GuardVerdict:
    """一次守卫判定的结果。``allowed`` 为假时 ``status`` 与 ``reason`` 必填。"""

    allowed: bool
    status: int = 0
    reason: str = ""


def _header(headers: Mapping[str, str], name: str) -> str:
    """大小写不敏感地取一个请求头（``http.server`` 传进来的是 ``email.message.Message``）。"""
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return ""


class RequestGuard:
    """回环请求守卫。由 :func:`st_agent.ui.server.serve` 在**绑定后**用实际端口构造。"""

    def __init__(self, *, token: str, host: str, port: int) -> None:
        self._token = token
        self._host = host
        self._port = port

    @property
    def authority(self) -> str:
        """允许的 ``Host`` 值。"""
        return f"{self._host}:{self._port}"

    @property
    def origin(self) -> str:
        """允许的 ``Origin`` 值（回环 http）。"""
        return f"http://{self.authority}"

    def check(self, headers: Mapping[str, str], *, require_token: bool = True) -> GuardVerdict:
        """校验一次请求。

        ``require_token`` 对**数据面**（``/api/*``）为真；静态壳为假——初始文档导航
        无法携带自定义头，壳里也没有用户数据。返回 ``allowed=False`` 时调用方**不得**
        返回任何数据。
        """
        if require_token:
            token = _header(headers, TOKEN_HEADER)
            if not token or not hmac.compare_digest(token, self._token):
                return GuardVerdict(False, 401, "缺少或无效的启动令牌")
        if _header(headers, "Host") != self.authority:
            return GuardVerdict(False, 403, "请求 Host 与回环地址不符")
        origin = _header(headers, "Origin")
        if origin and origin != self.origin:
            return GuardVerdict(False, 403, "请求 Origin 与回环地址不符")
        return GuardVerdict(True)


def resolve_static(root: Path, url_path: str) -> Path | None:
    """把 URL 路径解析到 ``root`` 下的真实文件；越界或不存在返回 ``None``。

    用 ``resolve()`` 之后的前缀比较挡路径穿越（``..`` / 软链 / 空字节）。
    """
    if "\x00" in url_path:
        return None
    root_resolved = root.resolve()
    candidate = (root_resolved / url_path.lstrip("/")).resolve()
    if not candidate.is_relative_to(root_resolved):
        return None
    if not candidate.is_file():
        return None
    return candidate
