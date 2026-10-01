"""券商账单导入初始画像（04 §8 末条；任务 T-L2-005）。

[§8](../../../docs/技术架构-v2/04-L2-记忆图谱.md) 末条：从券商账单等外部数据导入初始
画像。产品口径见 [D-068](../../../项目管理/决策日志.md)——**通用 CSV/Excel 解析 + 导入时
用户指定列映射**：不预置逐券商适配器、不做 PDF 版式解析。本模块是这一段的落点，与
[`importer`](importer.py)（``.stmem`` 片段导入，**另一条**通道：他人公开片段 →
``inferred``）并列。

四条取向：

- **列映射由用户认定**（[D-068](../../../项目管理/决策日志.md)）：账单表头位置与列名因
  券商而异，故映射按**列名**给出，引擎自动**定位表头行**（首个含全部映射列名的行，
  兼容券商导出的前导说明行）。**必需列缺失即显式报缺**，不臆测列名、不静默跳过
- **来源＝用户本人数据**（§3.2）：导入节点 ``source=user_stated``（同 Onboarding），按
  §3.2 **直写**、不过 §4 的自主写入白名单与冲突队列；按 §1 **不带** ``provenance``
- **持仓与决策事件分落两类节点**（§1）：账单的证券代码集落 ``attention.holdings``，
  逐行成交 / 委托落 ``history.event``（无自由文本列时由日期 / 方向 / 数量 / 价格合成）
- **幂等＝内容去重**（§4「不存在任何静默覆盖」）：``memory_node_id`` 由本机随机生成
  （§1），故幂等靠**内容判据**——既有 ``attention`` 节点持仓相同、或既有 ``history``
  节点事件文本相同即**跳过**并报告，不产生副本、不覆盖

**隐私分级**（§8「导入数据同样过隐私分级」）：账单是个人数据，导入节点默认标
``private``；**不允许标 ``public``**（持仓 / 成交属个人面，公开由 [09 §2](../../../docs/技术架构-v2/09-生态与分享.md)
的导出过滤面另管）。见任务假设 `A1`。

**代码归一**（任务假设 `A3`）：账单常给裸 6 位代码，须归一到 ``stock_id``（
``sh.`` / ``sz.``）；北交所等未收录前缀（``8`` / ``4`` / ``920``）按
[D-032](../../../项目管理/决策日志.md) 口径**过滤并在载荷显式计数**，不静默丢弃。

**留痕**：**有入库**的导入落 ``execution_log`` 分区
``memory-statement-import/<import_id>.json``，``import_id`` 是账单内容摘要的确定性
摘要——同一账单重放得同一 id；纯跳过的重放**不重写**既有记录（同 [`importer`](importer.py)）。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any
from xml.etree import ElementTree

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from st_agent.contracts.identifiers import StockId, digest_id
from st_agent.l2.memory.errors import MemoryValidationError
from st_agent.l2.memory.graph import MemoryGraph
from st_agent.l2.memory.models import (
    MemoryNode,
    _require_tz,
    checked_node,
    new_node_id,
)
from st_agent.l2.memory.onboarding import DEFAULT_STATED_CONFIDENCE
from st_agent.l2.memory.writer import MemoryWriter

__all__ = [
    "MAPPING_FIELDS",
    "REQUIRED_MAPPING_FIELDS",
    "STATEMENT_CONFIRMATION",
    "STATEMENT_IMPORT_PREFIX",
    "STATEMENT_PRIVACY_LEVELS",
    "ColumnMapping",
    "ResolvedMapping",
    "StatementImportOutcome",
    "StatementImportRecord",
    "StatementImporter",
    "StatementPreview",
    "StatementTable",
    "checked_mapping",
    "new_statement_import_id",
    "normalize_stock_id",
    "read_table",
]

STATEMENT_IMPORT_PREFIX = "memory-statement-import/"
"""``execution_log`` 分区内账单导入记录的目录前缀（与片段导入的 ``memory-import/``
同构、前缀区分）。"""

STATEMENT_CONFIRMATION = "user"
"""导入确认门的合法取值（导入是用户显式动作，同 §3.2 / 片段导入的确认门形状）。"""

MAPPING_FIELDS: tuple[str, ...] = (
    "stock_code", "event", "date", "direction", "quantity", "price",
)
"""列映射可用的源字段名（账单列 → 源字段）。

