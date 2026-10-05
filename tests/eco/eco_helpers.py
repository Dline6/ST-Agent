"""ECO 层测试夹具件（四支用例共用；同 ``tests/l2/memory_helpers.py`` 的范式）。

时间锚点固定（``NOW``），故容器创建时间、卡片文件名与校验和全部可复算。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from st_agent.l2.memory import checked_node, new_node_id

CST = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 5, 18, 0, tzinfo=CST)
PASS = "eco-rig-passphrase"
STOCK_ID = "sh.600000"


def memory_node(*, privacy: str = "public", **over: Any):
    """构造一个合法记忆节点（缺省 thesis 类、``public`` 分级）。

    ``privacy`` 是 `.stmem` 强制三步的判据面：只有 ``public`` 能离开本机
    （[09 §2](../../docs/技术架构-v2/09-生态与分享.md)）。
    """
    fields: dict[str, Any] = {
        "type": "thesis",
        "memory_node_id": new_node_id(),
        "confidence": 0.8,
        "source": "user_stated",
        "privacy_level": privacy,
        "created_at": NOW,
        "updated_at": NOW,
        "subject": STOCK_ID,
        "subject_kind": "stock",
        "view": "看好反转",
        "stated_at": NOW,
    }
    fields.update(over)
    return checked_node(**fields)
