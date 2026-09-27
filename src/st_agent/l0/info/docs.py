"""文本类信息的承载基件与文档取数面（T-L0-010.1 ②③；口径见 D-031）。

**一条记录 = 表行（元数据 / 可查询索引）＋文件（正文）**，两段**同源同现**。
正文落 ``data_cache`` 分区的文本目录、经 ``Store`` 加密落盘（逐文件 digest +
分区级校验）——`Store.put(partition, name, bytes)` 本就是「分区 + 分区内相对
路径 → bytes」的通用 KV，故**存储层零改动**。

- **路径由标识确定性派生**：``<TEXT_ROOT>/<域>/<代码>/<日期>/<标识>.txt``。
  证据引用只持标识即可（``EvidenceRef.ref`` 上限 128 因此天然满足，D-031），
  路径本身不作证据引用目标。
- **一致性判据**（不静默）：**表在而文件缺 → 损坏**（走分区恢复）；
  **文件在而表缺 → 孤儿**（可回收）。
- **Skill 不直读文件**：一律经 :class:`DocSource` 消费。读 ``data_cache``
  内的缓存数据属**数据访问**，不在 ``local_read:<path-scope>`` 权限范围内
  （该权限针对**缓存外**文件，01 §10）——若把缓存读也纳入，每个 Skill 都要
  申请一遍，与「``data_cache`` 消费方＝全部 Skill」的既有定位冲突。
"""

from __future__ import annotations

from typing import Iterable, NamedTuple, Protocol

from st_agent.contracts.result_envelope import ResultEnvelope

from st_agent.l0.info.errors import InfoValidationError

__all__ = [
    "DOC_TEXT_DOMAINS",
    "DocConsistency",
    "DocSource",
    "LocalDocSource",
    "TEXT_ROOT",
    "audit_docs",
    "text_path",
]

TEXT_ROOT = "info"
"""``data_cache`` 分区内的文本根目录（与 ``market.db`` 平级，同分区同加密）。"""

_DOC_SUFFIX = ".txt"

DOC_TEXT_DOMAINS: dict[str, tuple[str, str, str]] = {
    # 域 → (元数据表, 标识列, 日期列)
    "announcement": ("announcement", "announcement_id", "pub_date"),
    "sentiment_qa": ("sentiment_qa", "qa_id", "ask_time"),
}
"""带正文文件的域（其余域为纯表格，无文本两段问题）。"""


def text_path(domain: str, code: str, day: str, doc_id: str) -> str:
    """文件路径（分区内相对路径）：``<TEXT_ROOT>/<域>/<代码>/<日期>/<标识>.txt``。

    由标识**确定性派生**——同标识恒同路径，换标识换路径，可复核。
    非法参数（未知域 / 空值）→ ``InfoValidationError``。
    """
    if domain not in DOC_TEXT_DOMAINS:
        raise InfoValidationError(
            f"未知文本域 {domain!r}；合法域 = {sorted(DOC_TEXT_DOMAINS)}"
        )
    for label, value in (("代码", code), ("日期", day), ("标识", doc_id)):
        if not value or not str(value).strip():
            raise InfoValidationError(f"文本路径缺少{label}（域 {domain!r}）")
    if "/" in str(doc_id) or "\\" in str(doc_id):
        raise InfoValidationError(f"标识不得含路径分隔符：{doc_id!r}")
    return f"{TEXT_ROOT}/{domain}/{code}/{day}/{doc_id}{_DOC_SUFFIX}"


class DocConsistency(NamedTuple):
    """文本两段的一致性核对结论（两种不一致都不静默）。"""

    missing_file: tuple[str, ...]
    """**表在而文件缺** → 损坏（该记录不可读，走分区恢复口径）。"""

    orphan_file: tuple[str, ...]
    """**文件在而表缺** → 孤儿（可回收的残留）。"""

    @property
    def is_consistent(self) -> bool:
        return not self.missing_file and not self.orphan_file


