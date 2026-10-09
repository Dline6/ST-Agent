"""Studio 画布端点（[`T-UI-005.1`]）：信封形态、`studio_canvas` 描述、fail-closed、输入类失败分流。

替身只回**已定形态**的数据：本组钉的是 ui 侧的信封与描述形态（两槽 + 必填、六态、路由），
真链路的装配由 `tests/integration/test_studio_canvas.py` 覆盖。
"""

from __future__ import annotations

import pytest

from st_agent.ui.server import serve

CANVAS = {
    "session": {
        "proposal_id": "prp_1", "base": "wf_x", "flow_id": "wf_x_v0.0",
        "node_count": 1, "status": "editing",
    },
    "nodes": [{"node_id": "n1", "skill_id": "sk_stock_watch_v1.0", "params": {}}],
    "edges": [],
    "groups": [],
    "schedule": {"mode": "manual"},
    "violations": [],
    "validation_ok": True,
    "skill_options": [
        {"name": "sk_stock_watch", "skill_id": "sk_stock_watch_v1.0", "description": "多标的盯盘"}
    ],
}

_EDIT_OPS = ("add_node", "remove_node", "connect", "disconnect", "group", "ungroup")


class FakeReflection:
    """Studio 画布适配面替身（只回已定形态的数据，并记下收到的调用）。"""

    def __init__(
        self,
        *,
        sessions=None,
        canvas=None,
        skills=None,
        edit=None,
        available: bool = True,
        reason: str = "未接入 Studio 草稿接收面（08 §4）",
    ) -> None:
        self._available = available
        self._reason = reason
        self._sessions = sessions if sessions is not None else {
            "available": True, "sessions": [CANVAS["session"]],
        }
        self._canvas = canvas if canvas is not None else {"available": True, "canvas": CANVAS}
        self._skills = skills if skills is not None else {
            "available": True, "skills": CANVAS["skill_options"],
        }
        self._edit = edit
        self.calls: list[tuple] = []

    def _guard(self):
        if not self._available:
            return {"available": False, "reason": self._reason}
        return None

    def studio_sessions(self):
        self.calls.append(("studio_sessions",))
        return self._guard() or self._sessions

    def studio_canvas(self, proposal_id):
        self.calls.append(("studio_canvas", proposal_id))
        if not proposal_id:
            raise ValueError("提案标识（proposal_id）必填——无法寻址的提案一律拒收")
        return self._guard() or self._canvas

    def studio_skills(self):
        self.calls.append(("studio_skills",))
        return self._guard() or self._skills

    def studio_edit(self, *, proposal_id, op, args=None):
        self.calls.append(("studio_edit", proposal_id, op))
        if op not in _EDIT_OPS:
            raise ValueError(f"画布编辑操作须为 {'/'.join(_EDIT_OPS)}，收到 {op!r}（08 §4）")
        if not proposal_id:
            raise ValueError("提案标识（proposal_id）必填——无法寻址的提案一律拒收")
        guard = self._guard()
        if guard is not None:
            return guard
        if self._edit is not None:
            return self._edit
        return {
            "available": True,
            "canvas": {**CANVAS, "edit": {"action": op, "applied": True,
                                          "message": "已生效", "blocked_by": []}},
        }


@pytest.fixture
def studio_server():
    """起一个注入了 Studio 画布替身的真服务。"""

    def _start(facade):
        return serve(dev=False, reflection=facade)

    return _start


# ── 画布描述形态 ───────────────────────────────────────────────────────────────

def test_canvas_endpoint_returns_a_studio_canvas_description(studio_server, http_get, auth):
    facade = FakeReflection()
    with studio_server(facade) as running:
        payload = http_get(
            running, "/api/studio/canvas?proposal_id=prp_1", headers=auth(running)
        ).json()
    assert payload["status"] == "ok"
    assert payload["data"]["component_type"] == "studio_canvas"
    slots = payload["data"]["slots"]
    assert set(slots) == {"canvas", "labels"}          # 两必填槽
    assert slots["canvas"]["session"]["proposal_id"] == "prp_1"
    assert slots["canvas"]["skill_options"][0]["name"] == "sk_stock_watch"
    assert slots["labels"]["accept"] == "接受" and slots["labels"]["add_node"] == "添加节点"
    assert facade.calls == [("studio_canvas", "prp_1")]


