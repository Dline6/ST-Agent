"""记忆片段导入 / 继承（04 §8 第三段）。

[§8](../../../docs/技术架构-v2/04-L2-记忆图谱.md)：**导入他人公开片段**——作为
``source: inferred``（他人陈述）类节点入库，置信度低于本人表达，可编辑可删除；
导入数据同样过隐私分级。本模块是这一段的落点，消费
[`T-L2-004.2`](../../../项目管理/tasks/T-L2-004.2-记忆片段导出隐私过滤清单确认门.md)
交付的 [`FragmentPayload`](sharing.py)。

四条取向：

- **溯源自成一支**（[D-051](../../../项目管理/决策日志.md)）：导入并无本地推理链，而
  [01 §1](../../../docs/技术架构-v2/01-平台共享契约.md) 登记 ``trace_id`` 的产生方是
  L1 调度器。故 [§1](../../../docs/技术架构-v2/04-L2-记忆图谱.md) 的 ``provenance``
  放宽为 ``trace_id`` / ``imported`` 二选一，导入项填 ``imported``（分享者 / 导入时刻 /
  校验和 / 出处链），形状同 [01 §2](../../../src/st_agent/contracts/capability_types.py) /
  [09 §5](../../../docs/技术架构-v2/09-生态与分享.md)
- **置信度封顶到推断基线**（§8「低于本人表达」）：入库取值 = ``min(原值, inferred 基线)``。
  本人表达的基线更高，故导入项必然低于本人表达——不必再引入第三种 ``source``
- **幂等**（[D-052](../../../项目管理/决策日志.md)）：保留分享方的原 ``memory_node_id``，
  已存在即**跳过**并报告，不产生副本、不静默覆盖（同 §4「不存在任何静默覆盖」）
- **过隐私分级＝入口校验**（§8）：载荷里出现 ``private`` / ``sensitive`` 节点即**拒**
  ——合法的 ``.stmem`` 不会有它们（导出侧强制过滤），出现即说明文件被改过或损坏

**留痕**：每次导入落 ``execution_log`` 分区 ``memory-import/<import_id>.json``，
``import_id`` 是导入来源（分享者 + 导入时刻 + 校验和）的**确定性摘要**——同一来源重放
得同一 id，不同时刻的两次导入是两条记录。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from st_agent.contracts.identifiers import digest_id
from st_agent.l2.memory.confidence import ConfidenceModel
from st_agent.l2.memory.errors import MemoryValidationError
from st_agent.l2.memory.graph import MemoryGraph
from st_agent.l2.memory.models import (
    INTERNAL_EDGE_TYPES,
    ImportOrigin,
    MemoryEdge,
    MemoryNode,
    Provenance,
    _require_tz,
    checked_node,
    edge_key,
)
from st_agent.l2.memory.sharing import EXPORTABLE_PRIVACY_LEVELS, FragmentPayload
from st_agent.l2.memory.writer import MemoryWriter

__all__ = [
    "IMPORT_CONFIRMATION",
    "IMPORT_RECORD_PREFIX",
    "FragmentImporter",
    "ImportOutcome",
    "ImportRecord",
    "checked_origin",
    "new_import_id",
]

IMPORT_RECORD_PREFIX = "memory-import/"
"""``execution_log`` 分区内导入记录的目录前缀（09 §5 来源追溯的本机落点）。"""

IMPORT_CONFIRMATION = "user"
"""导入确认门的合法取值（导入是用户显式动作，同 §3.2 的确认门形状）。"""


def _now() -> datetime:
    """用户本地时区当前时刻（01 §8）。"""
    return datetime.now().astimezone()


def new_import_id(origin: ImportOrigin) -> str:
    """导入来源的**确定性摘要** ID（``imp_<20 位十六进制>``）。

    业务键＝分享者 + 导入时刻 + 校验和：同一来源重放得同一 id；不同时刻的两次导入是
    两条记录。**不新增契约 ID 类**（沿用 `T-SC-002` 取向）。
    """
    return digest_id("imp", origin.sharer, origin.imported_at.isoformat(), origin.checksum)


def checked_origin(**fields: Any) -> ImportOrigin:
    """构造一个导入来源（非法 → ``MemoryValidationError``，不抛裸 pydantic 异常）。"""
    try:
        return ImportOrigin(**fields)
    except ValidationError as exc:
        raise MemoryValidationError(f"导入来源非法：{exc}") from exc


class ImportRecord(BaseModel):
    """一次导入的审计记录（落 ``execution_log`` 分区；只增不改）。"""

    model_config = ConfigDict(frozen=True)

    import_id: str
    """业务键的确定性摘要（:func:`new_import_id`）。"""
    origin: ImportOrigin
    recorded_at: datetime
    """本机记下这条记录的时刻。"""
    created: tuple[str, ...]
    """本次真正入库的节点 id（升序）。"""
    skipped: tuple[str, ...]
    """因本机已存在而跳过的节点 id（升序）——幂等路径的可见证据。"""
    edges_added: tuple[str, ...]
    """本次入库的边键（升序）。"""
    edges_skipped: tuple[str, ...]
    """因端点不在图谱内而未入库的边键（升序）。"""
    payload_digest: str
    """载荷摘要（sha256 十六进制）——复核「这条记录对应哪份载荷」。"""

    @model_validator(mode="after")
    def _shape(self) -> "ImportRecord":
        _require_tz(self.recorded_at, "recorded_at")
        if not self.created and not self.skipped:
            raise MemoryValidationError("导入记录至少要有一条节点结果（入库或跳过）")
        return self


class ImportOutcome(BaseModel):
    """一次导入的产出（记录 + 入库的节点与边本体）。"""

    model_config = ConfigDict(frozen=True)

    record: ImportRecord
    nodes: tuple[MemoryNode, ...]
    edges: tuple[MemoryEdge, ...]


class FragmentImporter:
    """记忆片段的导入面（04 §8；只经 :class:`MemoryGraph` / :class:`MemoryWriter` 读写）。

    :param graph: :class:`MemoryGraph`（幂等判据与边不变量）
    :param writer: :class:`MemoryWriter`（写入面）
    :param confidence: :class:`ConfidenceModel`（取推断基线；缺省按图谱自建）
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    def __init__(
        self,
        graph: MemoryGraph,
        writer: MemoryWriter,
        *,
        confidence: ConfidenceModel | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._graph = graph
        self._writer = writer
        self._confidence = ConfidenceModel(graph) if confidence is None else confidence
        self._now = _now if now is None else now

    # ───────────────────────── 导入 ─────────────────────────

    def import_fragment(
        self,
        payload: FragmentPayload | Mapping[str, Any],
        *,
        origin: ImportOrigin | Mapping[str, Any],
        confirmed_by: str,
    ) -> ImportOutcome:
        """导入一个公开片段（§8；用户确认后调用）。

        :param payload: 片段载荷（``FragmentPayload`` 或其 JSON 形态）
        :param origin: 导入来源（分享者 / 导入时刻 / 校验和 / 出处链）
        :param confirmed_by: **只接受 ``"user"``** —— 导入是用户显式动作（§8），
            与 ``edit_node`` / ``delete`` / 片段导出的确认门同款

        入库节点的 ``source`` 一律改写为 ``inferred``（他人陈述），``provenance`` 换成
        ``imported``，``confidence`` 封顶到推断基线；节点 id 保留分享方的原值，故
        **重复导入天然幂等**（已存在者进 ``skipped``）。载荷携非 ``public`` 节点即拒。
        """
        if confirmed_by != IMPORT_CONFIRMATION:
            raise MemoryValidationError(
                f"导入片段的 confirmed_by 只接受 {IMPORT_CONFIRMATION!r}，得到 "
                f"{confirmed_by!r}——04 §8 的导入是用户显式动作"
            )
        checked = _checked_payload(payload)
        parsed_origin = (
            origin if isinstance(origin, ImportOrigin) else checked_origin(**dict(origin))
        )
        self._reject_non_public(checked)

        ceiling = self._confidence.baseline("inferred")
        created: list[MemoryNode] = []
        skipped: list[str] = []
        for node in sorted(checked.nodes, key=lambda n: n.memory_node_id):
            if self._graph.has_node(node.memory_node_id):
                skipped.append(node.memory_node_id)      # 幂等：已存在即跳过，不覆盖
                continue
            created.append(self._writer.add_node(_as_imported(node, parsed_origin, ceiling)))

        added: list[MemoryEdge] = []
        edges_skipped: list[str] = []
        for e in sorted(checked.edges, key=lambda x: (x.source_id, x.edge_type, x.target_id)):
            if not self._edge_importable(e):
                edges_skipped.append(edge_key(e))
                continue
            added.append(self._writer.add_edge(e))

        moment = self._now()
        record = ImportRecord(
            import_id=new_import_id(parsed_origin),
            origin=parsed_origin,
            recorded_at=moment,
            created=tuple(n.memory_node_id for n in created),
            skipped=tuple(skipped),
            edges_added=tuple(edge_key(e) for e in added),
            edges_skipped=tuple(edges_skipped),
            payload_digest=_payload_digest(checked),
        )
        if created or added:
            # 留痕只增不改（同 §6 审计取向）：纯跳过（这次什么都没入库）不重写既有记录，
            # 否则会把「这批节点是本导入建的」这条事实抹掉。
            self._write(record)
        return ImportOutcome(record=record, nodes=tuple(created), edges=tuple(added))

    # ───────────────────────── 审计面 ─────────────────────────

    def records(self) -> tuple[ImportRecord, ...]:
        """全部导入记录（按 ``import_id`` 升序）。"""
        return tuple(sorted((self._parse(self._graph.store.get("execution_log", p))
                             for p in self._files()), key=lambda r: r.import_id))

    def record_for(self, import_id: str) -> ImportRecord:
        """按 ``import_id`` 取一条记录（不存在 → ``MemoryValidationError``）。"""
        for record in self.records():
            if record.import_id == import_id:
                return record
        raise MemoryValidationError(f"无此导入记录：{import_id!r}")

    # ───────────────────────── 内部工具 ─────────────────────────

    @staticmethod
    def _reject_non_public(payload: FragmentPayload) -> None:
        """§8「导入数据同样过隐私分级」：非 ``public`` 节点即拒（不静默丢弃）。"""
        offenders = sorted(n.memory_node_id for n in payload.nodes
                           if n.privacy_level not in EXPORTABLE_PRIVACY_LEVELS)
        if offenders:
            raise MemoryValidationError(
                f"片段含非公开节点，拒绝导入：{offenders}——合法片段经导出侧强制过滤，"
                "出现即说明文件被改写或损坏（04 §8）"
            )

    def _edge_importable(self, e: MemoryEdge) -> bool:
        """边能否入库（内端须在图谱内；导入后不产生悬空边）。"""
        if not self._graph.has_node(e.source_id):
            return False
        if e.edge_type in INTERNAL_EDGE_TYPES:
            return self._graph.has_node(e.target_id)
        return True

    def _files(self) -> tuple[str, ...]:
        return tuple(p for p in self._graph.store.list_files("execution_log")
                     if p.startswith(IMPORT_RECORD_PREFIX) and p.endswith(".json"))

    @staticmethod
    def _path(import_id: str) -> str:
        return f"{IMPORT_RECORD_PREFIX}{import_id}.json"

    def _write(self, record: ImportRecord) -> None:
        self._graph.store.put("execution_log", self._path(record.import_id),
                              record.model_dump_json().encode("utf-8"))

    @staticmethod
    def _parse(raw: bytes) -> ImportRecord:
        try:
            return ImportRecord.model_validate_json(raw)
        except (ValueError, UnicodeDecodeError, ValidationError) as exc:
            raise MemoryValidationError(f"导入记录损坏无法解析：{exc}") from exc


def _checked_payload(payload: FragmentPayload | Mapping[str, Any]) -> FragmentPayload:
    """把载荷（对象或 JSON 形态）归一成 ``FragmentPayload``（非法 → 本层错误）。"""
    if isinstance(payload, FragmentPayload):
        return payload
    try:
        return FragmentPayload(**dict(payload))
    except (ValidationError, TypeError) as exc:
        raise MemoryValidationError(f"片段载荷非法：{exc}") from exc


def _as_imported(node: MemoryNode, origin: ImportOrigin, ceiling: float) -> MemoryNode:
    """把片段节点改写成「他人陈述」形态（§8）。

    只改来源相关四项：``source`` → ``inferred``、``provenance`` → ``imported``、
    ``confidence`` 封顶到推断基线、``revision_history`` 清空（与导出侧同口径）。
    节点 id / 类型 / 专属字段 / 时间戳 / 隐私分级一律原样保留。
    """
    fields = node.model_dump(mode="python")
    fields["source"] = "inferred"
    fields["provenance"] = Provenance(imported=origin)
    fields["confidence"] = min(float(node.confidence), ceiling)
    fields["revision_history"] = ()
    return checked_node(**fields)


def _payload_digest(payload: FragmentPayload) -> str:
    """载荷摘要（sha256 十六进制 64 位）——排序后的 JSON 保证同载荷同摘要。"""
    body = json.dumps(payload.model_dump(mode="json"), sort_keys=True,
                      ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()