class DocStore:
    """文本正文的读写（薄封装 ``Store``；不做业务语义判定）。"""

    def __init__(self, store) -> None:
        self._store = store

    def write(self, domain: str, code: str, day: str, doc_id: str,
              text: str) -> str:
        """写入正文并返回其路径（同路径重复写 = 覆盖，幂等）。"""
        if not isinstance(text, str):
            raise InfoValidationError("正文须为字符串")
        path = text_path(domain, code, day, doc_id)
        self._store.put("data_cache", path, text.encode("utf-8"))
        return path

    def read(self, path: str) -> str:
        """读正文（路径不存在 → ``InfoValidationError``；调用方先 :meth:`exists`）。"""
        if not self.exists(path):
            raise InfoValidationError(f"文本文件不存在：{path!r}")
        return self._store.get("data_cache", path).decode("utf-8")

    def exists(self, path: str) -> bool:
        return path in self._store.list_files("data_cache")

    def delete(self, path: str) -> None:
        """回收孤儿文件（仅用于审计结论中的孤儿项）。"""
        self._store.delete("data_cache", path)

    def list_paths(self, domain: str | None = None) -> tuple[str, ...]:
        """列文本路径（可按域过滤；按域即路径前缀）。"""
        prefix = f"{TEXT_ROOT}/{domain}/" if domain else f"{TEXT_ROOT}/"
        return tuple(sorted(
            name for name in self._store.list_files("data_cache")
            if name.startswith(prefix) and name.endswith(_DOC_SUFFIX)
        ))


def audit_docs(store, referenced: Iterable[str],
               domain: str | None = None) -> DocConsistency:
    """核对「表行引用的路径」与「盘上文件」两段是否一致。

    :param referenced: 元数据表里**非空**的 ``file_path`` 集合
    :param domain: 只核对该域（``None`` = 全部文本域）
    """
    docs = DocStore(store)
    referenced_set = {p for p in referenced if p}
    on_disk = set(docs.list_paths(domain))
    return DocConsistency(
        missing_file=tuple(sorted(referenced_set - on_disk)),
        orphan_file=tuple(sorted(on_disk - referenced_set)),
    )


class DocSource(Protocol):
    """文档取数面协议（鸭子类型；与 ``MarketDb.query`` 同构，**不 import L0 具体类**）。

    L1 侧经**注入**消费本协议即可，不必知道 ``Store`` / ``MarketDb`` 的存在。
    """

    def list_docs(self, *, domain: str | None = None, code: str | None = None,
                  limit: int = 50) -> ResultEnvelope: ...

    def read_doc(self, doc_id: str) -> ResultEnvelope: ...

    def search_docs(self, query: str, *, domain: str | None = None,
                    limit: int = 50) -> ResultEnvelope: ...


