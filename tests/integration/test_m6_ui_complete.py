"""T-INT-007 · M6 集成关卡 · 界面完备（[00 §5] 端到端数据流 + [11-sitemap §3] 各 Flow）。

把 M6 各叶交付的界面**装起来跑一遍端到端**：真组合根（``build_m5_runtime``）＋ 真回环服务
（``serve`` ＋ **生产入口的端口映射** ``ui_ports``），断言经真 HTTP 连接发出。单叶用例全是
注入替身的分面行为；**装配关系与跨屏数据流**恰是它们集体覆盖不到的部分：

- **GWT-1 页面集**：声明的 19 条路径逐条回同一静态壳、未声明的**不**回壳（404）、前端登记的
  路径集/状态端点集与后端逐条对得上、壳引用的资产真的可取——[11-sitemap §2.2] 是判据。
- **GWT-2 六态**：四态取自真端点，两态（``failed`` / ``dependency_failed``）在真装配下**只有
  真故障或装配错误**才产，故经 dev 样本面呈现；横切不变量（非 ok 必带 ``reason``、每条信封
  都带 ``render`` 语义段）在**所有**走查到的信封上成立。
- **GWT-3 写面**：写表外的 POST 一律 404 **且零副作用**（真存储的文件路径集不变）；表内每一条
  都在令牌闸门之后（无令牌 ⇒ 401，**不派发**）；表内合法写真的落盘并可经读端点取回。
- **GWT-4 出站点**：装配出来的那个 ``UiApp`` 实例在出站点真的挂着中性化门——违规 ``generated``
  文案被阻断为 ``validation_failed`` 且**不回显原文**；走查到的描述都是已登记型且 ``text_kinds``
  逐槽有标注。
- **GWT-5 拒绝路径**：跨源 ``Origin`` / 外来 ``Host`` ⇒ 403、缺令牌 ⇒ 401、未支持方法 ⇒ 501，
  **每条的信封/响应都真的抵达**；且拒绝之后**复用同一条连接**的下一次请求仍正常（读净的判据）。
- **GWT-6 生产入口**：``ui_ports`` 与 ``serve`` 的端口形参**逐名相等**（新增面没注入即在此现形），
  且入口透传的就是这张表；真根上十三个端口一个不少、各面探针全 ``ok``。
"""

from __future__ import annotations

import io
import json
from typing import Any, get_args

import pytest
from rig import ROOT_NAME
from rig_m6 import (
    SHELL_PATHS,
    declared_pages_js_paths,
    declared_pages_js_status_paths,
    seeded_m6,
    serve_port_names,
    store_paths,
    ui_server,
)

from st_agent.__main__ import run_backend, ui_ports
from st_agent.contracts.result_envelope import EnvelopeStatus
from st_agent.contracts.ui_description import IMPLEMENTED_COMPONENT_TYPES, UiDescription
from st_agent.ui.server import _POST_ROUTES  # noqa: PLC2701 —— 写表是**唯一真相源**，测试不另抄镜像

SIX_STATES = frozenset(get_args(EnvelopeStatus))
"""[01 §5] 的六态（自契约的 ``status`` 字面量取，不另抄一份）。"""

PROBE_ROUTES = {
    "reflection": "/api/reflection/status",
    "evolution": "/api/evolution/status",
    "eco": "/api/eco/status",
    "workspace": "/api/workspace/status",
    "skills": "/api/skills/status",
    "mcp": "/api/mcp/status",
    "graph": "/api/graph/status",
    "deliberation": "/api/deliberation/status",
    "delivery": "/api/delivery/status",
    "settings": "/api/settings/status",
}
"""各面的可用性探针（[`T-UI-011.1`] 起逐面登记）。"""

DESCRIPTION_ROUTES = (
    "/api/skills/catalog",
    "/api/memory/browse?view=list",
    "/api/delivery/reports?limit=5",
    "/api/deliberation/decisions",
)
"""产 ``ok`` UI 描述的读端点（GWT-4 的走查面：覆盖能力 / 记忆 / 触达 / 推理四个分区）。"""

