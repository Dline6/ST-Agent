"""T-L3-001.1 · 会话模型（05 §1；GWT-1..5）。"""

from __future__ import annotations

from datetime import timedelta

import pytest
from l3_helpers import NOW, PASS, STOCK_A, node

from st_agent.l0.storage import Store
from st_agent.l2.memory import MemoryReader, MemoryWriter
from st_agent.l3 import SessionNotFoundError, SessionValidationError
from st_agent.l3.chat import (
    DEFAULT_CONTEXT_BUDGET,
    ROLE_LABELS,
    SessionStore,
    assemble_context,
    new_message_id,
)

SESSION_ID = "sess_" + "a" * 20


class TestGwt1TreeAndBranching:
    """GWT-1：树状会话——分支不改动原链路。"""

    def test_branch_keeps_the_original_line_intact(self, chat: SessionStore) -> None:
        s = chat.create(now=NOW)
        first = chat.append(s.session_id, "关注 ST 名单", now=NOW)
        second = chat.append(s.session_id, "已记录关注面", role="agent",
                             now=NOW + timedelta(seconds=1))

        branch = chat.branch_from(s.session_id, first.message_id, "换个方向",
                                  now=NOW + timedelta(seconds=2))
        loaded = chat.load(s.session_id)

        assert branch.parent_id == first.message_id
        assert len(loaded.messages) == 3
        # 原链路的后续消息一字未改
        after = [m for m in loaded.messages if m.message_id == second.message_id][0]
        assert after.parent_id == first.message_id
        assert [m.message_id for m in chat.chain(loaded)] == [
            first.message_id, second.message_id]
        assert [m.message_id for m in chat.chain(loaded, leaf_id=branch.message_id)] == [
            first.message_id, branch.message_id]

    def test_default_append_goes_to_the_default_line_tip(self, chat: SessionStore) -> None:
        s = chat.create(now=NOW)
        first = chat.append(s.session_id, "A", now=NOW)
        tip = chat.append(s.session_id, "B", now=NOW + timedelta(seconds=1))
        chat.branch_from(s.session_id, first.message_id, "C", now=NOW + timedelta(seconds=2))

        nxt = chat.append(s.session_id, "D", now=NOW + timedelta(seconds=3))
        assert nxt.parent_id == tip.message_id, "默认追加应挂到默认链路（未分支的主线）末端"

    def test_duplicate_message_id_is_rejected(self, chat: SessionStore) -> None:
        """同一父消息 + 同一时刻 + 同一文本 ⇒ 同一确定性 ID ⇒ 显式拒（不静默产副本）。"""
        s = chat.create(now=NOW)
        root = chat.append(s.session_id, "起点", now=NOW)
        chat.branch_from(s.session_id, root.message_id, "重复", now=NOW)
        with pytest.raises(SessionValidationError):
            chat.branch_from(s.session_id, root.message_id, "重复", now=NOW)
        assert len(chat.load(s.session_id).messages) == 2, "被拒的追加不得落盘"

    def test_message_id_is_a_deterministic_digest(self) -> None:
        kwargs = dict(session_id=SESSION_ID, parent_id=None, role="user",
                      text="同一输入", created_at=NOW)
        assert new_message_id(**kwargs) == new_message_id(**kwargs)
        assert new_message_id(**{**kwargs, "text": "另一输入"}) != new_message_id(**kwargs)


class TestGwt2PersistedAndReadableAfterRestart:
    """GWT-2：落盘后重新打开存储，树结构完整可读。"""

    def test_reopen_reads_the_whole_tree(self, store_root, chat: SessionStore) -> None:
        s = chat.create(title="首次可对话", now=NOW)
        a = chat.append(s.session_id, "第一问", now=NOW)
        b = chat.append(s.session_id, "第一答", role="agent", now=NOW + timedelta(seconds=1))

        reopened = SessionStore(Store.open(store_root, PASS))
        loaded = reopened.load(s.session_id)

        assert loaded.title == "首次可对话"
        assert [(m.message_id, m.parent_id, m.text) for m in loaded.messages] == [
            (a.message_id, None, "第一问"),
            (b.message_id, a.message_id, "第一答"),
        ]
        assert loaded.model_dump_json() == chat.load(s.session_id).model_dump_json()

    def test_unknown_session_is_not_silently_empty(self, chat: SessionStore) -> None:
        with pytest.raises(SessionNotFoundError):
            chat.load("sess_" + "f" * 20)


