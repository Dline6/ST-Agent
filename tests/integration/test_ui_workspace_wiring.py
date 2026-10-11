"""工作区在**真 M5 组合根**上的端到端（[`T-UI-013`]）。

与 `tests/ui/test_ui_workspace.py`（替身钉信封 / 六态 / 两道闸）分工：本组用真
[`build_m5_runtime`](../../src/st_agent/app.py) 造出的 [`WorkspaceFacade`] 走一遍真链路——

1. **真描述能钉住**——把 `/api/chat/context` 出的**真** `context_card` 描述原样交给
   `/api/workspace/pin`，读面回的就是它（`description_id` 作寻址单位，[01 §1]）；
2. **重解析走真只读面**——`refresh` 经组合根注入的 `DialogFacade.context_card()` 重取，
   回 `ok` 且**沿用原 `description_id`**（否则「刷新」就成了「又钉一件」）；
3. **没有只读来源的型如实降级**——刷新回 `unavailable` ＋ 点名，不是静默返回旧数据；
4. **真落盘**——钉住物进 L0 `config` 分区的 `workspace/pins/<description_id>.json`
   （走出对话流、跨会话留存的物质基础）。

离线可跑：真 Store / 真 L1 技能目录 / 真 L2–L6，只有 LLM 出网那一跳是脚本替身（`rig_m5`）。
"""

from __future__ import annotations

import http.client
import json

import pytest
from rig import ROOT_NAME
from rig_m5 import seeded_m5

from st_agent.contracts.ui_description import checked_description, new_description_id
from st_agent.ui.security import TOKEN_HEADER
from st_agent.ui.server import serve

RULES: list[tuple[str, object]] = []


def _request(running, method: str, path: str, body: dict | None = None) -> dict:
    conn = http.client.HTTPConnection(running.host, running.port, timeout=60)
    try:
        headers = {TOKEN_HEADER: running.token}
        payload = None
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=payload, headers=headers)
        return json.loads(conn.getresponse().read().decode("utf-8"))
    finally:
        conn.close()


def _get(running, path: str) -> dict:
    return _request(running, "GET", path)


def _post(running, path: str, body: dict) -> dict:
    return _request(running, "POST", path, body)


@pytest.fixture()
def m5(tmp_path):
    return seeded_m5(tmp_path / ROOT_NAME, rules=RULES)


def _serving(m5, **extra):
    """起回环面：工作区面 ＋ 对话面（重解析来源要经后者取数）。"""
    return serve(dev=False, workspace=m5.m5.workspace, chat=m5.m5.chat, **extra)


def test_a_real_description_can_be_pinned_listed_refreshed_and_unpinned(m5) -> None:
    """GWT-1 / GWT-3 / GWT-5：钉 → 呈现 → 刷新 → 取消钉住，全程真链路。"""
    with _serving(m5) as running:
        card = _get(running, "/api/chat/context")
        assert card["status"] == "ok", card.get("reason")
        description = card["data"]
        description_id = description["description_id"]

        pinned = _post(running, "/api/workspace/pin", {"description": description})
        assert pinned["status"] == "ok", pinned.get("reason")
        assert pinned["data"]["description_id"] == description_id

        listed = _get(running, "/api/workspace/pins")
        assert listed["status"] == "ok"
        assert [item["description_id"] for item in listed["data"]] == [description_id]
        assert listed["data"][0]["component_type"] == "context_card"

        refreshed = _post(running, "/api/workspace/refresh", {"description_id": description_id})
        assert refreshed["status"] == "ok", refreshed.get("reason")
        # 寻址单位不变：刷新换的是绑定与 as_of，不是又钉了一件
        assert refreshed["data"]["description_id"] == description_id
        assert [item["description_id"] for item in _get(running, "/api/workspace/pins")["data"]] == [
            description_id
        ]

        assert _post(running, "/api/workspace/unpin", {"description_id": description_id})[
            "status"
        ] == "ok"
        assert _get(running, "/api/workspace/pins")["status"] == "empty"


def test_the_pin_lands_on_the_real_disk_in_the_config_partition(m5) -> None:
    """「持久」的物质基础：一条钉住物 ＝ `config` 分区下一个以 `description_id` 命名的文件。"""
    with _serving(m5) as running:
        description = _get(running, "/api/chat/context")["data"]
        _post(running, "/api/workspace/pin", {"description": description})
        name = f"workspace/pins/{description['description_id']}.json"
        assert name in m5.m5.store.list_files("config")


def test_a_type_without_a_read_face_refreshes_to_unavailable(m5) -> None:
    """没有只读取数口的型：钉得住，但刷新**如实**说「没有可重取的来源」（[01 §5]）。"""
    table = checked_description(
        description_id=new_description_id(),
        component_type="table",
        title="持仓",
        slots={"columns": [{"key": "code", "label": "代码"}], "rows": [{"code": "sh.600000"}]},
        text_kinds={"columns": "generated", "rows": "data"},
        as_of=None,
    )
    with _serving(m5) as running:
        pinned = _post(running, "/api/workspace/pin", {"description": table.model_dump(mode="json")})
        assert pinned["status"] == "ok"
        refreshed = _post(
            running, "/api/workspace/refresh", {"description_id": pinned["data"]["description_id"]}
        )
        assert refreshed["status"] == "unavailable"
        assert refreshed["reason"]
        # 旧绑定**原样**留在工作区——降级不等于丢数据
        assert _get(running, "/api/workspace/pins")["status"] == "ok"


def test_an_empty_workspace_reads_as_empty_not_unavailable(m5) -> None:
    """「没钉过东西」与「面没接线」是两回事（[01 §5]），在真根上同样分得开。"""
    with _serving(m5) as running:
        payload = _get(running, "/api/workspace/pins")
        assert payload["status"] == "empty"
        assert payload["reason"]
        assert _get(running, "/api/workspace/status")["status"] == "ok"
