"""M6 各面端口与探针（[`T-UI-011.1`]）。

本组锁四件事：

1. **fail-closed**——某面端口未注入 ⇒ 该面 `/api/<面>/status` 回 `unavailable` + **点名**
   装配归属（不 500、不装空表冒充「面在但无数据」）。
2. **端口名 ＝ 面名**——注入后探针回 `ok` 且 `data == {"face": <面名>, "available": True}`，
   故 `api_face_status(face)` 的 `getattr` 与端口字段逐名对应（端口改名而未同步面名会在此红）。
3. **新增路径只有只读探针**——七条全在 `_GET_ROUTES`、`_POST_ROUTES` 里一条都没有；
   写面白名单（15 条）本叶**逐条不变**（[01 §12] 动作不进描述：本叶不开任何写面）。
4. **守卫不放宽**——探针属 `/api/*` 数据面，恒要令牌。

真实组合根上的「六面在、`workspace` 刻意未接线」由 `tests/integration/test_ui_face_wiring.py` 兜。
"""

from __future__ import annotations

import pytest

from st_agent.ui.server import _GET_ROUTES, _POST_ROUTES, serve

_FACES = ("workspace", "skills", "mcp", "graph", "deliberation", "delivery", "settings")
"""七个新面（[`T-UI-011.1`]）；端口名 ＝ 面名 ＝ 探针路径中段。"""

_STATUS_PATHS = tuple(f"/api/{face}/status" for face in _FACES)

_WIRED_FACES = tuple(face for face in _FACES if face != "workspace")
"""组合根**会**造门面的六个面——`workspace` 刻意不装配（钉住物归 [`T-UI-013`]）。"""

_EXPECTED_POST_ROUTES = frozenset({
    "/api/chat",
    "/api/reflection/feedback",
    "/api/reflection/proposals/decide",
    "/api/reflection/proposals/studio",
    "/api/reflection/proposals/studio/decide",
    "/api/studio/edit",
    "/api/evolution/changes/rollback",
    "/api/evolution/authorization",
    "/api/evolution/factory-reset",
    "/api/eco/export",
    "/api/eco/import/review",
    "/api/eco/import/permissions",
    "/api/eco/import/decide",
    "/api/eco/import/install",
    "/api/eco/violations/disable",
    # 记忆区图谱面的写面（[`T-UI-014.3`]）：导出确认门 + Onboarding 提交。
    "/api/memory/export",
    "/api/memory/onboarding",
})
"""写面白名单的**副本**——枚举副本 + 测试钉住（既有做法）。

`T-UI-011.1` 落下 15 条；[`T-UI-014.3`] 新增 2 条（记忆导出确认 / Onboarding 提交）。
后续各面功能叶在自己的交付里新增写端点时，同批把此处补上。
"""


@pytest.mark.parametrize("path", _STATUS_PATHS)
def test_probe_is_unavailable_and_names_the_face_when_not_wired(
    ui_server, http_get, auth, path: str
) -> None:
    """GWT-1：端口未注入 ⇒ `unavailable` + 点名（含最后更新时间与 `delayed` 呈现）。"""
    response = http_get(ui_server, path, headers=auth(ui_server))
    assert response.status == 200
    payload = response.json()
    assert payload["status"] == "unavailable"
    assert payload["reason"], "不可用必须说明原因"
    assert "未接入" in payload["reason"] and "面" in payload["reason"]
    assert payload["render"]["presentation"] == "delayed"
    assert payload["last_updated_at"], "unavailable 须给「最后更新时间 T」"


@pytest.mark.parametrize("path", _STATUS_PATHS)
def test_probe_needs_the_token(ui_server, http_get, path: str) -> None:
    """守卫不放宽：新探针属 `/api/*` 数据面，不带令牌即 401。"""
    assert http_get(ui_server, path).status == 401


@pytest.mark.parametrize("face", _WIRED_FACES)
def test_probe_is_ok_and_names_the_face_once_wired(http_get, auth, face: str) -> None:
    """GWT-2：注入端口 ⇒ `ok`，且 `data.face` 就是**面名**（端口名与面名逐名对应）。"""
    with serve(dev=False, **{face: object()}) as running:
        payload = http_get(running, f"/api/{face}/status", headers=auth(running)).json()
    assert payload["status"] == "ok"
    assert payload["data"] == {"face": face, "available": True}


def test_each_face_gets_its_own_probe(http_get, auth) -> None:
    """七条探针**各自可辨**——注入一面不会顺带把别的面也报成 `ok`。"""
    with serve(dev=False, skills=object()) as running:
        statuses = {
            face: http_get(running, f"/api/{face}/status", headers=auth(running)).json()["status"]
            for face in _FACES
        }
    assert statuses["skills"] == "ok"
    assert all(status == "unavailable" for face, status in statuses.items() if face != "skills")


@pytest.mark.parametrize("path", _STATUS_PATHS)
def test_new_paths_are_get_only(ui_server, http_post, auth, path: str) -> None:
    """GWT-4：七条新路径**只读**——`POST` 到它们仍是 404（不在写面白名单内）。"""
    assert path in _GET_ROUTES
    assert path not in _POST_ROUTES
    assert http_post(ui_server, path, {}, headers=auth(ui_server)).status == 404


def test_this_leaf_added_no_write_face() -> None:
    """GWT-4：写面白名单**逐条不变**——本叶只开只读探针（[01 §12] 动作不进描述）。"""
    assert set(_POST_ROUTES) == _EXPECTED_POST_ROUTES, (
        "写面白名单与本叶落定时不一致：本叶只应新增只读探针；"
        "若确实新增了写端点，请同批更新本用例的白名单副本"
    )


def test_existing_four_ports_keep_their_probes(http_get, auth) -> None:
    """既有四端口语义不动——三条老探针照旧（回归）。"""
    with serve(dev=False, reflection=object(), eco=object()) as running:
        for path, face in (
            ("/api/reflection/status", "reflection"),
            ("/api/evolution/status", "reflection"),
            ("/api/eco/status", "eco"),
        ):
            payload = http_get(running, path, headers=auth(running)).json()
            assert payload["status"] == "ok"
            assert payload["data"] == {"face": face, "available": True}