class TestGwt3Search:
    """GWT-3：跨会话搜索；无命中带原因。"""

    def test_hits_across_sessions(self, chat: SessionStore) -> None:
        one = chat.create(now=NOW)
        chat.append(one.session_id, "关注 ST 退市风险", now=NOW)
        two = chat.create(now=NOW)
        chat.append(two.session_id, "今天行情如何", now=NOW)

        result = chat.search("退市")
        assert result.envelope.status == "ok"
        assert len(result.hits) == 1
        assert result.hits[0].session_id == one.session_id
        assert "退市" in result.hits[0].snippet

    def test_scoped_search_and_no_hit_reason(self, chat: SessionStore) -> None:
        one = chat.create(now=NOW)
        chat.append(one.session_id, "关注 ST 名单", now=NOW)
        two = chat.create(now=NOW)
        chat.append(two.session_id, "关注 ST 名单", now=NOW)

        assert len(chat.search("名单", session_id=two.session_id).hits) == 1
        miss = chat.search("不存在的词")
        assert miss.envelope.status == "empty"
        assert miss.envelope.reason
        assert chat.search("   ").envelope.status == "empty"


class TestGwt4MarkdownExport:
    """GWT-4：导出稳定可复核，分支关系可辨。"""

    def test_export_is_deterministic_and_marks_branches(self, chat: SessionStore) -> None:
        s = chat.create(now=NOW)
        a = chat.append(s.session_id, "第一问", now=NOW)
        chat.append(s.session_id, "第一答", role="agent", now=NOW + timedelta(seconds=1))
        chat.branch_from(s.session_id, a.message_id, "另一路", now=NOW + timedelta(seconds=2))

        text = chat.export_markdown(s.session_id)
        assert text == chat.export_markdown(chat.load(s.session_id))
        assert "# 会话记录" in text
        assert ROLE_LABELS["user"] in text and ROLE_LABELS["agent"] in text
        assert f"分支自：{a.message_id}" in text
        assert text.count("分支自：") == 1, "只有分支消息才标分支关系"


class TestGwt5ContextAssembly:
    """GWT-5：上下文 = 会话消息 + 记忆切片，且不超预算。"""

    def test_assembly_within_budget(
        self, chat: SessionStore, reader: MemoryReader, writer: MemoryWriter
    ) -> None:
        writer.add_node(node("attention", holdings=(STOCK_A,), sector_preferences=("银行",)))
        s = chat.create(now=NOW)
        chat.append(s.session_id, "关注 银行 板块", now=NOW)
        chat.append(s.session_id, "已记录", role="agent", now=NOW + timedelta(seconds=1))

        ctx = chat.assemble_context(s.session_id, reader, topic="银行",
                                    token_budget=DEFAULT_CONTEXT_BUDGET)
        assert ctx.envelope.status == "ok"
        assert [m.text for m in ctx.messages] == ["关注 银行 板块", "已记录"]
        assert ctx.memory.slices, "记忆切片应非空"
        assert all(sl.as_of is not None for sl in ctx.memory.slices)
        assert all(0.0 <= sl.confidence <= 1.0 for sl in ctx.memory.slices)
        assert ctx.used <= ctx.token_budget

    def test_tight_budget_never_exceeds_the_limit(
        self, chat: SessionStore, reader: MemoryReader, writer: MemoryWriter
    ) -> None:
        writer.add_node(node("thesis", view="看好反转"))
        s = chat.create(now=NOW)
        for i in range(20):
            chat.append(s.session_id, f"第 {i} 条比较长的消息内容用于撑满预算",
                         now=NOW + timedelta(seconds=i))

        ctx = assemble_context(chat.load(s.session_id), reader, store=chat,
                               topic="反转", token_budget=60)
        assert ctx.used <= 60
        assert ctx.token_budget == 60

    def test_minimal_budget_keeps_nothing(
        self, chat: SessionStore, reader: MemoryReader
    ) -> None:
        s = chat.create(now=NOW)
        chat.append(s.session_id, "老消息", now=NOW)
        chat.append(s.session_id, "新消息", now=NOW + timedelta(seconds=1))

        ctx = chat.assemble_context(s.session_id, reader, token_budget=1)
        assert ctx.messages == (), "预算为 1 时消息全被截掉"
        assert ctx.used <= 1
        assert ctx.envelope.status == "empty"

    def test_bad_budget_is_rejected(self, chat: SessionStore, reader: MemoryReader) -> None:
        s = chat.create(now=NOW)
        with pytest.raises(SessionValidationError):
            chat.assemble_context(s.session_id, reader, token_budget=0)
