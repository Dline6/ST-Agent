"""L2 记忆图谱测试夹具件（四支测试共用）。

时间锚点全部可注入（``NOW``），故相关性/时近度判据在用例里可确定复算。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from st_agent.l2.memory import (
    NODE_TYPES,
    EvolutionPoint,
    MemoryEdge,
    MemoryNode,
    checked_edge,
    checked_node,
    new_node_id,
)

CST = timezone(timedelta(hours=8))
NOW = datetime(2026, 9, 27, 12, 0, tzinfo=CST)
TRACE_ID = "tr_" + "0" * 20
STOCK_ID = "sh.600000"
ANNOUNCEMENT_ID = "ann_" + "1" * 20
PASS = "l2-rig-passphrase"

#: 六类节点的最小合法专属字段（§1 表逐项取值）。
OWN_DEFAULTS: dict[str, dict[str, Any]] = {
    "identity": {"risk_preference": "稳健", "investing_years": "5 年"},
    "attention": {"holdings": (STOCK_ID,), "sector_preferences": ("银行",)},
    "thesis": {"subject": STOCK_ID, "subject_kind": "stock", "view": "看好反转",
               "stated_at": NOW},
    "history": {"event": "买入 sh.600000", "outcome": "unknown"},
    "pattern": {"pattern": "总在周一冲动加仓"},
    "evolution": {"dimension": "风险偏好",
                  "trajectory": (EvolutionPoint(at=NOW, value="偏激进"),)},
}


def node(kind: str = "thesis", **over: Any) -> MemoryNode:
    """构造一个合法节点（默认 thesis；``over`` 覆写任意字段）。

    推断类节点须由调用方**显式**给出 ``provenance``——不自动补，否则「缺 trace
    即拒」这条不变量在测试里就验不出来了。
    """
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


def edge(edge_type: str, source_id: str, target_id: str, at: datetime = NOW) -> MemoryEdge:
    """构造一条边。"""
    return checked_edge(edge_type=edge_type, source_id=source_id, target_id=target_id,
                        created_at=at)


def node_types() -> tuple[str, ...]:
    """§1 六类节点（供参数化用例遍历）。"""
    return NODE_TYPES
