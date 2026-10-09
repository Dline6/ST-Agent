"""记忆区的持仓 / 关注读面在**真装配**下端到端（[`T-UI-009.2`]）。

`tests/ui/test_ui_memory.py` 用替身钉信封与描述形态；本组用 M4 关卡的**真组合根**走一遍
真链路：真 `MemoryWriter` 落一个 `attention` 节点 → 真 `MemoryPositionsFacade` 读它 →
**真 `MarketDb`** 查最新日线 → 真回环面出描述。

行情是本地库里的**真值**（rig 播种 sh.600000 / sz.000001，并把最新交易日改成异动 +7.5% /
换手 12.0%），故涨跌列的数不是编出来的——这正是 `T-UI-009.1` 那套编码要接的东西。
"""

from __future__ import annotations

import http.client
import json

import pytest
from rig import ROOT_NAME
from rig_m4 import NOW, seeded_m4

from st_agent.app import MemoryPositionsFacade
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l2.memory import checked_node, new_node_id
from st_agent.ui.security import TOKEN_HEADER
from st_agent.ui.server import serve

HOLDING = "sh.600000"
WATCHED = "sz.000001"
UNCACHED = "sz.300750"
"""一个**记忆里有、本地行情库里没有**的标的——用来验「该行仍在、行情列缺席」。"""


def _attention(*, holdings: tuple[str, ...] = (), watchlist: tuple[str, ...] = ()):
    """一个合法的 `attention` 节点（[04 §1] 的最小专属字段）。"""
    return checked_node(
        type="attention",
        memory_node_id=new_node_id(),
        confidence=0.8,
        source="user_stated",
        privacy_level="private",
        created_at=NOW,
        updated_at=NOW,
        holdings=holdings,
        watchlist=watchlist,
        sector_preferences=(),
    )


def _facade(rig, *, market) -> MemoryPositionsFacade:
    """读面：`market` 显式给（`rig.feed` 为真库，`None` 为「行情源没注入」）。"""
    return MemoryPositionsFacade(reader=rig.m4.m1.reader, market=market)


def _get(running, path: str) -> dict:
    conn = http.client.HTTPConnection(running.host, running.port, timeout=5)
    try:
        conn.request("GET", path, headers={TOKEN_HEADER: running.token})
        return json.loads(conn.getresponse().read().decode("utf-8"))
    finally:
        conn.close()


@pytest.fixture()
def rig(tmp_path):
    """真 M4 装配 + 记忆里落一段持仓与一段关注池。"""
    built = seeded_m4(tmp_path / ROOT_NAME)
    built.m4.m1.writer.add_node(_attention(holdings=(HOLDING,), watchlist=(WATCHED,)))
    return built


def test_holdings_carry_real_quotes_from_the_local_market_db(rig) -> None:
    """GWT-1：行的每一项都来自真库——名称取自 `security`、行情取自最新日线视图。"""
    view = _facade(rig, market=rig.feed).positions("holdings")
    assert view["available"] is True and view["reason"] == ""
    (row,) = view["rows"]
    assert row["code"] == HOLDING
    assert row["code_name"] == "浦发银行"
    assert row["close"] == 0.50
    assert row["pct_chg"] == 7.5
    assert row["turn"] == 12.0


def test_watchlist_reads_the_other_attention_field(rig) -> None:
    """两段取自同一节点的**不同字段**（[04 §1]）——不是同一份数据的两次呈现。"""
    view = _facade(rig, market=rig.feed).positions("watchlist")
    assert [row["code"] for row in view["rows"]] == [WATCHED]
    assert view["rows"][0]["code_name"] == "平安银行"


def test_end_to_end_over_the_loopback_face(rig) -> None:
    """GWT-1：真回环面上出的描述——列级角色与数值一路不改地到表现层。"""
    with serve(dev=False, memory=_facade(rig, market=rig.feed)) as running:
        payload = _get(running, "/api/memory/holdings")
    assert payload["status"] == "ok"
    description = payload["data"]
    assert description["component_type"] == "table"
    assert [column["key"] for column in description["slots"]["columns"]] == [
        "code", "code_name", "close", "pct_chg", "turn",
    ]
    kinds = {column["key"]: column["kind"] for column in description["slots"]["columns"]}
    assert kinds["pct_chg"] == "direction" and kinds["close"] == "number"
    (row,) = description["slots"]["rows"]
    assert row["pct_chg"] == 7.5, "涨跌列到表现层的仍是**数**，编码归渲染件"


def test_holding_without_a_cached_bar_keeps_its_row_with_blank_quotes(tmp_path) -> None:
    """GWT-3：记忆里有、行情库里没有 ⇒ 那一行**仍在**，行情列缺席（不当 0、不留白）。"""
    built = seeded_m4(tmp_path / ROOT_NAME)
    built.m4.m1.writer.add_node(_attention(holdings=(HOLDING, UNCACHED)))
    view = _facade(built, market=built.feed).positions("holdings")
    rows = {row["code"]: row for row in view["rows"]}
    assert set(rows) == {HOLDING, UNCACHED}
    assert rows[UNCACHED]["close"] is None
    assert rows[UNCACHED]["pct_chg"] is None
    assert rows[UNCACHED]["code_name"] is None
    assert rows[HOLDING]["pct_chg"] == 7.5, "缺行情的那一行不得牵连其余行"


def test_no_quotes_and_no_positions_reports_empty_first(tmp_path) -> None:
    """GWT-3 的**优先级**：记忆里没有标的时回空行集——对着空持仓喊「行情不可用」是噪声。"""
    built = seeded_m4(tmp_path / ROOT_NAME)
    view = _facade(built, market=None).positions("holdings")
    assert view == {"available": True, "reason": "", "rows": []}


def test_unwired_quote_source_reports_unavailable_with_a_reason(rig) -> None:
    """GWT-3：行情源没注入 ⇒ `available=False` + 点名——**不**退化成「只有代码」的半表。"""
    view = _facade(rig, market=None).positions("holdings")
    assert view["available"] is False
    assert "行情源未注入" in view["reason"]
    assert view["rows"] == []


def test_market_db_query_failure_is_unavailable_not_empty(rig) -> None:
    """GWT-3：本地行情库查询**非 ok** ⇒ 也是 `unavailable`（据实转述它的原因）。"""

    class _BrokenMarket:
        def query(self, _sql, _params=()):
            return ResultEnvelope.unavailable("本地行情库未建库", last_updated_at=NOW)

    view = _facade(rig, market=_BrokenMarket()).positions("holdings")
    assert view["available"] is False
    assert "本地行情库未建库" in view["reason"]


def test_unknown_scope_is_refused_by_the_facade(rig) -> None:
    """面键是闭集（[01 §12] 的动作不进描述）——不认识的面键显式拒，不静默给空表。"""
    with pytest.raises(ValueError):
        _facade(rig, market=rig.feed).positions("portfolio")
