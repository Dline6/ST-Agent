"""反思数据池（[08 §1](../../docs/技术架构-v2/08-L6-反思演进.md)）的验收用例。

对齐任务文件 [`T-L6-001.1`](../项目管理/tasks/T-L6-001.1-反思数据池.md) 的 GWT-1..5——
六字段落盘与幂等、逐动作计数与不合契约的拒收、窗口读面与「不属本面」、纯内存态与损坏即抛、
「不自铸 `feedback_id`、不外发」。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from l6_helpers import (
    FB_ADOPTED,
    FB_IGNORED,
    FB_LIKED,
    FB_QUERIED,
    FB_REJECTED,
    NOW,
    PASS,
    REJECT_REASON,
    feedback_event,
)
from st_agent.contracts.time_events import PlatformEvent
from st_agent.l0.storage import Store
from st_agent.l6 import (
    FEEDBACK_PREFIX,
    SOURCE,
    FeedbackPool,
    FeedbackPoolError,
    FeedbackPoolStore,
)

EARLIER = NOW - timedelta(days=3)


class TestGwt1SixFieldsAndIdempotence:
    """GWT-1：六字段完整落盘、可按 ID 读回；同 ID 重放**不产生第二条**。"""

    def test_consume_persists_the_six_fields(self, pool, store):
        entry = pool.consume(feedback_event(feedback_id=FB_ADOPTED))
        assert entry is not None
        assert entry.event.feedback_id == FB_ADOPTED
        assert entry.event.action == "adopted"
        assert entry.event.target.kind == "delivery"
        assert entry.event.timestamp == NOW
        assert entry.source == SOURCE
        assert store.get("reflection", FeedbackPoolStore.path_for(FB_ADOPTED))   # 真落盘

        back = pool.get(FB_ADOPTED)
        assert back is not None and back.event.feedback_id == FB_ADOPTED

    def test_same_feedback_id_replayed_stays_one_entry(self, pool):
        pool.consume(feedback_event(feedback_id=FB_ADOPTED))
        pool.consume(feedback_event(feedback_id=FB_ADOPTED))
        assert pool.counts().total == 1
        assert len(pool.all()) == 1

    def test_rejected_feedback_keeps_the_user_reason(self, pool):
        entry = pool.consume(feedback_event(
            feedback_id=FB_REJECTED, action="rejected", reason=REJECT_REASON,
        ))
        assert entry is not None and entry.event.reason == REJECT_REASON


class TestGwt2CountsAndRejection:
    """GWT-2：逐动作 / 窗口计数；不合契约**拒收 + 记因、不落盘**。"""

    def test_counts_cover_all_five_actions(self, pool):
        pool.consume(feedback_event(feedback_id=FB_ADOPTED, action="adopted"))
        pool.consume(feedback_event(feedback_id=FB_REJECTED, action="rejected",
                                    reason=REJECT_REASON))
        pool.consume(feedback_event(feedback_id=FB_IGNORED, action="ignored"))
        pool.consume(feedback_event(feedback_id=FB_LIKED, action="liked"))
        pool.consume(feedback_event(feedback_id=FB_QUERIED, action="queried"))
        counts = pool.counts()
        assert counts.total == 5
        assert counts.by_action["adopted"] == 1
        assert set(counts.by_action) == {"adopted", "ignored", "rejected", "queried", "liked"}

    def test_window_restricts_the_tally(self, pool):
        pool.consume(feedback_event(feedback_id=FB_ADOPTED, occurred_at=EARLIER))
        pool.consume(feedback_event(feedback_id=FB_LIKED, occurred_at=NOW))
        window = (NOW - timedelta(days=1), NOW)
        assert pool.counts(between=window).total == 1
        assert pool.between(*window)[0].event.feedback_id == FB_LIKED

    @pytest.mark.parametrize("payload, why", [
        ({"feedback_id": "", "target": {"kind": "delivery", "ref": "dlv_" + "1" * 20},
          "action": "adopted"}, "feedback_id 缺失"),
        ({"feedback_id": FB_ADOPTED, "target": {"kind": "delivery", "ref": "not-an-id"},
          "action": "adopted"}, "target.ref 不合 01 §1 形态"),
        ({"feedback_id": FB_ADOPTED, "target": {"kind": "delivery", "ref": "dlv_" + "1" * 20},
          "action": "rejected"}, "rejected 缺 reason"),
        ({"feedback_id": FB_ADOPTED, "target": {"kind": "delivery", "ref": "dlv_" + "1" * 20},
          "action": "shrugged"}, "未知动作"),
    ])
    def test_malformed_payload_is_rejected_and_not_persisted(self, pool, store, payload, why):
        event = PlatformEvent(event="FeedbackRecorded", payload=payload, occurred_at=NOW)
        with pytest.raises(FeedbackPoolError):
            pool.consume(event)
        assert pool.counts().total == 0, why
        assert store.list_files("reflection") == ()

    def test_rejected_missing_reason_via_collector_shape(self, pool):
        """`reason` 为空白的否定反馈同样拒收（08 §1「否定类建议必填入口」）。"""
        event = PlatformEvent(
            event="FeedbackRecorded",
            payload={"feedback_id": FB_REJECTED, "target": {"kind": "delivery",
                                                            "ref": "dlv_" + "1" * 20},
                     "action": "rejected", "reason": "   "},
            occurred_at=NOW,
        )
        with pytest.raises(FeedbackPoolError):
            pool.consume(event)


class TestGwt3ReadFaces:
    """GWT-3：窗口读面按发生时刻升序、空集合法；不属本面的事件返回 ``None``。"""

    def test_between_is_ordered_and_empty_is_legal(self, pool):
        pool.consume(feedback_event(feedback_id=FB_LIKED, occurred_at=NOW))
        pool.consume(feedback_event(feedback_id=FB_ADOPTED, occurred_at=EARLIER))
        got = pool.between(EARLIER - timedelta(days=1), NOW)
        assert [e.event.feedback_id for e in got] == [FB_ADOPTED, FB_LIKED]
        assert pool.between(NOW + timedelta(days=1), NOW + timedelta(days=2)) == ()
        assert pool.by_action("queried") == ()

    def test_between_rejects_naive_or_inverted_window(self, pool):
        with pytest.raises(FeedbackPoolError):
            pool.between(datetime(2026, 10, 1), NOW)
        with pytest.raises(FeedbackPoolError):
            pool.between(NOW, EARLIER)

    def test_foreign_events_are_not_ours(self, pool):
        other = PlatformEvent(event="SignalEmitted", payload={
            "level": "routine", "content_ref": {"conclusion": "x", "lens_stances": []},
            "evidence_refs": [], "dedup_key": "k", "source_trace_id": "tr_" + "5" * 20,
        }, trace_id="tr_" + "5" * 20, occurred_at=NOW)
        assert pool.consume(other) is None
        assert pool.counts().total == 0

    def test_non_mapping_payload_is_not_ours(self, pool):
        bare = SimpleNamespace(event="FeedbackRecorded", payload=["not", "a", "mapping"])
        assert pool.consume(bare) is None


class TestGwt4MemoryModeAndCorruption:
    """GWT-4：纯内存态不谎称已持久化；池内文件损坏**显式抛错**。"""

    def test_without_store_is_memory_only(self):
        first = FeedbackPool(now=lambda: NOW)
        first.consume(feedback_event(feedback_id=FB_ADOPTED))
        assert first.get(FB_ADOPTED) is not None
        assert FeedbackPool(now=lambda: NOW).get(FB_ADOPTED) is None   # 没落盘 ⇒ 新实例读不到

    def test_corrupt_record_raises_instead_of_reading_as_empty(self, store):
        path = FeedbackPoolStore.path_for(FB_ADOPTED)
        store.put("reflection", path, b"{not json")
        pool = FeedbackPool(store=store, now=lambda: NOW)
        with pytest.raises(FeedbackPoolError):
            pool.get(FB_ADOPTED)
        with pytest.raises(FeedbackPoolError):
            pool.all()
        with pytest.raises(FeedbackPoolError):
            pool.counts()

    def test_wrong_shape_record_raises(self, store):
        store.put("reflection", FeedbackPoolStore.path_for(FB_ADOPTED),
                  b'{"event": {"feedback_id": "x"}}')
        pool = FeedbackPool(store=store, now=lambda: NOW)
        with pytest.raises(FeedbackPoolError):
            pool.get(FB_ADOPTED)

    def test_prefix_and_paths_are_stable(self, tmp_path: Path):
        other = Store.create(tmp_path / "root2", PASS)
        pool = FeedbackPool(store=other, now=lambda: NOW)
        pool.consume(feedback_event(feedback_id=FB_ADOPTED))
        assert other.list_files("reflection") == (f"{FEEDBACK_PREFIX}{FB_ADOPTED}.json",)


class TestGwt5NoIdMintingNoEgress:
    """GWT-5：**不铸 `feedback_id`**、**不出网**（本机优先）。"""

    def test_l6_sources_never_generate_feedback_ids(self):
        src = Path(__file__).resolve().parents[2] / "src" / "st_agent" / "l6"
        for path in sorted(src.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            assert not re.search(r"FeedbackId\s*\.\s*generate", text), path.name
            assert "new_id(" not in text, path.name

    def test_l6_sources_do_not_reach_the_network(self):
        src = Path(__file__).resolve().parents[2] / "src" / "st_agent" / "l6"
        for path in sorted(src.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for banned in ("requests", "urllib", "http.client", "socket"):
                assert f"import {banned}" not in text, f"{path.name} 触网：{banned}"