def test_edit_endpoint_returns_the_rerendered_canvas_with_edit_summary(
    studio_server, http_post, auth
):
    facade = FakeReflection()
    with studio_server(facade) as running:
        payload = http_post(
            running, "/api/studio/edit",
            {"proposal_id": "prp_1", "op": "add_node",
             "args": {"node_id": "n2", "skill_id": "sk_stock_watch_v1.0"}},
            headers=auth(running),
        ).json()
    assert payload["status"] == "ok"
    assert payload["data"]["component_type"] == "studio_canvas"
    assert payload["data"]["slots"]["canvas"]["edit"]["applied"] is True
    assert facade.calls == [("studio_edit", "prp_1", "add_node")]


def test_sessions_and_skills_endpoints_return_ok_data(studio_server, http_get, auth):
    facade = FakeReflection()
    with studio_server(facade) as running:
        sessions = http_get(running, "/api/studio/sessions", headers=auth(running)).json()
        skills = http_get(running, "/api/studio/skills", headers=auth(running)).json()
    assert sessions["status"] == "ok" and sessions["data"]["available"] is True
    assert sessions["data"]["sessions"][0]["proposal_id"] == "prp_1"
    assert skills["status"] == "ok" and skills["data"]["skills"][0]["name"] == "sk_stock_watch"


# ── 输入类失败：就地 validation_failed（不断连） ─────────────────────────────────

def test_canvas_without_proposal_id_is_validation_failed(studio_server, http_get, auth):
    with studio_server(FakeReflection()) as running:
        payload = http_get(running, "/api/studio/canvas", headers=auth(running)).json()
    assert payload["status"] == "validation_failed"
    assert "proposal_id" in payload["reason"]


def test_edit_with_unknown_op_is_validation_failed(studio_server, http_post, auth):
    with studio_server(FakeReflection()) as running:
        payload = http_post(
            running, "/api/studio/edit", {"proposal_id": "prp_1", "op": "set_params"},
            headers=auth(running),
        ).json()
    assert payload["status"] == "validation_failed"
    assert "画布编辑操作" in payload["reason"]


def test_edit_without_the_required_body_key_is_validation_failed(studio_server, http_post, auth):
    with studio_server(FakeReflection()) as running:
        payload = http_post(
            running, "/api/studio/edit", {"proposal_id": "prp_1"}, headers=auth(running)
        ).json()
    assert payload["status"] == "validation_failed"


# ── 面未注入 / 子面未接线：unavailable + 点名 ────────────────────────────────────

@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/api/studio/sessions", None),
        ("get", "/api/studio/canvas?proposal_id=prp_1", None),
        ("get", "/api/studio/skills", None),
        ("post", "/api/studio/edit", {"proposal_id": "prp_1", "op": "add_node", "args": {}}),
    ],
)
def test_endpoints_are_unavailable_without_the_face(ui_server, http_get, http_post, auth,
                                                    method, path, body):
    """`reflection` 面未注入 ⇒ `unavailable` + 点名（不 500、不伪造画布）。"""
    call = (
        (lambda: http_get(ui_server, path, headers=auth(ui_server)))
        if method == "get"
        else (lambda: http_post(ui_server, path, body, headers=auth(ui_server)))
    )
    payload = call().json()
    assert payload["status"] == "unavailable"
    assert "未接入" in payload["reason"]


def test_subface_unavailable_is_not_faked(studio_server, http_get, auth):
    """`available: false` ⇒ `unavailable` + 点名（**不**包成 `ok`）。"""
    facade = FakeReflection(available=False, reason="未接入 Studio 草稿接收面（08 §4）")
    with studio_server(facade) as running:
        payload = http_get(
            running, "/api/studio/canvas?proposal_id=prp_1", headers=auth(running)
        ).json()
    assert payload["status"] == "unavailable"
    assert "Studio" in payload["reason"]


def test_studio_endpoints_need_the_token(ui_server, http_get, http_post):
    """数据面恒要令牌——新增端点**不得**放宽守卫。"""
    assert http_get(ui_server, "/api/studio/sessions").status == 401
    assert http_post(ui_server, "/api/studio/edit", {"proposal_id": "p", "op": "add_node"}).status == 401
