"""信息面同步引擎（T-L0-010.2–.5 的执行面）。

与 ``BaoStockSync`` **同一形状**（setup → run_task → 水位 → 校验），但：

- 抓取走 :class:`InfoFetcher`（真实 HTTP / 测试 Fake），**一律经 L0 出网网关**
- **写多表**：一条任务可写该域的多张表（龙虎榜的上榜记录 + 席位明细）
- **主备让位**：``role=primary`` 走 ``INSERT OR REPLACE``、``backup`` 走
  ``INSERT OR IGNORE``——故备胎只补空缺，不会覆盖主源口径（D-030 的业务键级
  单一事实源）
- **主档预过滤**：未收录代码过滤 + 计数（D-032），过滤命中记
  ``sync_state.last_status='partial'``，**批次不失败**
- **文本两段**：公告 / 问答的正文经 :class:`DocStore` 落文件，表行存
  ``file_path``（D-031）
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from st_agent.contracts.result_envelope import ResultEnvelope

from st_agent.l0.info.docs import DocConsistency, DocStore, audit_docs
from st_agent.l0.info.errors import (
    InfoFetchError,
    InfoFetchUnavailableError,
    InfoValidationError,
)
from st_agent.l0.info.prefilter import master_codes
from st_agent.l0.info.sources import (
    INFO_SOURCES,
    INFO_TASKS,
    InfoTask,
    get_info_task,
    hosts_of,
    market_of,
    to_stock_id,
    window_for,
)

__all__ = [
    "ARCHIVAL_DOMAINS",
    "InfoSync",
    "digest_id",
    "DOMAIN_INFO_TASKS",
]

ARCHIVAL_DOMAINS: tuple[str, ...] = ("announcement",)
"""**档案型**域：记录一旦错过、且源端随后失效，即**不可重取**（见 05「不可逆缺口」）。