VIOLATING_TITLE = "我认为这只股票会涨"
"""[01 §6] 执行点 2 明列的反例之一（第一人称 ＋ 情感/判断表达）。"""


@pytest.fixture()
def m6(tmp_path):
    """真 M6 装配（真 Store / 真 MarketDb / 真官方 Pack / 真 L0–L6 编排；只脚本化出网那一跳）。"""
    return seeded_m6(tmp_path / ROOT_NAME)


# ───────────────────────── GWT-1 · 页面集 ─────────────────────────


def test_every_declared_page_serves_the_same_shell(m6) -> None:
    """GWT-1：19 条声明路径逐条 200 且回**同一份**静态壳（SPA 回退的唯一形态）。"""
    with ui_server(m6) as ui:
        status, shell = ui.text("/")
        assert status == 200
        for path in sorted(SHELL_PATHS):
            code, body = ui.text(path)
            assert code == 200, path
            assert body == shell, f"{path} 回的壳与 / 不一致"


def test_unregistered_path_does_not_fall_back_to_the_shell(m6) -> None:
    """GWT-1：**未声明**的路径回 404——「逐条可达」因此不是「所有路径都回壳」的假象。"""
    with ui_server(m6) as ui:
        for path in ("/nope", "/settings/nope", "/eco/nope"):
            code, _body = ui.text(path)
            assert code == 404, path


def test_frontend_page_registration_matches_the_backend_page_paths(m6) -> None:
    """GWT-1：真服务取回的前端登记集 == 后端声明集（跨端漂移在装配态现形）。"""
    with ui_server(m6) as ui:
        code, source = ui.text("/js/pages.js")
        assert code == 200
        assert declared_pages_js_paths(source) == set(SHELL_PATHS)


def test_every_registered_page_has_a_live_status_endpoint(m6) -> None:
    """GWT-1：每条登记的 ``statusPath`` 都**不是 404** 且回六态之一——页面有登记、后端无端点即在此现形。"""
    with ui_server(m6) as ui:
        _code, source = ui.text("/js/pages.js")
        status_paths = declared_pages_js_status_paths(source)
        assert status_paths, "pages.js 未解析出任何 statusPath（解析口径失效？）"
        for path in sorted(status_paths):
            code, payload = ui.get(path)
            assert code == 200, f"{path} 未接线（{code}）"
            assert payload["status"] in SIX_STATES, (path, payload)


def test_shell_assets_are_servable_with_their_content_types(m6) -> None:
    """GWT-1：壳引用的资产整体可取（[`T-UI-008`] 的打包模式在**运行时可服务**这一侧的兑现）。"""
    with ui_server(m6) as ui:
        _code, shell = ui.text("/index.html")
        for asset in ("/css/tokens.css", "/css/app.css", "/js/main.js", "/js/pages.js"):
            code, head, raw = ui.request("GET", asset)
            assert code == 200, asset
            assert raw, asset
            assert head["content-type"].split(";")[0] in ("text/css", "text/javascript"), asset
        assert "tokens.css" in shell and "app.css" in shell and "main.js" in shell


# ───────────────────────── GWT-2 · 六态 ─────────────────────────


def test_four_states_come_from_real_endpoints(m6) -> None:
    """GWT-2：``ok`` / ``empty`` / ``unavailable`` / ``validation_failed`` 四态取自**真端点**。

    ``unavailable`` 取「节点不存在」（真读面如实降级），不是「面没接线」——真装配下十三个面
    都在（见 GWT-6），故这一态必须由**数据侧**的真实缺口产生。
    """
    with ui_server(m6) as ui:
        cases = {
            "/api/skills/status": "ok",
            "/api/workspace/pins": "empty",
            "/api/memory/node?id=mn_00000000000000000000": "unavailable",
            "/api/memory/node": "validation_failed",
        }
        for path, expected in cases.items():
            code, payload = ui.get(path)
            assert code == 200, path
            assert payload["status"] == expected, (path, payload)


