"""记忆区**持仓 / 关注行情读面**（[`T-UI-009.2`]）。

本组钉四件事：

1. **形状**——出的是 `table` 描述，列带 `kind`（收盘 / 换手率是 `number`、涨跌幅是 `direction`），
   行是键控对象且**只带数值**（不预格式化、不判方向——三路编码归渲染件，[D-105] ②）；
2. **两段各自一条端点**——持仓与关注池互不牵连（段级空态不串味）；
3. **三种缺口互不冒充**（[01 §5]）：面未接线 ⇒ `unavailable` + 点名；记忆里这段为空 ⇒
   `empty` + 原因；行情源没注入 / 行情库非 ok ⇒ `unavailable` + 原因；
4. **页面登记**——`/memory` 在静态壳档可达，且前端登记表里有对应渲染件。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from st_agent.ui.app import PAGE_PATHS, build_ui
from st_agent.ui.server import serve

_UI = Path(__file__).resolve().parents[2] / "src" / "st_agent" / "ui"

_ROWS = (
    {
        "code": "sh.600000", "code_name": "浦发银行",
        "close": 0.50, "pct_chg": 7.5, "turn": 12.0,
    },
    {
        # 该标的在本地缓存里没有日线：行情列缺席（**不**冒充 0、**不**读作「平」）
        "code": "sz.000001", "code_name": None,
        "close": None, "pct_chg": None, "turn": None,
    },
)


class _FakeMemory:
    """记忆面替身（鸭子端口）：**只**回已定形态的 `positions(scope)`。"""

    def __init__(self, **sections: dict) -> None:
        self._sections = sections

    def positions(self, scope: str) -> dict:
        return self._sections.get(scope, {"available": True, "reason": "", "rows": []})


@pytest.fixture
def memory_server():
    def _start(facade):
        return serve(dev=False, memory=facade)

    return _start


def _payload(memory_server, http_get, auth, facade, path):
    with memory_server(facade) as running:
        return http_get(running, path, headers=auth(running)).json()


def test_holdings_endpoint_renders_a_quote_table(memory_server, http_get, auth) -> None:
    """GWT-1：持仓段出 `table`，列级角色齐备，行**只带数值**。"""
    payload = _payload(
        memory_server, http_get, auth,
        _FakeMemory(holdings={"available": True, "reason": "", "rows": list(_ROWS)}),
        "/api/memory/holdings",
    )
    assert payload["status"] == "ok"
    description = payload["data"]
    assert description["component_type"] == "table"

    columns = {column["key"]: column for column in description["slots"]["columns"]}
    assert columns["pct_chg"]["kind"] == "direction", "涨跌幅须是涨跌列（三路编码由渲染件出）"
    assert columns["close"]["kind"] == "number" and columns["turn"]["kind"] == "number"
    assert columns["code"]["kind"] == "text"

    rows = description["slots"]["rows"]
    assert rows == list(_ROWS)
    # 数值**原样**是数，不是成品文案——格式化与方向都在渲染面（[D-105] ②）
    assert rows[0]["pct_chg"] == 7.5 and rows[1]["pct_chg"] is None
    assert not any(isinstance(row["pct_chg"], str) for row in rows)


def test_watchlist_is_a_second_independent_endpoint(memory_server, http_get, auth) -> None:
    """GWT-1/GWT-2：关注池另一条端点——一段出读面不牵连另一段。"""
    facade = _FakeMemory(
        holdings={"available": True, "reason": "", "rows": list(_ROWS)},
        watchlist={"available": True, "reason": "", "rows": []},
    )
    holdings = _payload(memory_server, http_get, auth, facade, "/api/memory/holdings")
    watchlist = _payload(memory_server, http_get, auth, facade, "/api/memory/watchlist")
    assert holdings["status"] == "ok"
    assert watchlist["status"] == "empty" and "关注池" in watchlist["reason"]


def test_empty_section_says_why(memory_server, http_get, auth) -> None:
    """GWT-3：记忆里这段为空 ⇒ `empty` + 原因（空是邀请，不是留白）。"""
    payload = _payload(
        memory_server, http_get, auth, _FakeMemory(), "/api/memory/holdings"
    )
    assert payload["status"] == "empty"
    assert payload["reason"] == "记忆中没有持仓信息"


def test_missing_quote_source_is_unavailable_not_empty(memory_server, http_get, auth) -> None:
    """GWT-3：行情源没注入 ⇒ `unavailable` + 原因——**不**装成「你没有持仓」。"""
    payload = _payload(
        memory_server, http_get, auth,
        _FakeMemory(holdings={
            "available": False,
            "reason": "行情源未注入：本次装配未带本地行情库（装配归生产入口）",
            "rows": [],
        }),
        "/api/memory/holdings",
    )
    assert payload["status"] == "unavailable"
    assert "行情源未注入" in payload["reason"]
    assert payload["last_updated_at"]


def test_missing_face_is_fail_closed(ui_server, http_get, auth) -> None:
    """GWT-4：端口未注入 ⇒ `unavailable` + 点名（不 500、不回空表冒充）。"""
    for path in ("/api/memory/holdings", "/api/memory/watchlist"):
        payload = http_get(ui_server, path, headers=auth(ui_server)).json()
        assert payload["status"] == "unavailable"
        assert "未接入记忆面" in payload["reason"]


def test_unknown_scope_is_rejected() -> None:
    """面键由路径定（[01 §12] 动作不进描述）：不认识的面键 ⇒ `validation_failed`。"""
    payload = build_ui(host="127.0.0.1", port=1).api_memory_positions("nope")
    assert payload["status"] == "validation_failed"


def test_page_path_is_reachable(ui_server, http_get, auth) -> None:
    """GWT-2：`/memory` 属**静态壳档**——首次导航不必带令牌即回壳，取数在 `/api/*` 之后。"""
    assert "/memory" in PAGE_PATHS
    response = http_get(ui_server, "/memory")
    assert response.status == 200
    assert b"<!--ST_DEV_SCRIPT-->" in response.body or b"<html" in response.body.lower()


def test_frontend_registers_the_page_with_a_renderer() -> None:
    """GWT-2：前端登记表里 `/memory` 带 `render`（否则壳只出可用性探针，不出读面）。"""
    pages = (_UI / "web" / "js" / "pages.js").read_text(encoding="utf-8")
    assert "'/memory'" in pages and "renderMemoryPage" in pages
    assert (_UI / "web" / "js" / "pages" / "memory.js").is_file()
    page = (_UI / "web" / "js" / "pages" / "memory.js").read_text(encoding="utf-8")
    # 页面只装配：两段各走同一条 `renderEnvelope`，**不**自己拼颜色或方向符
    assert "/api/memory/holdings" in page and "/api/memory/watchlist" in page
    code = "\n".join(
        line for line in page.splitlines() if not line.lstrip().startswith("//")
    )
    assert "num--" not in code and "▲" not in code and "▼" not in code
