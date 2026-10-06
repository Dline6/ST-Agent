"""L5 层测试夹具件（两支用例共用；同 ``tests/eco/eco_helpers.py`` 的范式）。

时间锚点固定（``NOW``），故事件时刻、变更留痕时间都可复算；ID 全为 §1 形态的
确定性字符串（不经随机生成），故 `signal_id` 可逐字节复算。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from st_agent.contracts.time_events import PlatformEvent

CST = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 6, 8, 0, tzinfo=CST)
PASS = "l5-rig-passphrase"

LENS_A = "lens_" + "a" * 20
LENS_B = "lens_" + "b" * 20
LENS_C = "lens_" + "c" * 20
ANN_ID = "ann_" + "1" * 20
SNAP_ID = "snap_" + "2" * 20
RUN_ID = "run_" + "3" * 20
MN_ID = "mn_" + "4" * 20
TRACE_ID = "tr_" + "5" * 20

DEDUP_KEY = "sh.600000:announcement_density"
"""`dedup_key` 样例（同一标的同一事件类型，07 §6 的去重合并键）。"""

CONCLUSION = "该标的近两日公告密度高于其 30 日均值"
"""中性陈述式结论（不含第一人称 / 情感 / 对话体）。"""


def stance(lens_id: str, st: str, summary: str) -> dict[str, str]:
    """一条逐视角摘要（07 §1 的 `lens_stances[]` 元素）。"""
    return {"lens_id": lens_id, "stance": st, "summary": summary}


def content(
    *, conclusion: str = CONCLUSION, stances: list[dict[str, str]] | None = None
) -> dict[str, Any]:
    """内容载体（07 §1 的 `content_ref`）——缺省三视角，**方向不一致**（分歧并列）。"""
    if stances is None:
        stances = [
            stance(LENS_A, "positive", "资金面显示净流入"),
            stance(LENS_B, "negative", "基本面指标走弱"),
            stance(LENS_C, "neutral", "同类标的公告密度普遍上行"),
        ]
    return {"conclusion": conclusion, "lens_stances": stances}


def payload(**over: Any) -> dict[str, Any]:
    """`SignalEmitted` 的负载（01 §11 登记的五字段）。"""
    base: dict[str, Any] = {
        "level": "important",
        "content_ref": content(),
        "evidence_refs": [ANN_ID, RUN_ID],
        "dedup_key": DEDUP_KEY,
        "source_trace_id": TRACE_ID,
    }
    base.update(over)
    return base


def event(*, trace_id: str | None = TRACE_ID, occurred_at: datetime = NOW, **over: Any):
    """一条 `SignalEmitted` 事件（01 §11 信封）。"""
    return PlatformEvent(
        event="SignalEmitted", payload=payload(**over),
        trace_id=trace_id, occurred_at=occurred_at,
    )
