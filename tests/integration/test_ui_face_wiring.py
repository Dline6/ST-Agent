"""M6 各面端口在**真装配**下端到端（[`T-UI-011.1`]）。

`tests/ui/test_ui_face_ports.py` 用替身钉信封与探针形态；本组用 M5 关卡的**真组合根**走一遍
真链路：真 [`build_m5_runtime`](../../src/st_agent/app.py) 造出门面 → 真回环面出探针。

两件事在此证明（替身覆盖不到）：

1. **七个门面各持真句柄**——不是空壳对象：`skills.registry` 就是 L1 的 `SkillRegistry`，
   `graph.graph` 就是 L2 的 `MemoryGraph`，余同；`workspace` 持有能回 `empty` 的真读面
   （[`T-UI-013`] 起接线，此前**刻意**不造它以区分「未接线」与「接线了但无数据」）；
2. **七面探针在真组合根上全 `ok`**——[01 §5] 的六态不可混用：有门面就该是 `ok`，
   而「面在但取不到数」由各面自己的信封说，不靠探针糊过去。

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
"""七个新面；真组合根为**每一个**造门面（`workspace` 自 [`T-UI-013`] 起亦在列）。"""


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


def test_workspace_face_is_wired_on_the_real_root(m5) -> None:
    """[`T-UI-013`]：`workspace` 在真组合根上**已接线**——空工作区回 `empty` ＋ 原因。

    「未接线」与「接线了但没有钉住物」是两回事（[01 §5] 六态不可混用）：前者是
    `unavailable` ＋ 点名装配归属，后者是 `empty` ＋ 说明。本叶之前它恒为前者。
    """
    envelope = m5.m5.workspace.pins()
    assert envelope.status == "empty"
    assert envelope.reason


def test_seven_faces_probe_ok_on_the_real_root(m5) -> None:
    """GWT-3：真组合根 + 真回环面上——**七面**探针全 `ok` 且各自点名自己的面名。"""
    runtime = m5.m5
    with serve(dev=False, **{face: getattr(runtime, face, None) for face in _FACES}) as running:
        payloads = {face: _get(running, f"/api/{face}/status") for face in _FACES}
    for face in _FACES:
        assert payloads[face]["status"] == "ok", face
        assert payloads[face]["data"] == {"face": face, "available": True}


def test_existing_memory_port_still_works_on_the_same_root(m5) -> None:
    """既有端口不被本叶波及：同一真根上 `memory` 面照旧（`empty`，因记忆里无持仓）。"""
    with serve(dev=False, memory=m5.m5.memory) as running:
        assert _get(running, "/api/memory/holdings")["status"] == "empty"


# ── 生产入口的注入面 ────────────────────────────────────────────────────────
class _PortStub:
    """带七个面属性的运行时替身（与生产组合根同形）。"""

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
    """真 `run_backend` 把七个端口**逐个**透传（一个不少、一个不错）。"""
    monkeypatch.setattr(sys, "stdin", io.StringIO("stop\n"))
    services = {face: object() for face in _FACES}
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
        assert box[face] is services[face]


def test_run_backend_passes_none_for_a_root_without_the_face(tmp_path, monkeypatch) -> None:
    """早期里程碑的组合根（M1–M4）没造 `workspace` ⇒ 入口传 `None`，不启动失败。"""
    monkeypatch.setattr(sys, "stdin", io.StringIO("stop\n"))
    runtime = _PortStub(**{face: object() for face in _FACES if face != "workspace"})
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
    assert box["workspace"] is None
