"""会话模型（05 §1）。

多轮、有状态、可回溯、**可分支**的树状会话；对话历史落 L0 ``chat_history``
分区（完全离线可查、可搜索、可导出 Markdown）；会话上下文 ＝ 当前活跃链路的
消息 ＋ [04 §3.1](../../../docs/技术架构-v2/04-L2-记忆图谱.md) ``MemoryReader``
切片，二者共用一份 Token 预算。

**树结构**：消息是节点，``parent_id`` 是边——``None`` 即根。任取一条消息发起
分支（:meth:`SessionStore.branch_from`）只多一个子节点，原链路的后续消息
``parent_id`` 一字不改，故分支不破坏既有回溯。

**默认链路**：从根沿「最早创建的子消息」下行到底（:meth:`SessionStore.chain`
不传 ``leaf_id`` 时）。:meth:`SessionStore.append` 的 ``parent_id=None`` 即挂到
该链路的末端。

**取值口径**（本叶假设 A1–A4，见任务文件）：

- 落盘粒度＝**一会话一文件**（``chat_history`` 分区的 ``session/<id>.json``）
- 搜索是**扫描式**（``list_files`` + 逐条 ``get``），不建派生索引——索引会成为
  第二个事实源
- 标识是**本地**的（``sess_`` / ``msg_`` 前缀），**不新增契约 ID 类**
  （[01 §1](../../../docs/技术架构-v2/01-平台共享契约.md) 的 14 类无会话 / 消息，
  新增即契约变更；取向同 ``T-SC-002``）。``message_id`` 是**业务键的确定性摘要**
  （会话 + 父消息 + 角色 + 文本 + 时刻），故同一输入重放得同一 id
- Token 预算按**字符数近似**（同 [T-L2-001.3](../../../项目管理/tasks/T-L2-001.3-MemoryReader上下文切片查询.md)
  假设 A1），并在「消息 / 记忆切片」之间显式分配

**中性视角（铁律 2）**：会话消息是**用户数据**，读回与导出**原样**呈现、不过
[01 §6](../../../docs/技术架构-v2/01-平台共享契约.md) 输出校验（口径见
[D-053](../../../项目管理/决策日志.md)）。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from st_agent.contracts.identifiers import digest_id
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.storage.store import Store
from st_agent.l2.memory.reader import MemoryReader, MemorySliceResult, SliceQuery
from st_agent.l3.errors import SessionNotFoundError, SessionValidationError

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

CHAT_PARTITION = "chat_history"
"""对话历史分区（[02 §2.1](../../../docs/技术架构-v2/02-L0-本地优先基座.md)，消费方即 L3）。"""

SESSION_PREFIX = "session/"
"""``chat_history`` 分区内会话记录的路径前缀（一会话一文件）。"""

DEFAULT_CONTEXT_BUDGET = 4000
"""上下文装配的默认 Token 预算（字符数近似；§1 未定默认值）。"""

MEMORY_BUDGET_SHARE = 0.5
"""预算中划给记忆切片的比例（其余留给会话消息）——两者共用一份预算的分配口径。"""

ChatRole = Literal["user", "agent"]
"""消息角色。**功能化取值**（铁律 2：不用人名 / 人格化称谓）。"""

ROLE_LABELS: dict[str, str] = {"user": "用户", "agent": "副驾"}
"""导出 / 渲染用的角色标签（05 §3.2 的既有称谓；过 §6 输出校验，见模块文档）。"""


def new_session_id() -> str:
    """新的 ``session_id``（本机生成，形态 ``sess_<20 位十六进制>``）。

    会话无「业务键」可供确定性摘要（同一时刻可开任意多会话），故取随机形态——
    与 ``ev_`` / ``viol_`` 的既有本地标识同构，**不新增契约 ID 类**。
    """
    return f"sess_{uuid.uuid4().hex[:20]}"


def new_message_id(
    *,
    session_id: str,
    parent_id: str | None,
    role: str,
    text: str,
    created_at: datetime,
) -> str:
    """消息的**确定性摘要** ID（``msg_<20 位十六进制>``）。

    业务键＝会话 + 父消息 + 角色 + 文本 + 时刻：同一输入重放得同一 id（幂等可复核）；
    同一父消息下同一时刻的同一文本会得到同一 id——这是**有意的**，重复追加即被
    :class:`ChatSession` 的唯一性校验显式拒掉，而不是静默产生两条副本。
    """
    return digest_id("msg", session_id, parent_id or "", role, text, created_at.isoformat())


class ChatMessage(BaseModel):
    """一条会话消息（05 §1 的树节点）。"""

    model_config = ConfigDict(frozen=True)

    message_id: Annotated[str, Field(min_length=3, max_length=128)]
    session_id: Annotated[str, Field(min_length=3, max_length=128)]
    parent_id: Annotated[str, Field(min_length=3, max_length=128)] | None = None
    """父消息；``None`` 即该会话的根。"""
    role: ChatRole
    text: str
    created_at: datetime

    @model_validator(mode="after")
    def _message_shape(self) -> "ChatMessage":
        if self.created_at.tzinfo is None:
            raise SessionValidationError("created_at 必须带时区语义（01 §8：内部传输带时区）")
        if not self.text.strip():
            raise SessionValidationError("消息文本不得为空白（空消息无意义）")
        return self


class ChatSession(BaseModel):
    """一个会话（树状消息集合 + 会话级元数据）。"""

    model_config = ConfigDict(frozen=True)

    session_id: Annotated[str, Field(min_length=3, max_length=128)]
    title: str | None = None
    created_at: datetime
    updated_at: datetime
    messages: tuple[ChatMessage, ...] = ()

    @model_validator(mode="after")
    def _session_shape(self) -> "ChatSession":
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise SessionValidationError("会话时间字段必须带时区语义（01 §8）")
        if self.updated_at < self.created_at:
            raise SessionValidationError("updated_at 不得早于 created_at")

        ids = [m.message_id for m in self.messages]
        if len(set(ids)) != len(ids):
            raise SessionValidationError("同一会话内 message_id 不得重复")
        if any(m.session_id != self.session_id for m in self.messages):
            raise SessionValidationError("消息的 session_id 必须与会话一致")

        roots = [m for m in self.messages if m.parent_id is None]
        if len(roots) > 1:
            raise SessionValidationError(
                f"会话至多一个根消息，得到 {len(roots)} 条（parent_id=None）"
            )
        known = set(ids)
        for m in self.messages:
            if m.parent_id is not None and m.parent_id not in known:
                raise SessionValidationError(
                    f"消息 {m.message_id} 的父消息 {m.parent_id} 不在本会话内（悬空边）"
                )
            if m.parent_id == m.message_id:
                raise SessionValidationError(f"消息 {m.message_id} 不得以自身为父（自环）")
            if m.created_at < self.created_at or m.created_at > self.updated_at:
                raise SessionValidationError(
                    f"消息 {m.message_id} 的 created_at 超出会话时间区间"
                )
        _assert_acyclic(self.messages)
        return self


def checked_session(**fields) -> ChatSession:
    """构造一个会话（非法 → :class:`SessionValidationError`，不抛裸 pydantic 异常）。

    pydantic 会把校验期内抛出的 ``SessionValidationError`` 再包一层
    ``ValidationError``；本工厂把它还原为本层错误类型——**取数面与写入面一律经它
    构造**，使「非法即显式 ``SessionValidationError``」在 API 形状上成立。
    同 [T-L2-001.1](../../../项目管理/tasks/T-L2-001.1-记忆图谱本体节点边模型与落盘.md)
    的 ``checked_node`` 先例。
    """
    try:
        return ChatSession(**fields)
    except ValidationError as exc:
        raise SessionValidationError(f"会话非法：{exc}") from exc


def _assert_acyclic(messages: Iterable[ChatMessage]) -> None:
    """树的不变量：沿 ``parent_id`` 上溯必达根（有环即报错）。"""
    parent = {m.message_id: m.parent_id for m in messages}
    for start in parent:
        seen: set[str] = set()
        node: str | None = start
        while node is not None:
            if node in seen:
                raise SessionValidationError(f"消息父链存在环，起点 {start}")
            seen.add(node)
            node = parent.get(node)
    return None


class SessionSearchHit(BaseModel):
    """一条搜索命中（消息 + 定位信息）。"""

    model_config = ConfigDict(frozen=True)

    session_id: str
    message_id: str
    role: ChatRole
    created_at: datetime
    snippet: str
    """命中处上下文片段（**用户数据原样**，不过 §6 输出校验）。"""


class SessionSearchResult(BaseModel):
    """搜索产出（§5 信封 + 命中列表）。"""

    model_config = ConfigDict(frozen=True)

    envelope: ResultEnvelope
    hits: tuple[SessionSearchHit, ...] = ()


class SessionContext(BaseModel):
    """一次上下文装配的产出（会话消息 + 记忆切片，共用一份预算）。"""

    model_config = ConfigDict(frozen=True)

    envelope: ResultEnvelope
    messages: tuple[ChatMessage, ...] = ()
    """保留的会话消息（默认链路的**近端若干条**，时间序）。"""
    memory: MemorySliceResult
    """记忆切片结果（`empty` 时 `slices` 为空、带原因）。"""
    token_budget: int
    used: int
    """消息 + 切片合计占用的字符数（**不超过** ``token_budget``）。"""
    as_of: datetime | None = None


class SessionStore:
    """会话的读写面（05 §1；落 ``chat_history`` 分区）。

    不缓存内存副本——每次调用从盘上读、改完即写，避免「盘已改、内存副本仍旧」
    这类双事实源（同 [T-L1-008](../../../项目管理/tasks/done/M0/T-L1-008-陈旧禁用旗标自愈与悬空引用显式化.md) 的修法取向）。
    """

    def __init__(self, store: Store) -> None:
        self._store = store

    # ───────────────────────── 会话生命周期 ─────────────────────────

    def create(self, *, title: str | None = None, now: datetime | None = None) -> ChatSession:
        """新建一个空会话并落盘。"""
        moment = now if now is not None else _now()
        session = checked_session(
            session_id=new_session_id(),
            title=title,
            created_at=moment,
            updated_at=moment,
            messages=(),
        )
        return self.save(session)

    def load(self, session_id: str) -> ChatSession:
        """按 id 读回会话（不存在 → :class:`SessionNotFoundError`）。"""
        try:
            raw = self._store.get(CHAT_PARTITION, _key(session_id))
        except Exception as exc:  # Store 对缺文件抛 KeyError 族；统一为 L3 错误
            raise SessionNotFoundError(f"会话不存在：{session_id}") from exc
        try:
            return ChatSession.model_validate_json(raw)
        except ValidationError as exc:
            raise SessionValidationError(f"会话记录损坏无法解析：{exc}") from exc

    def save(self, session: ChatSession) -> ChatSession:
        """整份写回会话（清单与密文同批更新）。"""
        self._store.put(CHAT_PARTITION, _key(session.session_id),
                        session.model_dump_json().encode("utf-8"))
        return session

    def sessions(self) -> tuple[str, ...]:
        """全部会话 id（按 id 字典序）。"""
        ids = [p[len(SESSION_PREFIX):-len(".json")]
               for p in self._store.list_files(CHAT_PARTITION)
               if p.startswith(SESSION_PREFIX) and p.endswith(".json")]
        return tuple(sorted(ids))

    # ───────────────────────── 追加与分支 ─────────────────────────

    def append(
        self,
        session_id: str,
        text: str,
        *,
        role: ChatRole = "user",
        parent_id: str | None = None,
        now: datetime | None = None,
    ) -> ChatMessage:
        """追加一条消息。

        ``parent_id=None`` 时挂到**默认链路的末端**；给了 ``parent_id`` 即从该消息
        分出新支（与 :meth:`branch_from` 等价，后者只是把意图写在名字上）。
        """
        session = self.load(session_id)
        if parent_id is None:
            tail = self.chain(session)
            parent_id = tail[-1].message_id if tail else None
        return self._attach(session, text, role=role, parent_id=parent_id, now=now)

    def branch_from(
        self,
        session_id: str,
        message_id: str,
        text: str,
        *,
        role: ChatRole = "user",
        now: datetime | None = None,
    ) -> ChatMessage:
        """从既有消息发起一条新分支（§1「任意消息可发起分支」）。"""
        session = self.load(session_id)
        if message_id not in {m.message_id for m in session.messages}:
            raise SessionValidationError(f"分支起点不在本会话内：{message_id}")
        return self._attach(session, text, role=role, parent_id=message_id, now=now)

    def _attach(
        self,
        session: ChatSession,
        text: str,
        *,
        role: str,
        parent_id: str | None,
        now: datetime | None,
    ) -> ChatMessage:
        moment = now if now is not None else _now()
        message = ChatMessage(
            message_id=new_message_id(
                session_id=session.session_id, parent_id=parent_id,
                role=role, text=text, created_at=moment,
            ),
            session_id=session.session_id,
            parent_id=parent_id,
            role=role,  # type: ignore[arg-type]
            text=text,
            created_at=moment,
        )
        # 显式重建而非 ``model_copy``——后者**不重跑校验**，重复 message_id 会被静默
        # 放过（红-绿已验：退回 model_copy 时本叶用例 FAIL）。
        self.save(checked_session(
            session_id=session.session_id,
            title=session.title,
            created_at=session.created_at,
            updated_at=max(session.updated_at, moment),
            messages=(*session.messages, message),
        ))
        return message

    # ───────────────────────── 链路 ─────────────────────────

    def chain(self, session: ChatSession, *, leaf_id: str | None = None) -> tuple[ChatMessage, ...]:
        """取一条链路（根 → 叶）。

        不给 ``leaf_id`` 即**默认链路**：从根沿「最早创建的子消息」下行到底——
        即从未分支过的那条主线。给了 ``leaf_id`` 则上溯到根后正序返回。
        """
        if not session.messages:
            return ()
        index = {m.message_id: m for m in session.messages}
        if leaf_id is not None:
            if leaf_id not in index:
                raise SessionValidationError(f"链路末端不在本会话内：{leaf_id}")
            walked: list[ChatMessage] = []
            node: ChatMessage | None = index[leaf_id]
            while node is not None:
                walked.append(node)
                node = index[node.parent_id] if node.parent_id is not None else None
            return tuple(reversed(walked))

        by_parent: dict[str | None, list[ChatMessage]] = {}
        for m in session.messages:
            by_parent.setdefault(m.parent_id, []).append(m)
        roots = by_parent.get(None, [])
        if not roots:
            raise SessionValidationError("会话无根消息（树不完整）")
        node = roots[0]
        out = [node]
        while True:
            kids = by_parent.get(node.message_id)
            if not kids:
                break
            node = kids[0]
            out.append(node)
        return tuple(out)

    # ───────────────────────── 搜索 ─────────────────────────

    def search(
        self,
        keyword: str,
        *,
        session_id: str | None = None,
        limit: int | None = None,
    ) -> SessionSearchResult:
        """按关键词搜索消息（大小写不敏感的子串命中）。

        无命中 → `empty` 信封并带原因（01 §5：合法的空结果必须携带原因）。
        """
        needle = keyword.strip()
        if not needle:
            return SessionSearchResult(envelope=ResultEnvelope.empty("搜索关键词为空"))
        ids = (session_id,) if session_id is not None else self.sessions()
        hits: list[SessionSearchHit] = []
        for sid in ids:
            for m in self.load(sid).messages:
                if needle.lower() in m.text.lower():
                    hits.append(SessionSearchHit(
                        session_id=sid, message_id=m.message_id, role=m.role,
                        created_at=m.created_at, snippet=_snippet(m.text, needle),
                    ))
        hits.sort(key=lambda h: (h.created_at, h.session_id, h.message_id))
        if limit is not None:
            hits = hits[:max(0, limit)]
        if not hits:
            return SessionSearchResult(
                envelope=ResultEnvelope.empty(f"无消息命中「{needle}」")
            )
        return SessionSearchResult(envelope=ResultEnvelope.ok(list(hits)), hits=tuple(hits))

    # ───────────────────────── 导出 ─────────────────────────

    def export_markdown(self, session: ChatSession | str) -> str:
        """导出会话为 Markdown（同一会话重复导出**得同一文本**）。"""
        if isinstance(session, str):
            session = self.load(session)
        ordered = sorted(session.messages, key=lambda m: (m.created_at, m.message_id))
        lines = [
            f"# 会话记录 {session.session_id}",
            "",
            f"- 标题：{session.title or '（未命名）'}",
            f"- 创建时间：{session.created_at.isoformat()}",
            f"- 消息数：{len(ordered)}",
            "",
        ]
        previous: str | None = None
        for i, m in enumerate(ordered, start=1):
            lines += [f"## {i}. {ROLE_LABELS.get(m.role, m.role)} · {m.created_at.isoformat()}", ""]
            if m.parent_id is not None and m.parent_id != previous:
                lines += [f"- 分支自：{m.parent_id}", ""]
            lines += [m.text, ""]
            previous = m.message_id
        return "\n".join(lines).rstrip() + "\n"

    # ───────────────────────── 上下文装配 ─────────────────────────

    def assemble_context(
        self,
        session_id: str,
        reader: MemoryReader,
        *,
        topic: str = "",
        token_budget: int = DEFAULT_CONTEXT_BUDGET,
    ) -> SessionContext:
        """装配会话上下文（会话消息 + 记忆切片，共用 ``token_budget``）。"""
        if token_budget < 1:
            raise SessionValidationError("token_budget 须 ≥ 1")
        return assemble_context(
            self.load(session_id), reader,
            store=self, topic=topic, token_budget=token_budget,
        )


def assemble_context(
    session: ChatSession,
    reader: MemoryReader,
    *,
    store: SessionStore | None = None,
    topic: str = "",
    token_budget: int = DEFAULT_CONTEXT_BUDGET,
) -> SessionContext:
    """装配会话上下文：默认链路的**近端若干条**消息 + 记忆切片。

    预算分配（假设 A4）：记忆切片先划 ``token_budget * MEMORY_BUDGET_SHARE``，
    余下留给消息；消息**优先保近**——从链路末端往前收，收不下即停（与 L2 的截断
    同向：超预算即停，不跳过大的去凑小的）。两侧合计**不超** ``token_budget``。
    """
    if token_budget < 1:
        raise SessionValidationError("token_budget 须 ≥ 1")

    chain = store.chain(session) if store is not None else _default_chain(session)
    memory_budget = max(1, int(token_budget * MEMORY_BUDGET_SHARE))
    message_budget = max(0, token_budget - memory_budget)

    kept: list[ChatMessage] = []
    used = 0
    for m in reversed(chain):
        size = len(m.model_dump_json())
        if used + size > message_budget:
            break
        kept.append(m)
        used += size
    kept.reverse()

    memory = reader.query(SliceQuery(
        task_type="chat", topic=topic, token_budget=memory_budget,
    ))
    used += sum(len(s.model_dump_json()) for s in memory.slices)

    if not kept and not memory.slices:
        envelope = ResultEnvelope.empty(
            "上下文为空：会话无消息，且该主题下无可用记忆切片"
        )
    else:
        envelope = ResultEnvelope.ok(list(kept), as_of=memory.as_of)
    return SessionContext(
        envelope=envelope, messages=tuple(kept), memory=memory,
        token_budget=token_budget, used=used, as_of=memory.as_of,
    )


def _default_chain(session: ChatSession) -> tuple[ChatMessage, ...]:
    """无 ``SessionStore`` 时的默认链路（同 :meth:`SessionStore.chain` 的口径）。"""
    if not session.messages:
        return ()
    by_parent: dict[str | None, list[ChatMessage]] = {}
    for m in session.messages:
        by_parent.setdefault(m.parent_id, []).append(m)
    roots = by_parent.get(None, [])
    if not roots:
        raise SessionValidationError("会话无根消息（树不完整）")
    node = roots[0]
    out = [node]
    while True:
        kids = by_parent.get(node.message_id)
        if not kids:
            break
        node = kids[0]
        out.append(node)
    return tuple(out)


def _snippet(text: str, needle: str, *, width: int = 20) -> str:
    """命中处上下文片段（前后各取 ``width`` 个字符，截断处加省略号）。"""
    at = text.lower().find(needle.lower())
    if at < 0:
        return text[: width * 2]
    start = max(0, at - width)
    end = min(len(text), at + len(needle) + width)
    return ("…" if start > 0 else "") + text[start:end] + ("…" if end < len(text) else "")


def _key(session_id: str) -> str:
    """会话在 ``chat_history`` 分区内的相对路径。"""
    return f"{SESSION_PREFIX}{session_id}.json"


def _now() -> datetime:
    """用户本地时区当前时刻（01 §8）。"""
    return datetime.now().astimezone()
