"""L3 测试夹具件（三支测试共用；时间锚点可注入，故结果可确定复算）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from st_agent.l2.memory import (
    EvolutionPoint,
    MemoryNode,
    checked_node,
    new_node_id,
)

CST = timezone(timedelta(hours=8))
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=CST)
LATER = datetime(2026, 9, 28, 12, 5, tzinfo=CST)
PASS = "l3-rig-passphrase"
TRACE_ID = "tr_" + "0" * 20
STOCK_A = "sh.600000"
STOCK_B = "sz.000001"

#: 六类节点的最小合法专属字段（04 §1 表逐项取值）。
OWN_DEFAULTS: dict[str, dict[str, Any]] = {
    "identity": {"risk_preference": "稳健", "investing_years": "5 年"},
    "attention": {"holdings": (STOCK_A,), "watchlist": (STOCK_B,),
                  "sector_preferences": ("银行",)},
    "thesis": {"subject": STOCK_A, "subject_kind": "stock", "view": "看好反转",
               "stated_at": NOW},
    "history": {"event": "买入 sh.600000", "outcome": "unknown"},
    "pattern": {"pattern": "总在周一加仓"},
    "evolution": {"dimension": "风险偏好",
                  "trajectory": (EvolutionPoint(at=NOW, value="偏稳健"),)},
}


def node(kind: str = "thesis", **over: Any) -> MemoryNode:
    """构造一个合法记忆节点（默认 thesis；``over`` 覆写任意字段）。"""
    fields: dict[str, Any] = {
        "type": kind,
        "memory_node_id": new_node_id(),
        "confidence": 0.8,
        "source": "user_stated",
        "privacy_level": "private",
        "created_at": NOW,
        "updated_at": NOW,
        **OWN_DEFAULTS[kind],
    }
    fields.update(over)
    return checked_node(**fields)