def test_failed_and_dependency_failed_come_from_the_sample_surface(m6) -> None:
    """GWT-2：``failed`` / ``dependency_failed`` 经 dev 样本面呈现，并说明为何**不**取真端点。

    真装配下这两态只由**真故障**（面抛非 ``ValueError``）与**装配错误**
    （``WorkspaceFacade.refresh`` 的两条「装配有误」）产生——前者要制造真实故障、后者要错配
    来源，都是**把故障注入装配**而不是验证装配，故本关卡不硬造；样本面走的是与真实描述
    **同一条**出站校验路径（[`T-UI-018.3`] 的 dev 开关控制其可达性）。
    """
    with ui_server(m6, dev=True) as ui:
        for expected in ("failed", "dependency_failed"):
            code, payload = ui.get(f"/api/dev/sample?status={expected}")
            assert code == 200, expected
            assert payload["status"] == expected
            assert payload["reason"]
            assert payload["render"]["presentation"] == "error"
        # 非 dev 服务上该面不可达（发布包剔除，[`T-UI-008`]）
    with ui_server(m6) as ui:
        code, _payload = ui.get("/api/dev/sample?status=failed")
        assert code == 404


def test_every_envelope_carries_render_semantics_and_reason(m6) -> None:
    """GWT-2 横切：走查到的**每一条**信封都带 ``render`` 语义段，非 ok 必带 ``reason``。"""
    with ui_server(m6) as ui:
        walked = set(PROBE_ROUTES.values())
        walked.update({
            "/api/health": None,
            "/api/workspace/pins": None,
            "/api/memory/profile": None,
            "/api/memory/node?id=mn_00000000000000000000": None,
            "/api/memory/node": None,
            "/api/delivery/reports?limit=5": None,
        })
        for path in walked:
            code, payload = ui.get(path)
            assert code == 200, path
            assert payload["status"] in SIX_STATES, (path, payload)
            assert isinstance(payload.get("render"), dict) and payload["render"], path
            if payload["status"] != "ok":
                assert payload["reason"], f"{path} 非 ok 却未给原因"


# ───────────────────────── GWT-3 · 写面 ─────────────────────────


def test_write_route_outside_the_table_is_404_and_leaves_no_trace(m6) -> None:
    """GWT-3：写表**外**的 POST 回 404，且真存储的文件路径集**一字不变**（零副作用）。"""
    with ui_server(m6) as ui:
        before = store_paths(m6.root)
        for path in ("/api/workspace/pin2", "/api/nope", "/api/settings/wipe2"):
            code, _payload = ui.post(path, {"x": 1})
            assert code == 404, path
        assert store_paths(m6.root) == before


def test_every_declared_write_route_is_behind_the_token_guard(m6) -> None:
    """GWT-3：写表内**每一条**在无令牌时回 401，且**不发生派发**（存储路径集不变）。

    逐条走一遍是必要的：写表是**枚举式**白名单，逐条断言才能证明没有哪一条被漏在闸门之外
    （漏一条 ＝ 该动作可被无令牌触发）。
    """
    assert _POST_ROUTES, "写表为空？"
    with ui_server(m6) as ui:
        before = store_paths(m6.root)
        for path in sorted(_POST_ROUTES):
            code, payload = ui.post(path, {}, token=False)
            assert code == 401, f"{path} 未被令牌闸门拦住（{code}）"
            assert payload["error"], path
        assert store_paths(m6.root) == before