与「可换源重取」的行情类域不同——故其空窗须能被判定为**可能永久**的缺口，
而非「等下次同步就好」。
"""

_ID_PREFIX = {"announcement": "ann", "sentiment_qa": "qa"}

DOMAIN_INFO_TASKS: dict[str, tuple[str, ...]] = {}
for _t in INFO_TASKS:
    DOMAIN_INFO_TASKS.setdefault(_t.domain, ())
    DOMAIN_INFO_TASKS[_t.domain] = DOMAIN_INFO_TASKS[_t.domain] + (_t.task_key,)


def digest_id(prefix: str, *parts: Any) -> str:
    """业务键的**确定性摘要**标识（01 §1：跨源的同一实体得同一 ID，长度有界）。

    形如 ``ann_1f3c...``（前缀 + 20 位十六进制，恒 24 字符）——远低于
    ``EvidenceRef.ref`` 的 128 上限，且**不沿用源方 ID**（D-030 / D-031）。
    """
    import hashlib

    raw = "\u0001".join(str(p or "").strip() for p in parts)
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"


def _now() -> datetime:
    return datetime.now().astimezone()


class InfoSync:
    """信息面同步引擎（只经 ``MarketDb`` 与出网网关读写）。

    :param db: 市场数据库门面（四域表建在同一 ``market.db``）
    :param gateway: 出网审计网关（02 §6 唯一出口）
    :param fetcher: :class:`InfoFetcher` 实现（测试注入离线 Fake）
    """

    def __init__(self, db, gateway, fetcher, *,
                 initiator: str = "info-sync") -> None:
        self._db = db
        self._gateway = gateway
        self._fetcher = fetcher
        self._initiator = initiator

    # ───────────────────────── 注册与种子 ─────────────────────────

    def setup(self) -> ResultEnvelope:
        """登记信息面数据源与同步任务（幂等；不改动 BaoStock 侧的登记）。"""
        if not self._db.exists():
            now = _now()
            return ResultEnvelope.unavailable(
                "本地市场数据库尚未建立（先执行 BaoStock 侧 setup 建库）",
                last_updated_at=now, as_of=now,
            )
        with self._db.transact() as con:
            for source in INFO_SOURCES:
                con.execute(
                    "INSERT OR IGNORE INTO data_source(source_id, name, manual_ref)"
                    " VALUES (?, ?, ?)",
                    (source.source_id, source.name, source.manual_ref),
                )
            for spec in INFO_TASKS:
                con.execute(
                    "INSERT OR IGNORE INTO sync_state(task_key, table_name, source_id,"
                    " mode, schedule_desc) VALUES (?, ?, ?, ?, ?)",
                    (spec.task_key, spec.table_name, spec.source_id,
                     spec.mode, spec.schedule_desc),
                )
            con.commit()
        return ResultEnvelope.ok(
            {"sources": len(INFO_SOURCES), "tasks": len(INFO_TASKS)},
            as_of=_now(),
        )

    # ───────────────────────── 单任务执行 ─────────────────────────

    def run_task(self, task_key: str, *,
                 enabled: dict[str, bool] | None = None,
                 day: str | None = None,
                 window: tuple[str, str] | None = None,
                 timeout_ms: int = 120_000,
                 cancel=None) -> ResultEnvelope:
        """执行单个信息面任务（前置 → 窗口 → 抓取 → 预过滤 → 入表 → 水位 → 校验）。"""
        try:
            spec = get_info_task(task_key)
        except InfoValidationError as exc:
            return ResultEnvelope.validation_failed(str(exc))
        if enabled is not None and task_key in enabled and not enabled[task_key]:
            return self._disabled_envelope(task_key)
        if not self._db.exists():
            now = _now()
            return ResultEnvelope.unavailable(
                "本地市场数据库尚未建立（先执行 BaoStock 侧 setup 建库）",
                last_updated_at=now, as_of=now,
            )
        known = master_codes(self._db)
        if known is None:
            now = _now()
            return ResultEnvelope.unavailable(
                "security 主档不可用，无法判定收录范围（拒绝以空集过滤全部行）",
                last_updated_at=self._db.last_updated_at() or now, as_of=now,
            )
        with self._db.transact() as con:
            watermark = self._watermark(con, task_key)
        window = window or window_for(task_key, watermark, day=day)
        try:
            fetched = self._via_gateway(task_key, spec, window,
                                        timeout_ms=timeout_ms, cancel=cancel)
        except InfoFetchUnavailableError as exc:
            self._mark(task_key, status="failed",
                       error=str(exc) or "源不可达", row_count=0, watermark=watermark)
            stamp = self._db.last_updated_at() or _now()
            return ResultEnvelope.unavailable(
                str(exc) or "信息面数据源不可达", last_updated_at=stamp, as_of=stamp,
            )
        except (InfoFetchError, Exception) as exc:  # noqa: BLE001
            log_ref = self._mark(task_key, status="failed",
                                 error=f"抓取失败：{exc}", row_count=0,
                                 watermark=watermark)
            return ResultEnvelope.failed(
                f"信息面任务 {task_key!r} 抓取失败：{exc}", log_ref=log_ref,
            )
        loaded, new_watermark, unmapped = self._load(spec, fetched, known)
        status = "ok" if unmapped.is_clean else "partial"
        self._mark(task_key, status=status, error=unmapped.describe(),
                   row_count=loaded, watermark=new_watermark)
        stamp = self._db.last_updated_at() or _now()
        payload = {
            "task": task_key, "domain": spec.domain, "rows": loaded,
            "watermark": new_watermark, "coverage": spec.coverage,
            "source": spec.source_id, "role": spec.role,
            "filtered_unmapped": {
                "rows_filtered": unmapped.rows_filtered,
                "codes_distinct": unmapped.codes_distinct,
                "sample": list(unmapped.sample),
            },
        }
        if loaded == 0:
            # 已知空集 ≠ 数据缺失：过滤命中时把原因说清，而非报「查到 0 条」（D-032）
            reason = f"源端在窗口 {window[0]}~{window[1]} 内无该域记录"
            if not unmapped.is_clean:
                reason = f"{reason}；且主档外代码已过滤——{unmapped.describe()}"
            return ResultEnvelope.empty(reason, as_of=stamp)
        return ResultEnvelope.ok(payload, as_of=stamp)

    def run_all(self, *, enabled: dict[str, bool] | None = None,
                day: str | None = None,
                timeout_ms: int = 120_000) -> dict[str, ResultEnvelope]:
        """按注册序执行全部信息面任务（禁用者跳过记 ``unavailable``）。"""
        results: dict[str, ResultEnvelope] = {}
        setup_env = self.setup()
        if setup_env.status != "ok":
            return {t: setup_env for t in DOMAIN_INFO_TASKS}
        for spec in INFO_TASKS:
            results[spec.task_key] = self.run_task(
                spec.task_key, enabled=enabled, day=day, timeout_ms=timeout_ms,
            )
        return results

    # ───────────────────────── 新鲜度与文本审计 ─────────────────────────

    def freshness_verdict(self, domain: str):
        """按信息面域判定新鲜度（任一相关任务非 ok → ``stale``）。"""
        from st_agent.l0.market.sync import StalenessVerdict

        if domain not in DOMAIN_INFO_TASKS:
            raise InfoValidationError(
                f"未知信息面域 {domain!r}；合法域 = {sorted(DOMAIN_INFO_TASKS)}"
            )
        keys = DOMAIN_INFO_TASKS[domain]
        stamp = self._db.last_updated_at() or _now()
        bad = [r for r in self._db.freshness() if r.get("task_key") in keys]
        if bad:
            detail = "数据面不完整：" + "; ".join(
                f"{r.get('task_key')}({r.get('last_status')})" for r in bad)
            return StalenessVerdict(stale=True, last_updated_at=stamp, detail=detail)
        return StalenessVerdict(stale=False, last_updated_at=stamp,
                               detail=f"{domain} 数据面完整")

    def archival_gaps(self, domain: str) -> tuple[dict[str, Any], ...]:
        """档案型域的**不可回补空窗**（05「不可逆缺口」的可判定形式）。

        与「可换源重取」的行情类域不同：公告一旦错过、且源端随后失效，即
        **永久缺口**（[02 §8.1] 已就此要求备份 UI 提示）。故此处不仅报「陈旧」，
        还逐条标注 ``recoverable=False``——消费面据此区分「等下次同步」与
        「这块历史再也拿不回来」。非档案型域返回空元组。
        """
        if domain not in ARCHIVAL_DOMAINS:
            return ()
        keys = DOMAIN_INFO_TASKS.get(domain, ())
        return tuple({**row, "recoverable": False, "archival": True}
                     for row in self._db.freshness()
                     if row.get("task_key") in keys)

    def audit_docs(self, domain: str | None = None) -> DocConsistency:
        """核对文本两段一致性（表在而文件缺 → 损坏；文件在而表缺 → 孤儿）。"""
        referenced: list[str] = []
        for table, column in (("announcement", "file_path"),
                              ("sentiment_qa", "file_path")):
            env = self._db.query(
                f"SELECT {column} AS p FROM {table} WHERE {column} IS NOT NULL"
            )
            if env.status == "ok":
                referenced.extend(r["p"] for r in env.data["rows"])
        return audit_docs(self._db.store, referenced, domain)

    # ───────────────────────── 内部：抓取闭包 ─────────────────────────

    def _via_gateway(self, task_key: str, spec: InfoTask,
                     window: tuple[str, str], *, timeout_ms: int,
                     cancel) -> dict[str, tuple[dict, ...]]:
        """经网关执行一次抓取（审计只记字节数；内容不进网关）。"""
        holder: dict[str, Any] = {}

        def _sender(kind, host, timeout):
            _ = (kind, host, timeout)
            try:
                rows = self._fetcher.fetch_task(task_key, window)
            except InfoFetchUnavailableError as exc:
                from st_agent.l0.net.errors import EgressUnavailableError
                raise EgressUnavailableError(str(exc) or "源不可达") from exc
            except Exception as exc:  # noqa: BLE001
                from st_agent.l0.net.errors import EgressError
                raise EgressError(f"抓取失败：{exc}") from exc
            blob = sum(len(str(v)) for group in rows.values() for row in group
                       for v in row.values())
            holder["rows"] = rows
            return (0, blob, ())

        env = self._gateway.execute(
            "data_fetch", hosts_of(spec.source_id)[0],
            initiator=self._initiator,
            purpose=f"信息面同步（{spec.domain} · {spec.source_id}）",
            timeout_ms=timeout_ms, cancel=cancel, sender=_sender,
        )
        if env.status == "ok":
            return holder["rows"]
        if env.status == "unavailable":
            raise InfoFetchUnavailableError(env.reason or "源不可达")
        raise InfoFetchError(f"{env.reason}（见 {env.log_ref}）")

    # ───────────────────────── 内部：入表 ─────────────────────────

    def _load(self, spec: InfoTask, fetched: dict[str, tuple[dict, ...]],
              known: frozenset[str]):
        """按域把抓取结果规范化、预过滤、入表；返回 ``(行数, 新水位, 计数)``。"""
        loader = getattr(self, f"_load_{spec.domain}")
        return loader(spec, fetched, known)

    @staticmethod
    def _role_clause(role: str) -> str:
        """主源可覆盖、备胎让位（D-030：同一业务键只由一条源产生）。"""
        return "INSERT OR IGNORE" if role == "backup" else "INSERT OR REPLACE"

    def _insert(self, con: sqlite3.Connection, table: str, columns: tuple[str, ...],
                rows: list[dict], *, role: str) -> int:
        if not rows:
            return 0
        placeholders = ", ".join("?" for _ in columns)
        sql = (f"{self._role_clause(role)} INTO {table}"
               f"({', '.join(columns)}) VALUES ({placeholders})")
        con.executemany(sql, [tuple(r.get(c) for c in columns) for r in rows])
        return len(rows)

    def _load_announcement(self, spec: InfoTask, fetched, known):
        from st_agent.l0.info.prefilter import filter_unmapped

        docs = DocStore(self._db.store)
        rows, unmapped = filter_unmapped(
            self._with_stock_id(fetched.get("announcement", ())),
            lambda r: r["code"], known)
        prepared: list[dict] = []
        with self._db.transact() as con:
            for row in rows:
                ann_id = digest_id("ann", row["code"], row["title"], row["pub_date"])
                path = None
                if row.get("text"):
                    path = docs.write("announcement", row["code"],
                                      row["pub_date"], ann_id, row["text"])
                prepared.append({
                    "announcement_id": ann_id, "code": row["code"],
                    "title": row["title"], "ann_type": row.get("ann_type"),
                    "pub_date": row["pub_date"], "url": row.get("url"),
                    "file_path": path, "coverage": spec.coverage,
                    "source_id": spec.source_id,
                })
            loaded = self._insert(
                con, "announcement",
                ("announcement_id", "code", "title", "ann_type", "pub_date",
                 "url", "file_path", "coverage", "source_id"),
                prepared, role=spec.role)
            con.commit()
        return loaded, self._max_of(prepared, "pub_date"), unmapped

    @staticmethod
    def _with_stock_id(rows) -> list[dict]:
        """把行里的 ``code`` 归一为全库统一形态（``to_stock_id`` 对已带前缀者幂等）。

        **归一在引擎侧统一做**——抓取器只需产出源方原始代码即可，不必各自
        重复实现号段规则（多源扩展规约 §1 的收敛点只此一处）。
        """
        out: list[dict] = []
        for row in rows or ():
            code = row.get("code")
            if not code:
                continue
            out.append({**row, "code": to_stock_id(code, row.get("market"))})
        return out

    def _load_dragon_tiger(self, spec: InfoTask, fetched, known):
        from st_agent.l0.info.prefilter import filter_unmapped

        tops = self._with_stock_id(fetched.get("dragon_tiger", ()))
        seat = self._with_stock_id(fetched.get("dragon_tiger_seat", ()))
        tops, unmapped_a = filter_unmapped(tops, lambda r: r["code"], known)
        seat, unmapped_b = filter_unmapped(seat, lambda r: r["code"], known)
        unmapped = _merge_counts(unmapped_a, unmapped_b)
        tops = _merge_reasons(tops)
        with self._db.transact() as con:
            loaded = self._insert(
                con, "dragon_tiger",
                ("code", "trade_date", "reasons", "net_amount", "buy_amount",
                 "sell_amount", "turnover", "source_id"),
                [{**r, "source_id": spec.source_id} for r in tops],
                role=spec.role,
            )
            loaded += self._insert(
                con, "dragon_tiger_seat",
                ("code", "trade_date", "side", "rank", "seat_name",
                 "buy_amount", "sell_amount", "net_amount", "source_id"),
                [{**r, "source_id": spec.source_id} for r in seat],
                role=spec.role,
            )
            con.commit()
        return loaded, self._max_of(tops, "trade_date"), unmapped

    def _load_shareholder_num(self, spec: InfoTask, fetched, known):
        from st_agent.l0.info.prefilter import filter_unmapped

        rows, unmapped = filter_unmapped(
            self._with_stock_id(fetched.get("shareholder_num", ())),
            lambda r: r["code"], known)
        with self._db.transact() as con:
            loaded = self._insert(
                con, "shareholder_num",
                ("code", "stat_date", "holder_num", "change_num",
                 "change_ratio", "avg_shares", "source_id"),
                [{**r, "source_id": spec.source_id} for r in rows],
                role=spec.role,
            )
            con.commit()
        return loaded, self._max_of(rows, "stat_date"), unmapped

    def _load_sentiment_qa(self, spec: InfoTask, fetched, known):
        from st_agent.l0.info.prefilter import filter_unmapped

        docs = DocStore(self._db.store)
        rows, unmapped = filter_unmapped(
            self._with_stock_id(fetched.get("sentiment_qa", ())),
            lambda r: r["code"], known)
        prepared = []
        with self._db.transact() as con:
            for row in rows:
                qa_id = digest_id("qa", row["code"], row["question"], row["ask_time"])
                body = f"Q: {row['question']}\nA: {row.get('answer') or '（尚未回复）'}"
                path = docs.write("sentiment_qa", row["code"],
                                  (row["ask_time"] or "")[:10], qa_id, body)
                prepared.append({
                    "qa_id": qa_id, "code": row["code"],
                    "market": market_of(row["code"]), "question": row["question"],
                    "answer": row.get("answer"), "answerer": row.get("answerer"),
                    "ask_time": row["ask_time"], "file_path": path,
                    "source_id": spec.source_id,
                })
            loaded = self._insert(
                con, "sentiment_qa",
                ("qa_id", "code", "market", "question", "answer", "answerer",
                 "ask_time", "file_path", "source_id"),
                prepared, role=spec.role,
            )
            con.commit()
        return loaded, self._max_of(prepared, "ask_time"), unmapped

    def _load_sentiment_hot(self, spec: InfoTask, fetched, known):
        from st_agent.l0.info.prefilter import filter_unmapped

        rows, unmapped = filter_unmapped(
            self._with_stock_id(fetched.get("sentiment_hot", ())),
            lambda r: r["code"], known)
        # 快照时刻取**微秒**精度——snapshot 模式的语义是「每次追加新快照、历史
        # 不动」（05 同步模式表）；秒级精度会让同一秒内的两次扫描坍缩为一行。
        stamp = _now().isoformat()
        with self._db.transact() as con:
            loaded = self._insert(
                con, "sentiment_hot",
                ("code", "board", "snapshot_at", "rank", "heat", "source_id"),
                [{**r, "snapshot_at": stamp, "source_id": spec.source_id}
                 for r in rows],
                role=spec.role,
            )
            con.commit()
        return loaded, None, unmapped

    # ───────────────────────── 内部：状态 ─────────────────────────

    @staticmethod
    def _max_of(rows: list[dict], column: str) -> str | None:
        values = [str(r[column]) for r in rows
                  if r.get(column) is not None]
        return max(values) if values else None

    @staticmethod
    def _watermark(con: sqlite3.Connection, task_key: str) -> str | None:
        row = con.execute(
            "SELECT watermark FROM sync_state WHERE task_key=?", (task_key,)
        ).fetchone()
        return row[0] if row else None

    def _mark(self, task_key: str, status: str, error: str,
              row_count: int, watermark: str | None) -> str:
        with self._db.transact() as con:
            con.execute(
                "UPDATE sync_state SET watermark=?, last_success_at=?, "
                "last_row_count=?, last_status=?, last_error=? WHERE task_key=?",
                (watermark, _now().isoformat(), row_count, status, error,
                 task_key),
            )
            con.commit()
        return f"data_cache/{task_key}"

    def _disabled_envelope(self, task_key: str) -> ResultEnvelope:
        stamp = (self._db.last_updated_at() if self._db.exists() else None) or _now()
        return ResultEnvelope.unavailable(
            f"信息面任务 {task_key!r} 已禁用（数据源开关关闭），数据停留于标注时间",
            last_updated_at=stamp, as_of=stamp,
        )


def _merge_reasons(rows: list[dict]) -> list[dict]:
    """同 ``(code, trade_date)`` 的多原因行合并为一行（业务键口径，D-030）。

    抓取器可能给出同标的同日的多条上榜记录（各带一个原因字符串）；合并后
    每标的每日**只有一行**，故跨源去重不依赖原因串的措辞。
    """
    merged: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row["code"], row["trade_date"])
        current = merged.get(key)
        if current is None:
            merged[key] = {**row}
            continue
        reason = row.get("reasons")
        if reason and reason not in (current.get("reasons") or ""):
            current["reasons"] = (f"{current['reasons']}；{reason}"
                                  if current.get("reasons") else reason)
        for column in ("net_amount", "buy_amount", "sell_amount", "turnover"):
            if current.get(column) is None and row.get(column) is not None:
                current[column] = row[column]
    return list(merged.values())


def _merge_counts(a, b):
    """合并两次过滤计数（同一任务写多表时各表各自过滤）。"""
    from st_agent.l0.info.prefilter import UnmappedCount

    sample = tuple(dict.fromkeys(a.sample + b.sample))[:20]
    return UnmappedCount(a.rows_filtered + b.rows_filtered,
                         a.codes_distinct + b.codes_distinct, sample)
