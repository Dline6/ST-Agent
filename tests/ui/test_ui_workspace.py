"""工作区（`/workspace` —— 钉住的持久组件）的回环面（[`T-UI-013`]）。

本组钉五件事（L3 侧的落盘与重解析由 `tests/l3/test_workspace.py` 钉，真组合根上的装配
由 `tests/integration/test_ui_workspace_wiring.py` 钉）：

1. **读面出的是描述序列**——[01 §5] 允许工作区的 `data` 为描述序列，故本层不另造容器型、
   也不直灌 JSON，逐条仍走**同一条**渲染路径（注册表 ＋ 两道闸）；
2. **空与不可用互不冒充**（[01 §5]）：面未接线 ⇒ `unavailable` ＋ 点名；钉过东西但为空 ⇒
   `empty` ＋ 原因；
3. **三道闸在写面上生效**——必填槽（注册表镜像）与渲染前中性门；序列里**任何一条**不过闸
   即整面 `validation_failed`（不静默漏掉那一条）；
4. **路由形态**——读面在 `_GET_ROUTES`，三条写面在 `_POST_ROUTES`；
5. **页面登记**——`/workspace` 挂上 `render`（不再是六态探针页）且探针路径真实存在。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.ui_description import checked_description, new_description_id
from st_agent.ui.app import PAGE_PATHS
from st_agent.ui.server import _GET_ROUTES, _POST_ROUTES, serve

_UI = Path(__file__).resolve().parents[2] / "src" / "st_agent" / "ui"


def _context_card():
    return checked_description(
        description_id=new_description_id(),
        component_type="context_card",
        title="当前画像摘要",
        slots={"sections": [{"key": "risk", "value": "稳健"}], "labels": {"risk": "风险偏好"}},
        text_kinds={"sections": "data", "labels": "generated"},
        as_of=None,
    )


def _table(*, rows=None):
    return checked_description(
        description_id=new_description_id(),
        component_type="table",
        title="持仓",
        slots={"columns": [{"key": "code", "label": "代码"}], "rows": rows or [{"code": "sh.600000"}]},
        text_kinds={"columns": "generated", "rows": "data"},
        as_of=None,
    )


class _FakeWorkspace:
    """工作区面替身（鸭子端口）：**只**回已定形态的四个口，并记下收到的请求体。"""

    def __init__(self, pins: ResultEnvelope | None = None, *, on_write=None) -> None:
        self._pins = pins if pins is not None else ResultEnvelope.empty("工作区还没有钉住的组件。")
        self._on_write = on_write or (lambda method, body: ResultEnvelope.ok(_context_card()))
        self.received: list[tuple[str, dict]] = []

    def pins(self) -> ResultEnvelope:
        return self._pins

    def pin(self, body: dict) -> ResultEnvelope:
        self.received.append(("pin", body))
        return self._on_write("pin", body)

    def unpin(self, body: dict) -> ResultEnvelope:
        self.received.append(("unpin", body))
        return self._on_write("unpin", body)

    def refresh(self, body: dict) -> ResultEnvelope:
        self.received.append(("refresh", body))
        return self._on_write("refresh", body)


def _get(http_get, auth, path, **faces):
    with serve(dev=False, **faces) as running:
        return http_get(running, path, headers=auth(running)).json()


def _post(http_post, auth, path, body, **faces):
    with serve(dev=False, **faces) as running:
        return http_post(running, path, body=body, headers=auth(running)).json()


# ── 读面 ────────────────────────────────────────────────────────────────────


def test_pins_endpoint_wraps_a_description_sequence(http_get, auth) -> None:
    face = _FakeWorkspace(ResultEnvelope.ok([_context_card(), _table()]))
    payload = _get(http_get, auth, "/api/workspace/pins", workspace=face)
    assert payload["status"] == "ok"
    assert [item["component_type"] for item in payload["data"]] == ["context_card", "table"]


def test_pins_keeps_the_description_id_as_the_addressing_unit(http_get, auth) -> None:
    card = _context_card()
    face = _FakeWorkspace(ResultEnvelope.ok([card]))
    payload = _get(http_get, auth, "/api/workspace/pins", workspace=face)
    assert payload["data"][0]["description_id"] == card.description_id


def test_empty_workspace_is_empty_with_a_reason(http_get, auth) -> None:
    face = _FakeWorkspace(ResultEnvelope.empty("工作区还没有钉住的组件。"))
    payload = _get(http_get, auth, "/api/workspace/pins", workspace=face)
    assert payload["status"] == "empty"
    assert payload["reason"]


def test_face_absent_is_unavailable_and_names_the_probe(http_get, auth) -> None:
    payload = _get(http_get, auth, "/api/workspace/pins")
    assert payload["status"] == "unavailable"
    assert "未接入" in payload["reason"]


# ── 写面 ────────────────────────────────────────────────────────────────────


def test_pin_forwards_the_description(http_post, auth) -> None:
    card = _context_card()
    face = _FakeWorkspace()
    payload = _post(
        http_post, auth, "/api/workspace/pin",
        {"description": card.model_dump(mode="json")}, workspace=face,
    )
    assert payload["status"] == "ok"
    assert face.received[0][0] == "pin"
    assert face.received[0][1]["description"]["component_type"] == "context_card"


def test_refresh_passes_only_the_description_id(http_post, auth) -> None:
    """描述**不接受** URL / 方法（[01 §12]）：去哪个来源由服务端按记录下来的面键决定。"""
    face = _FakeWorkspace()
    payload = _post(
        http_post, auth, "/api/workspace/refresh",
        {"description_id": "desc_" + "1" * 20}, workspace=face,
    )
    assert payload["status"] == "ok"
    assert face.received[0] == ("refresh", {"description_id": "desc_" + "1" * 20})


def test_unpin_returns_the_id(http_post, auth) -> None:
    face = _FakeWorkspace(on_write=lambda method, body: ResultEnvelope.ok({"description_id": "x"}))
    payload = _post(
        http_post, auth, "/api/workspace/unpin", {"description_id": "x"}, workspace=face
    )
    assert payload["status"] == "ok"
    assert payload["data"] == {"description_id": "x"}


def test_write_face_absent_is_unavailable(http_post, auth) -> None:
    payload = _post(http_post, auth, "/api/workspace/pin", {"description": {}})
    assert payload["status"] == "unavailable"
    assert "未接入" in payload["reason"]


# ── 两道闸 ──────────────────────────────────────────────────────────────────


def test_a_description_missing_required_slots_is_rejected(http_get, auth) -> None:
    """`context_card` 的必填槽是 `sections` + `labels`（[01 §12]）——缺了不许出站。"""
    broken = _context_card().model_copy(update={"slots": {"sections": []}})
    face = _FakeWorkspace(ResultEnvelope.ok([broken]))
    payload = _get(http_get, auth, "/api/workspace/pins", workspace=face)
    assert payload["status"] == "validation_failed"
    assert "labels" in payload["reason"]


def test_a_bad_item_fails_the_whole_face_not_just_itself(http_get, auth) -> None:
    """序列里一条不过闸即整面失败——静默漏掉它会让工作区看起来「本来就少一块」。"""
    broken = _table().model_copy(update={"slots": {"columns": [{"key": "code", "label": "代码"}]}})
    face = _FakeWorkspace(ResultEnvelope.ok([_context_card(), broken]))
    payload = _get(http_get, auth, "/api/workspace/pins", workspace=face)
    assert payload["status"] == "validation_failed"


def test_generated_wording_that_hits_the_neutrality_gate_is_rejected(http_get, auth) -> None:
    """`generated` 槽过 [01 §6] 执行点 2；命中即阻断（不出可渲染的描述）。"""
    card = _context_card().model_copy(
        update={
            "slots": {
                "sections": [{"key": "risk", "value": "稳健"}],
                "labels": {"risk": "我认为你的风险偏好是稳健"},
            }
        }
    )
    face = _FakeWorkspace(ResultEnvelope.ok([card]))
    payload = _get(http_get, auth, "/api/workspace/pins", workspace=face)
    assert payload["status"] == "validation_failed"
    assert payload["reason"]


# ── 路由与页面登记 ──────────────────────────────────────────────────────────


def test_pins_is_a_read_route_and_the_writes_are_post_routes() -> None:
    assert "/api/workspace/pins" in _GET_ROUTES
    assert "/api/workspace/pins" not in _POST_ROUTES
    for path in ("/api/workspace/pin", "/api/workspace/unpin", "/api/workspace/refresh"):
        assert path in _POST_ROUTES


def test_workspace_is_a_registered_page_with_a_render_and_a_probe() -> None:
    assert "/workspace" in PAGE_PATHS
    assert "/api/workspace/status" in _GET_ROUTES
    pages = (_UI / "web" / "js" / "pages.js").read_text(encoding="utf-8")
    block = pages.split("path: '/workspace'", 1)[1].split("},", 1)[0]
    assert "render: renderWorkspacePage" in block


def test_page_module_imports_only_the_shared_render_path() -> None:
    """页面**只**经 `render.js` 的注册表渲染——不另造第二条渲染路径（[05 §6]）。"""
    text = (_UI / "web" / "js" / "pages" / "workspace.js").read_text(encoding="utf-8")
    assert "renderDescription" in text
    assert "renderEnvelope" in text
    assert "st_agent" not in text


@pytest.mark.parametrize("path", ["/api/workspace/pins", "/api/workspace/pin"])
def test_workspace_endpoints_need_the_token(path: str, http_get, http_post) -> None:
    """守卫不放宽：新端点属 `/api/*` 数据面，恒要令牌。"""
    with serve(dev=False, workspace=_FakeWorkspace()) as running:
        if path.endswith("pins"):
            assert http_get(running, path).status == 401
        else:
            assert http_post(running, path, body={}).status == 401