def test_a_declared_write_route_really_writes_and_reads_back(m6) -> None:
    """GWT-3：一条**真跨面**的写往返——读端点产出的描述经固定回环路由钉到工作区，再读回、取钉。

    这条同时证明：写端点真的落盘（不是空壳 200）、读端点真的看见它、且**钉住物落的是 L0
    ``config`` 分区**（存储路径集在钉 / 取钉之间确实变了又变回）。
    """
    with ui_server(m6) as ui:
        empty_before = store_paths(m6.root)

        code, catalog = ui.get("/api/skills/catalog")
        assert code == 200 and catalog["status"] == "ok"
        description = catalog["data"]
        assert description["component_type"] == "table"

        description_id = description["description_id"]
        code, pinned = ui.post("/api/workspace/pin", {"description": description})
        assert code == 200 and pinned["status"] == "ok", pinned

        code, pins = ui.get("/api/workspace/pins")
        assert code == 200 and pins["status"] == "ok"
        assert [item["description_id"] for item in pins["data"]] == [description_id]
        assert store_paths(m6.root) != empty_before, "钉住未落盘"

        code, unpinned = ui.post("/api/workspace/unpin", {"description_id": description_id})
        assert code == 200 and unpinned["status"] == "ok", unpinned

        code, pins = ui.get("/api/workspace/pins")
        assert pins["status"] == "empty" and pins["reason"]


# ───────────────────────── GWT-4 · 出站点 ─────────────────────────


def test_outbound_gate_blocks_neutrality_violations_without_echoing_them(m6) -> None:
    """GWT-4：装配出来的那个 ``UiApp`` 在出站点真的挂着中性化门。

    投一份**一份真描述 + 违规 ``generated`` 文案**（``title`` 恒为生成文案，[01 §12]）：
    必须阻断为 ``validation_failed``，且**不回显违规原文**（[`ui/neutrality_gate.py`] 明列：
    把违规原文抄进信封等于让它换个地方继续传播）。
    """
    with ui_server(m6) as ui:
        code, catalog = ui.get("/api/skills/catalog")
        assert code == 200 and catalog["status"] == "ok"

        payload = dict(catalog["data"])
        payload["title"] = VIOLATING_TITLE
        out = ui.app.api_description(UiDescription.model_validate(payload))

        assert out["status"] == "validation_failed", out
        assert out["reason"], out
        assert "中性校验" in out["reason"], out
        assert VIOLATING_TITLE not in json.dumps(out, ensure_ascii=False)


def test_walked_descriptions_are_registered_typed_and_fully_labelled(m6) -> None:
    """GWT-4：走查到的 ``ok`` 描述都是**已登记型**且 ``text_kinds`` 逐槽有标注（[01 §12]）。"""
    with ui_server(m6) as ui:
        seen = 0
        for path in DESCRIPTION_ROUTES:
            code, payload = ui.get(path)
            assert code == 200, path
            if payload["status"] != "ok":
                continue
            descriptions = payload["data"]
            if not isinstance(descriptions, list):
                descriptions = [descriptions]
            for description in descriptions:
                seen += 1
                assert description["component_type"] in IMPLEMENTED_COMPONENT_TYPES, path
                assert set(description["text_kinds"]) == set(description["slots"]), (
                    f"{path} 的 text_kinds 未逐槽标注"
                )
        assert seen, "走查面未产任何 ok 描述（构造失效？）"


# ───────────────────────── GWT-5 · 拒绝路径 ─────────────────────────


def test_foreign_origin_and_host_are_refused_with_a_readable_body(m6) -> None:
    """GWT-5：跨源 ``Origin`` 与外来 ``Host`` ⇒ 403，且**信封真的抵达**（不是网络错误）。"""
    with ui_server(m6) as ui:
        code, _head, raw = ui.request(
            "GET", "/api/health", headers={"Origin": "http://evil.example"}
        )
        assert code == 403
        assert "error" in json.loads(raw.decode("utf-8"))

        conn = ui.connection()
        try:
            conn.putrequest("GET", "/api/health", skip_host=True, skip_accept_encoding=True)
            conn.putheader("Host", "evil.example")
            conn.putheader("X-ST-Token", ui.token)
            conn.endheaders()
            response = conn.getresponse()
            body = response.read()
            assert response.status == 403
            assert "error" in json.loads(body.decode("utf-8"))
        finally:
            conn.close()


