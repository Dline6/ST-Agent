"""`tests/ui/` 的公共夹具：起一个**真服务**（回环 + OS 分配端口 + 启动令牌）。

不做桩：本包的要点恰恰是「回环绑定 + 令牌握手 + `Host` / `Origin` 校验」这些**只有真
HTTP 面才成立**的性质（`T-UI-001.2` 假设 `A3`）。
"""

from __future__ import annotations

import http.client
import json
from dataclasses import dataclass
from typing import Mapping

import pytest

from st_agent.ui.security import TOKEN_HEADER
from st_agent.ui.server import RunningUi, serve


@dataclass(frozen=True)
class Response:
    """一次响应（头名已小写，便于断言）。"""

    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> dict:
        return json.loads(self.body.decode("utf-8"))


@pytest.fixture
def ui_server():
    """非 dev 的服务实例（生产口径）。"""
    with serve(dev=False) as running:
        yield running


@pytest.fixture
def ui_server_dev():
    """dev 开启的服务实例（走查面板 + 示例端点）。"""
    with serve(dev=True) as running:
        yield running


@pytest.fixture
def http_get():
    """发一个 GET。``headers`` 为空 = 完全不带令牌（用于构造拒绝场景）。"""

    def _get(
        running: RunningUi, path: str, headers: Mapping[str, str] | None = None
    ) -> Response:
        conn = http.client.HTTPConnection(running.host, running.port, timeout=5)
        try:
            conn.request("GET", path, headers=dict(headers or {}))
            raw = conn.getresponse()
            return Response(
                status=raw.status,
                headers={key.lower(): value for key, value in raw.getheaders()},
                body=raw.read(),
            )
        finally:
            conn.close()

    return _get


@pytest.fixture
def http_post():
    """发一个 POST。``body`` 为 ``bytes`` 时原样发送（构造畸形体用）；为映射时序列化为 JSON。

    不吞任何传输异常——「服务端把连接丢了」正是这里的观测点（``RemoteDisconnected`` 应
    以用例失败暴露，见 ``test_ui_chat_errors``）。
    """

    def _post(
        running: RunningUi,
        path: str,
        body: object = None,
        headers: Mapping[str, str] | None = None,
    ) -> Response:
        if body is None:
            payload = b""
        elif isinstance(body, bytes):
            payload = body
        else:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        conn = http.client.HTTPConnection(running.host, running.port, timeout=5)
        try:
            conn.request("POST", path, body=payload, headers=dict(headers or {}))
            raw = conn.getresponse()
            return Response(
                status=raw.status,
                headers={key.lower(): value for key, value in raw.getheaders()},
                body=raw.read(),
            )
        finally:
            conn.close()

    return _post


@pytest.fixture
def auth():
    """默认的**合法**请求头：仅带令牌，``Host`` 由客户端按连接自动填回环地址。"""

    def _auth(running: RunningUi, **extra: str) -> dict[str, str]:
        headers = {TOKEN_HEADER: running.token}
        headers.update(extra)
        return headers

    return _auth