class LocalDocSource:
    """本地实现：元数据经 ``MarketDb.query`` 读，正文经 :class:`DocStore` 读。

    :param db: ``MarketDb`` 门面（鸭子类型：只要求 ``query`` / ``last_updated_at``）
    :param store: ``Store`` 句柄（正文出入口）
    """

    def __init__(self, db, store) -> None:
        self._db = db
        self._docs = DocStore(store)

    # ───────────────────────── 取数面三件 ─────────────────────────

    def list_docs(self, *, domain: str | None = None, code: str | None = None,
                  limit: int = 50) -> ResultEnvelope:
        """列文档元数据（不含正文；正文经 :meth:`read_doc` 单取）。"""
        domains = self._resolve_domains(domain)
        rows: list[dict] = []
        for name in domains:
            table, id_col, date_col = DOC_TEXT_DOMAINS[name]
            sql = (f"SELECT {id_col} AS doc_id, code, {date_col} AS doc_date, "
                   f"file_path, source_id FROM {table}")
            clauses, params = [], []
            if code is not None:
                clauses.append("code = ?")
                params.append(code)
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += f" ORDER BY {date_col} DESC LIMIT ?"
            env = self._db.query(sql, (*params, limit))
            if env.status == "ok":
                rows.extend({**r, "domain": name} for r in env.data["rows"])
            elif env.status == "unavailable":
                return env
        if not rows:
            return ResultEnvelope.empty(
                "无匹配文档（列表合法但为空）", as_of=self._as_of(),
            )
        return ResultEnvelope.ok(
            {"columns": ["domain", "doc_id", "code", "doc_date", "file_path",
                         "source_id"],
             "rows": rows[:limit]},
            as_of=self._as_of(),
        )

    def read_doc(self, doc_id: str) -> ResultEnvelope:
        """按标识读正文。

        三种结局各自显式：未知标识 → ``empty``；**表在而文件缺** → ``unavailable``
        （损坏，走分区恢复口径，**不**返回空串冒充）；正常 → ``ok``。
        """
        if not doc_id or not str(doc_id).strip():
            return ResultEnvelope.validation_failed("文档标识不可为空")
        located = self._locate(doc_id)
        if located is None:
            return ResultEnvelope.empty(
                f"未知文档标识 {doc_id!r}（元数据表中无此记录）", as_of=self._as_of(),
            )
        domain, path = located
        if not path or not self._docs.exists(path):
            return ResultEnvelope.unavailable(
                f"文档 {doc_id!r} 的正文文件缺失（表在而文件缺，判为损坏）"
                f"；文档域 {domain!r}",
                last_updated_at=self._stamp(), as_of=self._as_of(),
            )
        return ResultEnvelope.ok(
            {"doc_id": doc_id, "domain": domain, "file_path": path,
             "text": self._docs.read(path)},
            as_of=self._as_of(),
        )

    def search_docs(self, query: str, *, domain: str | None = None,
                    limit: int = 50) -> ResultEnvelope:
        """按关键词检索元数据文本列（表即索引；全文/语义检索属 RAG 侧，见 D-031 ④）。"""
        if not query or not str(query).strip():
            return ResultEnvelope.validation_failed("检索关键词不可为空")
        patterns = {"announcement": ["title"],
                    "sentiment_qa": ["question", "answer"]}
        rows: list[dict] = []
        for name in self._resolve_domains(domain):
            table, id_col, date_col = DOC_TEXT_DOMAINS[name]
            columns = patterns[name]
            where = " OR ".join(f"{col} LIKE ?" for col in columns)
            sql = (f"SELECT {id_col} AS doc_id, code, {date_col} AS doc_date, "
                   f"file_path, source_id FROM {table} WHERE {where} "
                   f"ORDER BY {date_col} DESC LIMIT ?")
            params = tuple(f"%{query}%" for _ in columns) + (limit,)
            env = self._db.query(sql, params)
            if env.status == "ok":
                rows.extend({**r, "domain": name} for r in env.data["rows"])
            elif env.status == "unavailable":
                return env
        if not rows:
            return ResultEnvelope.empty(
                f"关键词 {query!r} 无匹配文档", as_of=self._as_of(),
            )
        return ResultEnvelope.ok(
            {"columns": ["domain", "doc_id", "code", "doc_date", "file_path",
                         "source_id"],
             "rows": rows[:limit]},
            as_of=self._as_of(),
        )

    # ───────────────────────── 内部 ─────────────────────────

    @staticmethod
    def _resolve_domains(domain: str | None) -> tuple[str, ...]:
        if domain is None:
            return tuple(DOC_TEXT_DOMAINS)
        if domain not in DOC_TEXT_DOMAINS:
            raise InfoValidationError(
                f"未知文本域 {domain!r}；合法域 = {sorted(DOC_TEXT_DOMAINS)}"
            )
        return (domain,)

    def _locate(self, doc_id: str) -> tuple[str, str | None] | None:
        """标识 → (域, 路径)；未登记 → None（路径可为 None = 该行未落正文）。"""
        for name, (table, id_col, _) in DOC_TEXT_DOMAINS.items():
            env = self._db.query(
                f"SELECT file_path FROM {table} WHERE {id_col} = ?", (doc_id,)
            )
            if env.status == "ok":
                return name, env.data["rows"][0]["file_path"]
        return None

    def _stamp(self):
        stamp = self._db.last_updated_at() if self._db.exists() else None
        return stamp or _now()

    def _as_of(self):
        return self._db.last_updated_at() if self._db.exists() else _now()


def _now():
    from datetime import datetime
    return datetime.now().astimezone()