def test_missing_token_is_401(m6) -> None:
    """GWT-5：数据面无令牌 ⇒ 401（[D-060] ③F 的启动令牌握手）。"""
    with ui_server(m6) as ui:
        code, payload = ui.get("/api/health", token=False)
        assert code == 401
        assert payload["error"]


def test_unsupported_method_is_501_and_the_body_is_read(m6) -> None:
    """GWT-5：未支持的方法 ⇒ 501（``http.server`` 自己的出口），响应同样**真的抵达**。"""
    with ui_server(m6) as ui:
        code, _head, raw = ui.request("PUT", "/api/chat", body=b"{}")
        assert code == 501
        assert raw, "501 响应体为空（读净口径失效？）"


def test_a_refusal_does_not_poison_the_reused_connection(m6) -> None:
    """GWT-5（``T-UI-022`` 在装配态）：被拒绝的大体请求之后，**同一条连接**仍能正常往返。

    判据是**复用**而不是单发：拒绝路径若没把声明的请求体读满，关闭连接时对端拿到复位——
    单发看不出（复位被读成网络错误），复用下一次请求才现形。
    """
    with ui_server(m6) as ui:
        conn = ui.connection()
        try:
            code, _head, _raw = ui.request(
                "POST", "/api/not-in-the-table", body=b"x" * (2 * 1024 * 1024), connection=conn
            )
            assert code == 404
            code, payload = ui.get("/api/health", connection=conn)
            assert code == 200
            assert payload["status"] == "ok"
        finally:
            conn.close()


# ───────────────────────── GWT-6 · 生产入口 ─────────────────────────


def test_the_entry_injects_exactly_the_serve_port_map(m6, monkeypatch) -> None:
    """GWT-6：``ui_ports`` 与 ``serve`` 的端口形参**逐名相等**，且入口透传的正是**这张表**。

    两个方向的漂移都在此现形：加了面却没进 ``ui_ports``（``serve`` 有、表里没有 ⇒ 该面恒
    ``unavailable``），或表里有 ``serve`` 不认识的键（**启动即失败**）。后半条用替身钉住
    ——入口若改回内联一张自己的表，这里立刻红（门面对象每次访问新建，故只能比**键**与
    传入的根，不能比身份）。
    """
    assert set(ui_ports(m6.m5)) == set(serve_port_names())

    captured: dict[str, Any] = {}

    def _capture(**kwargs: Any) -> Any:
        captured.update(kwargs)

        class _Running:
            host, port, token = "127.0.0.1", 1, "t"

            def shutdown(self) -> None:
                pass

        return _Running()

    monkeypatch.setattr("st_agent.__main__.ui_ports", lambda runtime: {"sentinel": runtime})
    exit_code = run_backend(
        root=m6.root,
        port=0,
        json_handshake=True,
        passphrase="ignored-by-stub",
        out=io.StringIO(),
        build_runtime=lambda *a, **k: m6.m5,
        serve_ui=_capture,
        control_stdin=True,
        ambient_interval=0,
        read_passphrase=lambda: None,
    )
    assert exit_code == 0
    assert captured.pop("sentinel") is m6.m5, "入口未透传 ui_ports 的那张表"
    assert set(captured) == {"host", "port", "dev", "shell", "shell_control"}
    assert captured["shell"] is True, "控制通道开启时未向表现层声明壳会话"


def test_the_production_root_carries_every_port_and_every_probe_is_ok(m6) -> None:
    """GWT-6：真根上十三个端口一个不少，且各面探针全 ``ok``。

    「面在位」与「面取不到数」是两回事（[01 §5] 六态不可混用）：探针 ``ok`` 只声明**接线**
    完成，各面的空 / 不可用由各面自己的信封说。
    """
    ports = ui_ports(m6.m5)
    assert all(value is not None for value in ports.values()), sorted(
        name for name, value in ports.items() if value is None
    )
    with ui_server(m6) as ui:
        for face, path in PROBE_ROUTES.items():
            code, payload = ui.get(path)
            assert code == 200, path
            assert payload["status"] == "ok", (face, payload)
            assert payload["data"]["available"] is True, (face, payload)