- ``stock_code``（**必需**）：证券代码列 → 归一到 ``stock_id``，落 ``attention.holdings``
- ``event``（可选）：账单若带自由文本的事件 / 摘要列，直接作 ``history.event``
- ``date`` / ``direction`` / ``quantity`` / ``price``（可选）：无 ``event`` 列时，
  由这四列（连同 ``stock_code``）**合成** ``history.event`` 文本
"""

REQUIRED_MAPPING_FIELDS: tuple[str, ...] = ("stock_code",)
"""至少须映射的源字段（缺则**显式报缺**，见 :func:`checked_mapping`）。"""

STATEMENT_PRIVACY_LEVELS: tuple[str, ...] = ("private", "sensitive")
"""账单导入允许的隐私分级（**不含 ``public``**：持仓 / 成交属个人面，见模块文档）。"""

_EXCHANGE_BY_LEADING_DIGIT: dict[str, str] = {"6": "sh", "0": "sz", "3": "sz"}
"""裸 6 位代码 → 交易所前缀（沪深两市；其余前缀按 D-032 过滤）。"""

_DIGITS = re.compile(r"^\d{6}$")


def _system_now() -> datetime:
    """用户本地时区当前时刻（01 §8）。"""
    return datetime.now().astimezone()


# ───────────────────────── 表格解析（CSV / Excel） ─────────────────────────


class StatementTable(BaseModel):
    """一份账单的原始行（未定位表头；表头由 :func:`checked_mapping` 按列名定位）。"""

    model_config = ConfigDict(frozen=True)

    rows: tuple[tuple[str, ...], ...]


def read_table(data: bytes, *, fmt: str | None = None) -> StatementTable:
    """把账单文件字节解析成行（``fmt`` 取 ``"csv"`` / ``"xlsx"``；缺省按魔数判定）。

    - CSV：UTF-8（容忍 BOM），标准库 ``csv``
    - Excel（``.xlsx``）：标准库 ``zipfile`` + XML 读**首个工作表**，覆盖共享字符串 /
      内联字符串 / 数值；**不引入第三方依赖**（见任务假设 `A5`）。公式取缓存值；
      日期列若为 Excel 序列号，本读取器按数值原样返回（导出时选「文本」列可避免）
    """
    kind = fmt or ("xlsx" if data[:4] == b"PK\x03\x04" else "csv")
    if kind == "xlsx":
        rows = _read_xlsx(data)
    elif kind == "csv":
        rows = _read_csv(data)
    else:
        raise MemoryValidationError(f"未知的账单文件格式：{kind!r}（只支持 csv / xlsx）")
    if not rows:
        raise MemoryValidationError("账单表格为空（至少要有一行表头）")
    return StatementTable(rows=tuple(tuple(cell for cell in row) for row in rows))


def _read_csv(data: bytes) -> list[list[str]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise MemoryValidationError(f"账单 CSV 非 UTF-8 编码：{exc}") from exc
    return [list(row) for row in csv.reader(io.StringIO(text))]


def _read_xlsx(data: bytes) -> list[list[str]]:
    """读 ``.xlsx`` 首个工作表为行（标准库实现；损坏 → 本层错误）。"""
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            shared = _shared_strings(archive, ns)
            sheet = _first_sheet(archive)
            root = ElementTree.fromstring(sheet)
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise MemoryValidationError(f"账单 Excel 无法解析：{exc}") from exc

    rows: list[list[str]] = []
    for row in root.iter(f"{ns}row"):
        cells: dict[int, str] = {}
        for cell in row.findall(f"{ns}c"):
            index = _column_index(cell.get("r"))
            cells[index] = _cell_text(cell, ns, shared)
        width = max(cells) + 1 if cells else 0
        rows.append([cells.get(i, "") for i in range(width)])
    return rows


def _shared_strings(archive: zipfile.ZipFile, ns: str) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    return ["".join(t.text or "" for t in item.iter(f"{ns}t"))
            for item in root.findall(f"{ns}si")]


def _first_sheet(archive: zipfile.ZipFile) -> bytes:
    sheets = sorted(n for n in archive.namelist()
                    if n.startswith("xl/worksheets/") and n.endswith(".xml"))
    if not sheets:
        raise KeyError("xl/worksheets/sheet1.xml")
    return archive.read(sheets[0])


def _column_index(ref: str | None) -> int:
    """由单元格引用（如 ``B3``）取 0 基列号；缺引用时按 0。"""
    if not ref:
        return 0
    letters = "".join(ch for ch in ref if ch.isalpha())
    index = 0
    for ch in letters.upper():
        index = index * 26 + (ord(ch) - ord("A") + 1)
    return index - 1


def _cell_text(cell: ElementTree.Element, ns: str, shared: list[str]) -> str:
    """单元格取文本：共享字符串 / 内联字符串 / 原值（数值 / 布尔 / 公式缓存值）。"""
    kind = cell.get("t")
    if kind == "s":
        value = cell.find(f"{ns}v")
        if value is None or value.text is None:
            return ""
        try:
            return shared[int(value.text)]
        except (ValueError, IndexError) as exc:
            raise MemoryValidationError(f"账单 Excel 共享字符串索引非法：{value.text!r}") from exc
    if kind == "inlineStr":
        inline = cell.find(f"{ns}is")
        return "" if inline is None else "".join(t.text or "" for t in inline.iter(f"{ns}t"))
    value = cell.find(f"{ns}v")
    return "" if value is None or value.text is None else value.text


# ───────────────────────── 列映射与代码归一 ─────────────────────────


class ColumnMapping(BaseModel):
    """账单列 → 源字段的映射（``fields``：源字段名 → 账单**列名**）。

    列名指账单**表头行**里的列标题；引擎自动定位表头行（见 :func:`checked_mapping`）。
    字段名须落在 :data:`MAPPING_FIELDS`，且须含 :data:`REQUIRED_MAPPING_FIELDS`。
    """

    model_config = ConfigDict(frozen=True)

    fields: dict[str, str]

    @model_validator(mode="after")
    def _shape(self) -> "ColumnMapping":
        unknown = sorted(set(self.fields) - set(MAPPING_FIELDS))
        if unknown:
            raise MemoryValidationError(
                f"列映射含未知源字段：{unknown}（合法字段：{list(MAPPING_FIELDS)}）"
            )
        empty = sorted(f for f, name in self.fields.items() if not name.strip())
        if empty:
            raise MemoryValidationError(f"列映射的列名不得为空白：{empty}")
        missing = sorted(f for f in REQUIRED_MAPPING_FIELDS if f not in self.fields)
        if missing:
            raise MemoryValidationError(
                f"列映射缺少必需字段：{missing}（04 §8 账单导入至少要映射证券代码列）"
            )
        return self


class ResolvedMapping(BaseModel):
    """在具体表格上定位后的映射（表头行 + 源字段 → 列号）。"""

    model_config = ConfigDict(frozen=True)

    header_index: int
    """表头行在表格中的 0 基行号。"""
    header: tuple[str, ...]
    """定位到的表头（逐列去空白）。"""
    columns: dict[str, int]
    """源字段 → 0 基列号。"""


def checked_mapping(
    mapping: ColumnMapping | Mapping[str, str], table: StatementTable,
) -> ResolvedMapping:
    """校验映射并在表格上定位表头行（非法 → ``MemoryValidationError``）。

    表头行＝**首个含全部映射列名的行**（兼容券商导出的前导说明行）；找不到即显式报缺，
    不臆测列名、不静默跳过（GWT-1）。
    """
    checked = mapping if isinstance(mapping, ColumnMapping) else _checked_column_mapping(mapping)
    wanted = {field: name.strip() for field, name in checked.fields.items()}
    needed = sorted(set(wanted.values()))
    for index, row in enumerate(table.rows):
        cells = tuple(cell.strip() for cell in row)
        if all(name in cells for name in needed):
            return ResolvedMapping(
                header_index=index,
                header=cells,
                columns={field: cells.index(name) for field, name in wanted.items()},
            )
    raise MemoryValidationError(
        f"未找到含全部映射列名的表头行：{needed}——请核对账单列名（04 §8 账单导入）"
    )


def _checked_column_mapping(raw: Mapping[str, str]) -> ColumnMapping:
    try:
        return ColumnMapping(fields=dict(raw))
    except (ValidationError, TypeError) as exc:
        raise MemoryValidationError(f"列映射非法：{exc}") from exc


def normalize_stock_id(raw: str) -> str | None:
    """把账单里的证券代码归一到 ``stock_id``（``sh.`` / ``sz.``）；未收录 → ``None``。

    接受 ``600000`` / ``sh.600000`` / ``600000.SH`` / ``SH600000`` 等写法（前缀一律
    按代码首位重推，不信任账单自带前缀）。前缀不在沪深（``6``→沪、``0``/``3``→深）
    的一律 ``None``——北交所等未收录代码按 [D-032] 口径过滤，由调用方显式计数。
    """
    text = (raw or "").strip()
    if not text:
        return None
    digits = re.sub(r"\D", "", text)
    if not _DIGITS.match(digits):
        return None
    prefix = _EXCHANGE_BY_LEADING_DIGIT.get(digits[0])
    if prefix is None:
        return None
    candidate = f"{prefix}.{digits}"
    try:
        StockId.of(candidate)
    except ValueError:
        return None
    return candidate


# ───────────────────────── 预览 / 导入产出 ─────────────────────────


class StatementPreview(BaseModel):
    """导入前预览（确认门取材面，§8「展示将写入的清单」）。"""

    model_config = ConfigDict(frozen=True)

    holdings: tuple[str, ...]
    """归一后的持仓 ``stock_id``（升序去重）。"""
    events: tuple[str, ...]
    """将写入的 ``history.event`` 文本（首见序去重）。"""
    unmapped: tuple[str, ...]
    """未能归一的代码（升序去重）——显式计数，不静默丢弃（D-032）。"""
    privacy_level: str
    nodes: tuple[MemoryNode, ...]
    """将要写入的节点（1 个 ``attention`` + 逐事件 1 个 ``history``；空表为空）。"""


class StatementImportRecord(BaseModel):
    """一次账单导入的审计记录（落 ``execution_log`` 分区；只增不改）。"""

    model_config = ConfigDict(frozen=True)

    import_id: str
    """账单内容摘要的确定性摘要（:func:`new_statement_import_id`）。"""
    statement_digest: str
    """账单表格内容摘要（sha256 十六进制）——复核「这条记录对应哪份账单」。"""
    recorded_at: datetime
    created: tuple[str, ...]
    """本次真正入库的节点 id（升序）。"""
    skipped: tuple[str, ...]
    """因内容已存在而跳过的既有节点 id（升序）——幂等路径的可见证据。"""
    holdings: tuple[str, ...]
    unmapped: tuple[str, ...]

    @model_validator(mode="after")
    def _shape(self) -> "StatementImportRecord":
        _require_tz(self.recorded_at, "recorded_at")
        return self


class StatementImportOutcome(BaseModel):
    """一次账单导入的产出（记录 + 入库的节点本体）。"""

    model_config = ConfigDict(frozen=True)

    record: StatementImportRecord
    nodes: tuple[MemoryNode, ...]


def _table_digest(table: StatementTable) -> str:
    body = json.dumps([list(row) for row in table.rows], sort_keys=True,
                      ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def new_statement_import_id(table: StatementTable) -> str:
    """账单内容摘要的**确定性摘要** ID（``stmt_<20 位十六进制>``）。

    业务键＝账单表格内容：同一账单重放得同一 id（同片段导入的 ``new_import_id`` 取向，
    不新增契约 ID 类）。
    """
    return digest_id("stmt", _table_digest(table))


# ───────────────────────── 导入面 ─────────────────────────


class StatementImporter:
    """券商账单的导入面（04 §8；只经 :class:`MemoryGraph` / :class:`MemoryWriter` 写）。

    :param graph: :class:`MemoryGraph`（内容去重判据与落盘）
    :param writer: :class:`MemoryWriter`（写入面）
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    def __init__(
        self,
        graph: MemoryGraph,
        writer: MemoryWriter,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._graph = graph
        self._writer = writer
        self._now = _system_now if now is None else now

    # ───────────────────────── 预览（确认门） ─────────────────────────

    def preview(
        self,
        table: StatementTable,
        mapping: ColumnMapping | Mapping[str, str],
        *,
        confidence: float = DEFAULT_STATED_CONFIDENCE,
        privacy_level: str = "private",
    ) -> StatementPreview:
        """算出将写入的节点清单（**不落盘**；确认门取材面，§8）。"""
        level = _checked_privacy(privacy_level)
        resolved = checked_mapping(mapping, table)
        rows = table.rows[resolved.header_index + 1:]

        holdings: set[str] = set()
        unmapped: set[str] = set()
        events: list[str] = []
        seen_events: set[str] = set()
        for row in rows:
            raw_code = _cell(row, resolved.columns.get("stock_code"))
            code = normalize_stock_id(raw_code)
            if code is not None:
                holdings.add(code)
            elif raw_code.strip():
                unmapped.add(raw_code.strip())
            event = _row_event(row, resolved, code, raw_code)
            if event and event not in seen_events:
                seen_events.add(event)
                events.append(event)

        moment = self._now()
        nodes: list[MemoryNode] = []
        if holdings:
            nodes.append(self._node("attention", moment, confidence, level,
                                    holdings=tuple(sorted(holdings))))
        for event in events:
            nodes.append(self._node("history", moment, confidence, level, event=event))
        return StatementPreview(
            holdings=tuple(sorted(holdings)),
            events=tuple(events),
            unmapped=tuple(sorted(unmapped)),
            privacy_level=level,
            nodes=tuple(nodes),
        )

    # ───────────────────────── 导入 ─────────────────────────

    def import_statement(
        self,
        table: StatementTable,
        mapping: ColumnMapping | Mapping[str, str],
        *,
        confirmed_by: str,
        confidence: float = DEFAULT_STATED_CONFIDENCE,
        privacy_level: str = "private",
    ) -> StatementImportOutcome:
        """导入一份账单（§8；用户确认后调用）。

        节点 ``source=user_stated``、按 §3.2 直写、不带 ``provenance``；内容与既有节点
        相同者**跳过**并报告（幂等，不覆盖）。**有入库**才落审计记录。
        """
        if confirmed_by != STATEMENT_CONFIRMATION:
            raise MemoryValidationError(
                f"导入账单的 confirmed_by 只接受 {STATEMENT_CONFIRMATION!r}，得到 "
                f"{confirmed_by!r}——04 §8 的导入是用户显式动作"
            )
        preview = self.preview(table, mapping, confidence=confidence,
                               privacy_level=privacy_level)
        by_holdings, by_event = self._existing_index()

        created: list[MemoryNode] = []
        skipped: list[str] = []
        for node in preview.nodes:
            existing_id = (by_holdings.get(node.holdings) if node.type == "attention"
                           else by_event.get(node.event))
            if existing_id is not None:
                skipped.append(existing_id)              # 幂等：内容已在，不产生副本
                continue
            created.append(self._writer.add_node(node))

        record = StatementImportRecord(
            import_id=new_statement_import_id(table),
            statement_digest=_table_digest(table),
            recorded_at=self._now(),
            created=tuple(sorted(n.memory_node_id for n in created)),
            skipped=tuple(sorted(skipped)),
            holdings=preview.holdings,
            unmapped=preview.unmapped,
        )
        if created:
            # 留痕只增不改（同 §6 审计取向）：纯跳过（这次内容全在）不重写既有记录。
            self._write(record)
        return StatementImportOutcome(record=record, nodes=tuple(created))

    # ───────────────────────── 审计面 ─────────────────────────

    def records(self) -> tuple[StatementImportRecord, ...]:
        """全部账单导入记录（按 ``import_id`` 升序）。"""
        return tuple(sorted((self._parse(self._graph.store.get("execution_log", p))
                             for p in self._files()), key=lambda r: r.import_id))

    def record_for(self, import_id: str) -> StatementImportRecord:
        """按 ``import_id`` 取一条记录（不存在 → ``MemoryValidationError``）。"""
        for record in self.records():
            if record.import_id == import_id:
                return record
        raise MemoryValidationError(f"无此账单导入记录：{import_id!r}")

    # ───────────────────────── 内部工具 ─────────────────────────

    def _node(self, kind: str, moment: datetime, confidence: float,
              level: str, **fields: Any) -> MemoryNode:
        return checked_node(
            type=kind,
            memory_node_id=new_node_id(),
            confidence=confidence,
            source="user_stated",
            privacy_level=level,
            created_at=moment,
            updated_at=moment,
            **fields,
        )

    def _existing_index(self) -> tuple[dict[Any, str], dict[Any, str]]:
        """既有节点的内容索引：``attention`` 按持仓集、``history`` 按事件文本。"""
        by_holdings: dict[Any, str] = {}
        by_event: dict[Any, str] = {}
        for node in self._graph.nodes():
            if node.type == "attention" and node.holdings:
                by_holdings.setdefault(node.holdings, node.memory_node_id)
            elif node.type == "history" and node.event:
                by_event.setdefault(node.event, node.memory_node_id)
        return by_holdings, by_event

    def _files(self) -> tuple[str, ...]:
        return tuple(p for p in self._graph.store.list_files("execution_log")
                     if p.startswith(STATEMENT_IMPORT_PREFIX) and p.endswith(".json"))

    @staticmethod
    def _path(import_id: str) -> str:
        return f"{STATEMENT_IMPORT_PREFIX}{import_id}.json"

    def _write(self, record: StatementImportRecord) -> None:
        self._graph.store.put("execution_log", self._path(record.import_id),
                              record.model_dump_json().encode("utf-8"))

    @staticmethod
    def _parse(raw: bytes) -> StatementImportRecord:
        try:
            return StatementImportRecord.model_validate_json(raw)
        except (ValueError, UnicodeDecodeError, ValidationError) as exc:
            raise MemoryValidationError(f"账单导入记录损坏无法解析：{exc}") from exc


def _cell(row: tuple[str, ...], index: int | None) -> str:
    """取一行的某列（列号缺省 / 越界 → 空串，不报错——券商行常参差不齐）。"""
    if index is None or index >= len(row):
        return ""
    return row[index]


def _row_event(row: tuple[str, ...], resolved: ResolvedMapping,
               code: str | None, raw_code: str) -> str | None:
    """一行 → ``history.event`` 文本。

    有 ``event`` 列且非空 → 直接取用；否则由已映射的日期 / 方向 / 代码 / 数量 / 价格
    **合成**（代码优先用归一后的 ``stock_id``）。都取不到 → ``None``（该行不产事件）。
    """
    columns = resolved.columns
    free = _cell(row, columns.get("event")).strip()
    if free:
        return free
    parts: list[str] = []
    for field in ("date", "direction"):
        value = _cell(row, columns.get(field)).strip()
        if value:
            parts.append(value)
    code_text = code or raw_code.strip()
    if code_text:
        parts.append(code_text)
    for field in ("quantity", "price"):
        value = _cell(row, columns.get(field)).strip()
        if value:
            parts.append(value)
    return " ".join(parts) if parts else None


def _checked_privacy(level: str) -> str:
    """校验隐私分级（账单导入**不得**标 ``public``，见模块文档 / 假设 A1）。"""
    if level not in STATEMENT_PRIVACY_LEVELS:
        raise MemoryValidationError(
            f"账单导入的 privacy_level 只接受 {list(STATEMENT_PRIVACY_LEVELS)}，"
            f"得到 {level!r}——持仓 / 成交属个人面，不得标 public（09 §2 导出过滤面另管公开）"
        )
    return level
