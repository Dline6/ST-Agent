"""官方 Pack 执行器的公共基件（T-L1-004.1；03 §1.2 · §1.3 + 02 §5）。

两件事：

- **取数只有一条通道**——执行器要行情 / 财务 / 板块数据时一律经
  :func:`query_rows` 进入 L0 数据源缓存（``MarketDb.query``）。``ok`` 摊平成
  行集；``empty`` / ``unavailable`` / ``failed`` **原样透出同一信封**——判定权在
  L0（§05 逐表水位与陈旧判据），L1 不另建第二套口径、不编造替代数据。
- **信封与证据的构造口径**——:func:`ok_envelope` / :func:`empty_envelope` 统一
  挂 ``as_of``；:func:`snapshot_ref` 给出 ``dataset_snapshot_id`` 证据引用。

取数源按**鸭子类型**接入（:class:`MarketQuerySource` 只要求 ``query``），
与 ``st_agent.l1.reuse.freshness`` 的 ``MarketFreshnessOracle`` 同一取向：
L1 不 import L0 具体类，测试可注入离线 Fake。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from st_agent.contracts.result_envelope import EvidenceRef, ResultEnvelope
from st_agent.l1.skills.official.errors import OfficialPackLoadError

__all__ = [
    "ROWS_KEY",
    "ExecutorStop",
    "MarketQuerySource",
    "RowsResult",
    "empty_envelope",
    "evidence_of",
    "fetch_rows",
    "ok_envelope",
    "query_rows",
    "round_of",
    "snapshot_ref",
    "stopping_factory",
]

ROWS_KEY = "rows"
"""``ok`` 载荷中行集的键（``MarketDb.query`` 的口径：``{'columns', 'rows'}``）。"""

SNAPSHOT_REF_LENGTH = 20
"""``dataset_snapshot_id`` 摘要位数（对齐 01 §1 ``snap_<uuid4 前 20 位>`` 的位数）。"""


@runtime_checkable
class MarketQuerySource(Protocol):
    """取数源（鸭子类型）：``st_agent.l0.market.MarketDb`` 即此形态。"""

    def query(self, sql: str, params: tuple = ()) -> ResultEnvelope:
        """执行一次只读查询 → ``ResultEnvelope``（``ok`` 载荷含 ``rows`` 键）。"""
        ...


class RowsResult(BaseModel):
    """``ok`` 信封摊平后的行集 + 该信封的 ``as_of``。"""

    model_config = ConfigDict(frozen=True)

    rows: tuple[dict[str, Any], ...]
    as_of: datetime | None = None


def query_rows(
    source: MarketQuerySource, sql: str, params: tuple = ()
) -> RowsResult | ResultEnvelope:
    """唯一取数入口：``ok`` → :class:`RowsResult`；其余状态 → 原封不动的信封。

    取数源返回非法类型或 ``ok`` 载荷缺 ``rows`` 键 → ``OfficialPackLoadError``
    （装载期缺陷，fail-fast，不静默当成空结果）。
    """
    envelope = source.query(sql, tuple(params))
    if not isinstance(envelope, ResultEnvelope):
        raise OfficialPackLoadError(
            f"取数源返回非法类型 {type(envelope).__name__}（须为 ResultEnvelope）"
        )
    if envelope.status != "ok":
        return envelope
    data = envelope.data
    if not isinstance(data, dict) or ROWS_KEY not in data:
        raise OfficialPackLoadError(
            f"取数源 ok 载荷缺 {ROWS_KEY!r} 键（口径同 MarketDb.query："
            "{'columns', 'rows'}）"
        )
    rows = data[ROWS_KEY]
    return RowsResult(rows=tuple(dict(r) for r in rows), as_of=envelope.as_of)


def ok_envelope(
    payload: Any, *, as_of: datetime | None = None,
    evidence_refs: tuple[EvidenceRef, ...] = (),
) -> ResultEnvelope:
    """构造 ``ok`` 信封（统一挂 ``as_of`` 与证据引用）。"""
    return ResultEnvelope.ok(payload, as_of=as_of, evidence_refs=tuple(evidence_refs))


def empty_envelope(
    reason: str, *, as_of: datetime | None = None,
    evidence_refs: tuple[EvidenceRef, ...] = (),
) -> ResultEnvelope:
    """构造 ``empty`` 信封（合法的空结果 + 原因说明，01 §5）。"""
    return ResultEnvelope.empty(reason, as_of=as_of, evidence_refs=tuple(evidence_refs))


def snapshot_ref(source: MarketQuerySource) -> EvidenceRef | None:
    """数据快照证据引用（01 §1 ``dataset_snapshot_id``）；取数源无水位接口时 → ``None``。

    水位取自 L0（``MarketDb.snapshot_id()``：各任务水位 + 成功时间的组合），
    但该组合串在满任务表下约 350 字符、且不是 ``snap_<20 位>`` 形态——超出
    ``EvidenceRef.ref`` 的 128 上限。故此处取**有界稳定摘要**：同一水位 → 同一
    ID、换水位 → 换 ID（确定性，可复核）。取数源未提供 ``snapshot_id`` 时返回
    ``None``（不编造证据）。
    """
    provider = getattr(source, "snapshot_id", None)
    if not callable(provider):
        return None
    composite = str(provider())
    digest = hashlib.sha256(composite.encode("utf-8")).hexdigest()[:SNAPSHOT_REF_LENGTH]
    return EvidenceRef(kind="dataset_snapshot_id", ref=f"snap_{digest}")


# ───────────────────────── 执行器共用的支撑件（两 Bundle 复用） ─────────────────────────

SkillBuilder = Callable[..., ResultEnvelope]
"""执行器体签名：``build(source, ctx, params) -> ResultEnvelope``。"""


class ExecutorStop(Exception):
    """取数未返回 ``ok`` 时中断执行器体；由 :func:`stopping_factory` 还原成同一信封。

    取数失败的**唯一**出口：执行器体内是线性逻辑，不必逐层判信封；越界发生
    （`unavailable` / `empty` / `failed`）时整个执行器即以该信封为结果——判定权
    留在 L0，不被改写、不编造替代数据。
    """

    __slots__ = ("envelope",)

    def __init__(self, envelope: ResultEnvelope) -> None:
        super().__init__(envelope.reason or envelope.status)
        self.envelope = envelope


def fetch_rows(
    source: MarketQuerySource, sql: str, params: tuple = ()
) -> RowsResult:
    """:func:`query_rows` 的执行器体版本——非 ``ok`` 即抛 :class:`ExecutorStop`。"""
    got = query_rows(source, sql, params)
    if isinstance(got, ResultEnvelope):
        raise ExecutorStop(got)
    return got


def stopping_factory(build: SkillBuilder) -> Callable[[MarketQuerySource], Any]:
    """把 ``build(source, ctx, params)`` 包成执行器工厂（``source -> SkillExecutor``）。"""

    def _make(source: MarketQuerySource):
        def _run(ctx: Any, params: dict[str, Any]) -> ResultEnvelope:
            try:
                return build(source, ctx, params)
            except ExecutorStop as stop:
                return stop.envelope

        return _run

    return _make


def evidence_of(source: MarketQuerySource) -> tuple[EvidenceRef, ...]:
    """把 :func:`snapshot_ref` 结果规整成 ``evidence_refs`` 元组（无水位接口则空）。"""
    ref = snapshot_ref(source)
    return (ref,) if ref is not None else ()


def round_of(value: Any, digits: int = 4) -> float | None:
    """四舍五入到指定位数（``None`` 原样透出，不把缺失当 0）。"""
    return None if value is None else round(float(value), digits)
