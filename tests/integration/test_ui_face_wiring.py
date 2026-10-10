"""M6 各面端口在**真装配**下端到端（[`T-UI-011.1`]）。

`tests/ui/test_ui_face_ports.py` 用替身钉信封与探针形态；本组用 M5 关卡的**真组合根**走一遍
真链路：真 [`build_m5_runtime`](../../src/st_agent/app.py) 造出门面 → 真回环面出探针。

两件事在此证明（替身覆盖不到）：

1. **六个门面各持真句柄**——不是空壳对象：`skills.registry` 就是 L1 的 `SkillRegistry`，
   `graph.graph` 就是 L2 的 `MemoryGraph`，余同；
2. **`workspace` 在真组合根上仍 `unavailable` + 点名**——本叶**刻意**不给它造门面
   （钉住物的读面归 [`T-UI-013`]），故「未接线」与「接线了」在真根上确实可分
   （[01 §5] 六态不可混用）。

另有一条**生产入口注入**的守卫：真 `run_backend` 确实把七个端口透传给 `serve_ui`
（只把 `serve_ui` 换成捕获替身——注入面是入口的职责，不是组合根的）。
"""

from __future__ import annotations

import http.client
import io
import json
import sys
from typing import Any

import pytest
from rig import ROOT_NAME
from rig_m5 import seeded_m5

from st_agent.__main__ import run_backend
from st_agent.ui.security import TOKEN_HEADER
from st_agent.ui.server import serve

_FACES = ("workspace", "skills", "mcp", "graph", "deliberation", "delivery", "settings")
"""七个新面；真组合根为其中六个造门面，`workspace` 刻意不造。"""


def _get(running, path: str) -> dict:
    conn = http.client.HTTPConnection(running.host, running.port, timeout=5)
    try:
        conn.request("GET", path, headers={TOKEN_HEADER: running.token})
        return json.loads(conn.getresponse().read().decode("utf-8"))
    finally:
        conn.close()


@pytest.fixture()
def m5(tmp_path):
    """真 M5 装配（真 Store / 真 MarketDb / 真官方 Pack / 真 L0–L6 编排；只脚本化出网那一跳）。"""
    return seeded_m5(tmp_path / ROOT_NAME)


def test_facades_hold_the_real_handles(m5) -> None:
    """六个门面**不是空壳**：各自持有对应的真句柄（同一对象，非副本）。"""
    runtime = m5.m5
    l1 = runtime.m1.runtime
    assert runtime.skills.registry is l1.skills
    assert runtime.mcp.servers is l1.mcp_servers
    assert runtime.mcp.permissions is l1.mcp_permissions
    assert runtime.mcp.mapper is l1.mcp_mapper
    assert runtime.mcp.machine is l1.mcp_machine
    assert runtime.graph.graph is runtime.m1.graph
    assert runtime.graph.reader is runtime.m1.reader
    assert runtime.graph.writer is runtime.m1.writer
    assert runtime.deliberation.roster is runtime.m2.roster
    assert runtime.deliberation.viewer is runtime.m2.viewer
    assert runtime.delivery.budget is runtime.m3.l5.budget
    assert runtime.delivery.delivery is runtime.m3.l5.delivery
    assert runtime.settings.config_registry is l1.config_registry
    assert runtime.settings.store is runtime.store


def test_workspace_has_no_facade_on_the_real_root(m5) -> None:
    """`workspace` 刻意不装配——钉住物读面归 [`T-UI-013`]（不造空门面冒充「面在」）。"""
    assert getattr(m5.m5, "workspace", None) is None


def test_six_faces_probe_ok_and_workspace_is_unavailable(m5) -> None:
    """GWT-3：真组合根 + 真回环面上——六面 `ok`、`workspace` `unavailable` + 点名。"""
    runtime = m5.m5
    with serve(dev=False, **{face: getattr(runtime, face, None) for face in _FACES}) as running:
        payloads = {face: _get(running, f"/api/{face}/status") for face in _FACES}
    for face in _FACES:
        if face == "workspace":
            continue
        assert payloads[face]["status"] == "ok", face
        assert payloads[face]["data"] == {"face": face, "available": True}
    assert payloads["workspace"]["status"] == "unavailable"
    assert "未接入" in payloads["workspace"]["reason"]


def test_existing_memory_port_still_works_on_the_same_root(m5) -> None:
    """既有端口不被本叶波及：同一真根上 `memory` 面照旧（`empty`，因记忆里无持仓）。"""
    with serve(dev=False, memory=m5.m5.memory) as running:
        assert _get(running, "/api/memory/holdings")["status"] == "empty"


# ── 生产入口的注入面 ────────────────────────────────────────────────────────
class _PortStub:
    """带七个面属性、**独缺 `workspace`** 的运行时替身（与生产组合根同形）。"""

    def __init__(self, **faces: Any) -> None:
        self.chat = object()
        for face, value in faces.items():
            setattr(self, face, value)


def _capture_serve(box: dict) -> Any:
    def _serve(**kwargs: Any) -> Any:
        box.update(kwargs)

        class _Running:
            host = "127.0.0.1"
            port = 1
            token = "t"

            def shutdown(self) -> None:
                pass

        return _Running()

    return _serve


def test_run_backend_injects_all_seven_face_ports(tmp_path, monkeypatch) -> None:
    """真 `run_backend` 把七个端口**逐个**透传（`workspace` 缺席 ⇒ `None`，与真根一致）。"""
    monkeypatch.setattr(sys, "stdin", io.StringIO("stop\n"))
    services = {face: object() for face in _FACES if face != "workspace"}
    runtime = _PortStub(**services)
    box: dict = {}
    stream = io.StringIO()

    code = run_backend(
        root=tmp_path / ROOT_NAME,
        host="127.0.0.1",
        port=0,
        json_handshake=True,
        passphrase="ignored-by-stub",
        out=stream,
        build_runtime=lambda *a, **k: runtime,
        serve_ui=_capture_serve(box),
        control_stdin=True,
        ambient_interval=0,
        read_passphrase=lambda: None,
    )

    assert code == 0
    for face in _FACES:
        assert face in box, f"入口未透传端口 {face!r}"
    assert box["workspace"] is None
    for face, service in services.items():
        assert box[face] is service
