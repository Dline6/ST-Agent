"""L3 会话模型包（[05 §1](../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

对外只经本模块取用——实现细节（落盘形态、默认链路算法）不出包。
"""

from __future__ import annotations

from st_agent.l3.chat.session import (
    CHAT_PARTITION,
    DEFAULT_CONTEXT_BUDGET,
    MEMORY_BUDGET_SHARE,
    ROLE_LABELS,
    SESSION_PREFIX,
    ChatMessage,
    ChatRole,
    ChatSession,
    SessionContext,
    SessionSearchHit,
    SessionSearchResult,
    SessionStore,
    assemble_context,
    checked_session,
    new_message_id,
    new_session_id,
)

__all__ = [
    "CHAT_PARTITION",
    "DEFAULT_CONTEXT_BUDGET",
    "MEMORY_BUDGET_SHARE",
    "ROLE_LABELS",
    "SESSION_PREFIX",
    "ChatMessage",
    "ChatRole",
    "ChatSession",
    "SessionContext",
    "SessionSearchHit",
    "SessionSearchResult",
    "SessionStore",
    "assemble_context",
    "checked_session",
    "new_message_id",
    "new_session_id",
]
