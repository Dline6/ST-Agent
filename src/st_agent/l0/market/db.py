"""本地市场数据库门面 ``MarketDb``（02 §5 数据源缓存 + 数据库设计族）。

布局（只经 ``Store`` 读写，不直连文件系统）：
- SQLite 单库 → ``data_cache`` 分区 ``market.db``（整文件加密落盘；
  断网可查历史 = 读本地加密分区，GWT-1）
- DDL 真相源 = 同包 ``schema.sql``（与 ``docs/数据库设计-BaoStock数据层/``
  的 ``schema.sql`` 同内容；包内副本供运行时建库，文档副本供人阅读。
  两份不一致时以文档族为准并同步本副本）

读写语义（SQLite 单文件作为 opaque blob 经 ``Store`` 存取）：
- 读/写均走「取 blob → 落工作拷贝 → sqlite3 操作 → 读回 blob → ``put``」
  闭环；写回后清单校验和同步更新（``Store.put`` 语义）
- 工作拷贝落在 ``tempfile.TemporaryDirectory``（进程退出即清，不入库；
  ``tmp/`` 在 ``.gitignore``）
- 外键约束每次连接显式 ``PRAGMA foreign_keys = ON``（SQLite 默认关闭）

查询面（上层 Skill / 视角 / 报告的统一入口）：
- ``connect``：只读工作连接（调用方自行 ``close``；写操作走 ``sync_*``）
- ``query``：只读 SQL 白名单（``SELECT`` / ``WITH`` / ``PRAGMA`` /
  ``EXPLAIN`` 开头，禁 ``;`` 多语句）→ ``ResultEnvelope``（空结果走
  ``empty``，库缺失/表缺失走 ``unavailable`` 标注最后更新）
- ``freshness``：05 文档统一自检 SQL（``sync_state`` 非 ok 行）
- ``as_of``：按数据域取实际截止时间（行情域 ``max(trade_date)`` 等）
- ``snapshot_id`` / ``snapshot_components``：读取时各任务
  ``(task_key, watermark, last_success_at)`` 组合（02 §5「不另行维护快照
  登记表」）——前者为其**确定性摘要**（01 §1 ``dataset_snapshot_id`` 形态），
  后者是可读原文（摘要输入）
"""

from __future__ import annotations

import re
import sqlite3
import tempfile
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Any

from st_agent.contracts.identifiers import digest_id
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.market.errors import MarketValidationError

__all__ = [
    "MARKET_DB_NAME",
    "MarketDb",
]

MARKET_DB_NAME = "market.db"
"""``data_cache`` 分区内的单库文件名。"""

_SCHEMA_RESOURCE = "schema.sql"

# 数据域 → 实际截止时间查询（02 §5「``as_of`` 按数据域取该域数据的实际截止时间」）。
_AS_OF_SQL: dict[str, str] = {
    "kline": "SELECT max(trade_date) FROM k_line_daily",
    "financial": "SELECT max(stat_date) FROM financial_quarter",
    "company_report": (
        "SELECT max(d) FROM (SELECT max(stat_date) AS d FROM performance_express "
        "UNION ALL SELECT max(stat_date) FROM profit_forecast)"
    ),
    "sector": (
        "SELECT max(d) FROM (SELECT max(update_date) AS d FROM stock_industry "
        "UNION ALL SELECT max(update_date) FROM index_constituent)"
    ),
    "macro": (
        "SELECT max(d) FROM (SELECT max(pub_date) AS d FROM macro_deposit_rate "
        "UNION ALL SELECT max(pub_date) FROM macro_loan_rate "
        "UNION ALL SELECT max(pub_date) FROM macro_reserve_ratio "
        "UNION ALL SELECT max(stat_year || '-' || printf('%02d', stat_month)) "
        "FROM macro_money_supply_month)"
    ),
}

_FRESHNESS_SQL = (
    "SELECT task_key, table_name, last_status, last_success_at, watermark, last_error "
    "FROM sync_state WHERE last_status IS NOT 'ok' OR last_success_at IS NULL"
)

_META_TABLES: frozenset[str] = frozenset({
    "sync_state", "data_source", "indicator_dictionary", "indicator_source_map",
    "sync_coverage",
})
"""非业务表（总览的「逐表行数」不计入）。"""

_SCHEMA_OBJECT_RE = re.compile(
    r"CREATE\s+(?:UNIQUE\s+)?(?:TABLE|VIEW|INDEX)\s+"
    r"(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)

_SCHEMA_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)

_READONLY_HEADS: tuple[str, ...] = ("SELECT", "WITH", "PRAGMA", "EXPLAIN")
"""只读 SQL 白名单（``MarketDb.query`` 与 ``local_sql`` 原语共用的唯一口径）。"""


def _st_pool_unavailable(reason: str) -> dict[str, Any]:
    """ST 池不可用（缺库 / 缺视图 / 读失败）的统一形状（[D-130]）。"""
    return {
        "available": False, "codes": (), "count": 0, "as_of": None, "reason": reason,
    }


def _st_pool_in(con: sqlite3.Connection, *, include_codes: bool) -> dict[str, Any]:
    """一次只读连接内的 ST 池解析（在市 × 最新日线 ``is_st=1``）。"""
    try:
        view = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='view' AND name='v_st_universe'"
        ).fetchone()
    except sqlite3.OperationalError as exc:
        return _st_pool_unavailable(f"ST 池读取失败：{exc}")
    if view is None:
        return _st_pool_unavailable(
            "本地库缺少 v_st_universe 视图（旧 schema；需重建库或升级）"
        )
    try:
        codes = tuple(
            r[0] for r in con.execute(
                "SELECT code FROM v_st_universe ORDER BY code"
            ).fetchall()
        )
        row = con.execute("SELECT max(trade_date) FROM k_line_daily").fetchone()
        as_of = row[0] if row and row[0] else None
    except sqlite3.OperationalError as exc:
        return _st_pool_unavailable(f"ST 池读取失败：{exc}")
    return {
        "available": True,
        "codes": codes if include_codes else (),
        "count": len(codes),
        "as_of": as_of,
        "reason": "" if codes else "ST 股票池为空（需先同步证券主档与日线行情）",
    }


def _st_sync_unavailable(reason: str) -> dict[str, Any]:
    """ST 同步池不可用的统一形状（[D-131]）。"""
    return {
        "available": False, "codes": (), "count": 0, "as_of": None,
        "source": "BaoStock 名称匹配（快照）", "reason": reason,
    }


def _st_sync_pool_in(con: sqlite3.Connection, *, include_codes: bool) -> dict[str, Any]:
    """一次只读连接内的 ST 同步池解析（``v_st_sync_pool`` / ``st_membership``）。"""
    try:
        view = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='view' AND name='v_st_sync_pool'"
        ).fetchone()
    except sqlite3.OperationalError as exc:
        return _st_sync_unavailable(f"ST 同步池读取失败：{exc}")
    if view is None:
        return _st_sync_unavailable(
            "本地库缺少 v_st_sync_pool 视图（旧 schema；需升级）"
        )
    try:
        codes = tuple(
            r[0] for r in con.execute(
                "SELECT code FROM v_st_sync_pool ORDER BY code"
            ).fetchall()
        )
        row = con.execute("SELECT max(captured_at) FROM st_membership").fetchone()
        as_of = row[0] if row and row[0] else None
    except sqlite3.OperationalError as exc:
        return _st_sync_unavailable(f"ST 同步池读取失败：{exc}")
    return {
        "available": True,
        "codes": codes if include_codes else (),
        "count": len(codes),
        "as_of": as_of,
        "source": "BaoStock 名称匹配（快照）",
        "reason": "" if codes else "ST 名单为空（先刷新 ST 名单）",
    }



def _now() -> datetime:
    """用户本地时区当前时刻（01 §8：时间统一用用户本地时区存储与展示）。"""
    return datetime.now().astimezone()


def _schema_text() -> str:
    return resources.files(__package__).joinpath(_SCHEMA_RESOURCE).read_text(
        encoding="utf-8"
    )


def _schema_table_names() -> tuple[str, ...]:
    """``schema.sql`` 声明的业务表（排除元数据表；供「表缺 / 表空」判定）。"""
    names: list[str] = []
    for statement in _schema_text().split(";"):
        match = _SCHEMA_TABLE_RE.search(statement)
        if match and match.group(1) not in _META_TABLES:
            names.append(match.group(1))
    return tuple(dict.fromkeys(names))


def _expected_task_tables() -> tuple[tuple[str, tuple[str, ...]], ...]:
    """全部同步任务的 ``(task_key, 写入表)``（行情 + 信息面）。"""
    from st_agent.l0.info.sources import INFO_TASKS
    from st_agent.l0.market.tasks import SYNC_TASKS

    rows: list[tuple[str, tuple[str, ...]]] = [
        (task.task_key, (task.table_name,)) for task in SYNC_TASKS
    ]
    rows.extend((task.task_key, task.written_tables) for task in INFO_TASKS)
    return tuple(rows)


def _task_status(
    task_key: str, table_names: tuple[str, ...], state: dict[str, Any] | None
) -> dict[str, Any]:
    """逐任务状态：``synced`` / ``incomplete`` / ``never_synced``。"""
    if state is None:
        status = "never_synced"
    elif state.get("last_status") == "ok" and state.get("last_success_at"):
        status = "synced"
    else:
        status = "incomplete"
    return {
        "task_key": task_key,
        "tables": list(table_names),
        "state": status,
        "last_status": (state or {}).get("last_status"),
        "last_success_at": (state or {}).get("last_success_at"),
        "watermark": (state or {}).get("watermark"),
        "last_error": (state or {}).get("last_error"),
    }


def _table_status(
    name: str,
    counts: dict[str, int],
    existing: set[str],
    owners: set[str],
    states: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """逐表状态：``present`` / ``known_empty`` / ``missing``。

    「已知空集」要求该表存在、行数为 0，且其所属任务**全部**已成功同步——否则
    0 行只说明「尚未写入」，属数据缺失而非可知的空集（T-AGT-010.2 GWT-2）。
    """
    if name in counts:
        rows: int | None = counts[name]
    elif name in existing:
        rows = 0
    else:
        return {"table": name, "rows": None, "state": "missing"}
    if rows > 0:
        return {"table": name, "rows": rows, "state": "present"}
    synced = all(
        states.get(key, {}).get("last_status") == "ok"
        and bool(states.get(key, {}).get("last_success_at"))
        for key in owners
    )
    return {"table": name, "rows": rows, "state": "known_empty" if owners and synced else "missing"}


def _coverage_status(
    rows: Iterable[tuple[Any, ...]], *, dataset: str | None, max_codes: int
) -> tuple[list[dict[str, Any]], list[str], int]:
    """覆盖账本 → 按数据集/变体汇总，缺口 = **无任何 ``ok`` 行**的码。"""
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for dataset_id, variant, unit, code, status, groups in rows:
        if dataset and str(dataset_id) != dataset:
            continue
        key = (str(dataset_id), str(variant or ""))
        bucket = buckets.setdefault(
            key,
            {"unit": str(unit), "codes": set(), "ok": set(), "gap": set(), "groups": set()},
        )
        bucket["codes"].add(str(code))
        (bucket["ok"] if str(status) == "ok" else bucket["gap"]).add(str(code))
        if groups and str(groups) != "all":
            bucket["groups"].update(part for part in str(groups).split(",") if part)
    entries: list[dict[str, Any]] = []
    all_gaps: set[str] = set()
    for (dataset_id, variant), bucket in sorted(buckets.items()):
        gaps = sorted(bucket["gap"] - bucket["ok"])
        all_gaps.update(gaps)
        entries.append({
            "dataset_id": dataset_id,
            "variant": variant,
            "unit": bucket["unit"],
            "groups": sorted(bucket["groups"]),
            "code_count": len(bucket["codes"]),
            "covered_code_count": len(bucket["ok"]),
            "gap_count": len(gaps),
            "gap_codes": gaps[:max_codes],
            "truncated": len(gaps) > max_codes,
        })
    ordered = sorted(all_gaps)
    return entries, ordered[:max_codes], len(ordered)


class MarketDb:
    """本地市场数据库门面（02 §5；只经 ``Store`` 读写 ``data_cache`` 分区）。

    :param store: ``Store`` 句柄（单库 blob 的唯一出入口）
    """

    def __init__(self, store) -> None:
        self._store = store

    @property
    def store(self):
        """底层 ``Store`` 句柄（文本面 / 审计面需要与库同分区的出入口）。"""
        return self._store

    # ───────────────────────── 建库与状态 ─────────────────────────

    def exists(self) -> bool:
        """本地单库是否存在（``data_cache/market.db`` 清单口径）。"""
        return MARKET_DB_NAME in self._store.list_files("data_cache")

    def init_db(self) -> None:
        """首次建库：执行 ``schema.sql`` 全量 DDL（已存在 → 拒绝，防误覆盖）。"""
        if self.exists():
            raise MarketValidationError(
                f"{MARKET_DB_NAME} 已存在；重建请先清空 data_cache 分区对应文件"
            )
        with self._work_copy(create=True) as path:
            con = sqlite3.connect(path)
            try:
                con.executescript(_schema_text())
                con.commit()
            finally:
                con.close()
            self._store.put("data_cache", MARKET_DB_NAME, Path(path).read_bytes())

    def ensure_schema(self) -> bool:
        """把旧库补齐到当前 ``schema.sql`` 的对象集合（幂等；不动业务行）。

        只创建 ``sqlite_master`` 中缺失的表 / 视图 / 索引；既有对象不重建。
        无缺失时不写回分区，避免每次任务都多一次整库加解密。
        返回是否发生了写回。
        """
        if not self.exists():
            return False
        changed = False
        with self._work_copy() as path:
            con = sqlite3.connect(path)
            try:
                con.execute("PRAGMA foreign_keys = ON")
                existing = {
                    r[0] for r in con.execute(
                        "SELECT name FROM sqlite_master"
                    ).fetchall()
                }
                for statement in _schema_text().split(";"):
                    match = _SCHEMA_OBJECT_RE.search(statement)
                    if match is None or match.group(1) in existing:
                        continue
                    con.execute(statement)
                    existing.add(match.group(1))
                    changed = True
                if changed:
                    con.commit()
            finally:
                con.close()
            if changed:
                self._write_back(path)
        return changed

    def tables(self) -> tuple[str, ...]:
        """列出单库全部表名（GWT-1 全量表断言的机器可读口径）。"""
        with self._readonly() as con:
            rows = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        return tuple(r[0] for r in rows)

    def views(self) -> tuple[str, ...]:
        """列出单库全部视图名（复权推导视图不断言落盘数据）。"""
        with self._readonly() as con:
            rows = con.execute(
                "SELECT name FROM sqlite_master WHERE type='view' ORDER BY name"
            ).fetchall()
        return tuple(r[0] for r in rows)

    def sync_state_rows(self) -> tuple[dict[str, Any], ...]:
        """``sync_state`` 全量行（逐任务水位 / 成功时间 / 状态；空库 → 空元组）。

        与 :meth:`freshness` 的分工：后者只回**非 ok** 行（自检口径），本方法回**全部**
        行——设置-数据同步视图要据此同时显示「已成功」与「未同步」。
        """
        if not self.exists():
            return ()
        with self._readonly() as con:
            return self._sync_state_rows_in(con)

    @staticmethod
    def _sync_state_rows_in(con: sqlite3.Connection) -> tuple[dict[str, Any], ...]:
        """一次只读连接内的 ``sync_state`` 全量行（overview 复用）。"""
        cur = con.execute(
            "SELECT task_key, table_name, source_id, mode, schedule_desc, watermark,"
            " last_success_at, last_row_count, last_status, last_error"
            " FROM sync_state ORDER BY task_key"
        )
        columns = [d[0] for d in (cur.description or [])]
        return tuple(dict(zip(columns, row)) for row in cur.fetchall())

    def table_counts(
        self, tables: Iterable[str] | None = None
    ) -> dict[str, int]:
        """逐表行数（**一次**只读连接内完成；[D-129]）。

        ``_readonly`` 每次都会把整库 blob 解密到临时文件，逐表调用会把大库反复解密；
        故总览面只用本方法。``tables`` 缺省＝全部业务表（排除 ``sync_state`` /
        ``data_source`` / 指标字典等元数据表）；显式给出时只计其中**真实存在**的表，
        不存在的表不出现在结果里（「表缺」与「表空」由调用方分开呈现）。
        """
        if not self.exists():
            return {}
        with self._readonly() as con:
            return self._counts_in(con, tables)

    @staticmethod
    def _counts_in(
        con: sqlite3.Connection, tables: Iterable[str] | None = None
    ) -> dict[str, int]:
        """一次只读连接内的逐表行数（``table_counts`` / ``overview_snapshot`` 复用）。"""
        existing = {
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        selected = (
            sorted(existing - _META_TABLES) if tables is None
            else [name for name in dict.fromkeys(tables) if name in existing]
        )
        counts: dict[str, int] = {}
        for name in selected:
            quoted = '"' + str(name).replace('"', '""') + '"'
            row = con.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()
            counts[str(name)] = int(row[0]) if row else 0
        return counts

    def overview_snapshot(self) -> dict[str, Any]:
        """数据同步总览的**一次只读连接**快照（[D-130]）。

        一次连接内取齐：``sync_state`` 全量行、全部业务表行数、ST 池规模与数据截止、
        在市股票数。旧调用方仍可分别用 :meth:`sync_state_rows` / :meth:`table_counts`；
        总览面用本方法避免整库反复解密。
        """
        if not self.exists():
            return {
                "states": (),
                "counts": {},
                "st": _st_pool_unavailable("本地市场数据库尚未建立"),
                "st_sync": _st_sync_unavailable("本地市场数据库尚未建立"),
                "listed_count": None,
            }
        with self._readonly() as con:
            states = self._sync_state_rows_in(con)
            counts = self._counts_in(con)
            st = _st_pool_in(con, include_codes=True)
            st_sync = _st_sync_pool_in(con, include_codes=True)
            listed_count = self._listed_count_in(con)
        return {
            "states": states,
            "counts": counts,
            "st": st,
            "st_sync": st_sync,
            "listed_count": listed_count,
        }

    def st_pool(self) -> dict[str, Any]:
        """当前 ST 股票池（``v_st_universe``；含代码集，供范围同步解析）。

        返回 ``{available, codes, count, as_of, reason}``。空池是**可知的可用空集**
        （``available=True, count=0`` + 原因），缺视图 / 旧库才是 ``available=False``
        ——两者不得混成同一种空态（[01 §5]）。
        """
        if not self.exists():
            return _st_pool_unavailable("本地市场数据库尚未建立")
        with self._readonly() as con:
            return _st_pool_in(con, include_codes=True)

    def preflight_snapshot(self) -> dict[str, Any]:
        """同步预检的**轻量**快照：``sync_state`` + ST 池 + 在市股票数。

        与 :meth:`overview_snapshot` 的分工：预检只看「首次全量」与范围解析，不需要
        逐表行数——大库上 ``COUNT(*)`` 很贵，故本方法**不计数业务表**（[D-130]）。
        """
        if not self.exists():
            return {
                "states": (),
                "st": _st_pool_unavailable("本地市场数据库尚未建立"),
                "st_sync": _st_sync_unavailable("本地市场数据库尚未建立"),
                "listed_count": None,
            }
        with self._readonly() as con:
            return {
                "states": self._sync_state_rows_in(con),
                "st": _st_pool_in(con, include_codes=True),
                "st_sync": _st_sync_pool_in(con, include_codes=True),
                "listed_count": self._listed_count_in(con),
            }

    def st_sync_pool(self) -> dict[str, Any]:
        """ST 同步标的池（``v_st_sync_pool`` / ``st_membership``；[D-131]）。

        与分析区的 :meth:`st_pool` 分开：本方法返回的是名称匹配快照，服务同步标的；
        ``as_of`` 为名单获取时间 ``captured_at``。
        """
        if not self.exists():
            return _st_sync_unavailable("本地市场数据库尚未建立")
        with self._readonly() as con:
            return _st_sync_pool_in(con, include_codes=True)

    def st_codes(self) -> list[str]:
        """ST 池代码列表（不可用或空池 → 空列表；调用方先读 :meth:`st_pool` 判原因）。"""
        return list(self.st_pool()["codes"])

    def listed_stock_count(self) -> int | None:
        """在市股票数（``security`` 中 ``type=1`` 且 ``status=1``；缺库 / 缺表 → None）。"""
        if not self.exists():
            return None
        with self._readonly() as con:
            return self._listed_count_in(con)

    @staticmethod
    def _listed_count_in(con: sqlite3.Connection) -> int | None:
        try:
            row = con.execute(
                "SELECT COUNT(*) FROM security WHERE type=1 AND status=1"
            ).fetchone()
            return int(row[0]) if row else 0
        except sqlite3.OperationalError:
            return None


    def last_updated_at(self) -> datetime | None:
        """全库「最后更新时间 T」（``sync_state.last_success_at`` 最大值；无则 None）。"""
        if not self.exists():
            return None
        with self._readonly() as con:
            row = con.execute(
                "SELECT max(last_success_at) FROM sync_state "
                "WHERE last_success_at IS NOT NULL"
            ).fetchone()
        if row is None or row[0] is None:
            return None
        stamp = datetime.fromisoformat(row[0])
        return stamp if stamp.tzinfo is not None else stamp.astimezone()

    # ───────────────────────── 查询面（上层统一入口） ─────────────────────────

    def query(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
        *,
        max_rows: int | None = None,
        timeout_ms: int | None = None,
    ) -> ResultEnvelope:
        """执行只读 SQL（白名单：SELECT / WITH / PRAGMA / EXPLAIN 单语句）。

        空结果走 ``empty``（带原因）；库缺失/表缺失走 ``unavailable``（标注
        最后更新时间）；非法 SQL 走 ``validation_failed``。

        :param max_rows: 可选行数上限。给出时 ok 载荷增 ``row_count`` / ``truncated`` /
            ``limit`` / ``hint``；超出即**显式**截断，不静默丢行（T-AGT-010.1 GWT-3）。
        :param timeout_ms: 可选执行时限。超时中止查询并走 ``failed``（附收窄建议），
            不返回半截结果。
        """
        if max_rows is not None and max_rows < 1:
            return ResultEnvelope.validation_failed("行数上限须为正整数")
        if timeout_ms is not None and timeout_ms < 1:
            return ResultEnvelope.validation_failed("执行时限须为正整数（毫秒）")
        stripped = sql.strip()
        head = stripped.split(";")[0].strip().upper()
        if ";" in stripped.rstrip(";") or not head.startswith(_READONLY_HEADS):
            return ResultEnvelope.validation_failed(
                "只读查询仅支持 SELECT / WITH / PRAGMA / EXPLAIN 单语句"
            )
        if not self.exists():
            now = _now()
            return ResultEnvelope.unavailable(
                "本地市场数据库尚未建立（首次同步未完成）",
                last_updated_at=now, as_of=now,
            )
        try:
            with self._readonly(timeout_ms=timeout_ms) as con:
                columns, rows, truncated = self._run_readonly(
                    con, stripped, params, max_rows
                )
        except sqlite3.OperationalError as exc:
            message = str(exc)
            if "interrupted" in message.lower() and timeout_ms is not None:
                return ResultEnvelope.failed(
                    f"查询超过执行时限 {timeout_ms} ms 已中止；"
                    "请用更窄的 WHERE / LIMIT 或减少列后重试",
                    log_ref=f"data_cache/{MARKET_DB_NAME}",
                )
            if "no such table" in message or "no such view" in message:
                stamp = self.last_updated_at() or _now()
                return ResultEnvelope.unavailable(
                    f"查询目标不存在（{message}）；数据面不完整",
                    last_updated_at=stamp, as_of=stamp,
                )
            stamp = self.last_updated_at() or _now()
            return ResultEnvelope.failed(
                f"查询执行失败：{message}",
                log_ref=f"data_cache/{MARKET_DB_NAME}",
            )
        as_of = self._domain_as_of()
        if not rows:
            return ResultEnvelope.empty("查询合法但结果为空（该条件无匹配行）", as_of=as_of)
        data: dict[str, Any] = {"columns": columns, "rows": rows}
        if max_rows is not None:
            data.update({
                "row_count": len(rows),
                "truncated": truncated,
                "limit": max_rows,
                "hint": (
                    f"结果已截断：仅返回前 {max_rows} 行。"
                    "用更窄的 WHERE 条件或 LIMIT / OFFSET 子句继续查询。"
                    if truncated else ""
                ),
            })
        return ResultEnvelope.ok(data, as_of=as_of)

    @staticmethod
    def _run_readonly(
        con: sqlite3.Connection,
        sql: str,
        params: tuple[Any, ...],
        max_rows: int | None,
    ) -> tuple[list[str], list[dict[str, Any]], bool]:
        """在给定只读连接上执行查询 → ``(列名, 行, 是否截断)``。

        ``SELECT`` / ``WITH`` 以子查询包裹并加 ``LIMIT``，避免大表整表取回；其余只读
        语句（``PRAGMA`` / ``EXPLAIN``）无法包裹，退化为多取一行再切片。
        """
        wrapped = max_rows is not None and sql.split()[0].upper() in ("SELECT", "WITH")
        if wrapped:
            assert max_rows is not None
            statement = f"SELECT * FROM ({sql.rstrip(';')}) LIMIT ?"
            cur = con.execute(statement, (*params, max_rows + 1))
        else:
            cur = con.execute(sql, params)
        columns = [d[0] for d in (cur.description or [])]
        if max_rows is None:
            return columns, [dict(zip(columns, r)) for r in cur.fetchall()], False
        fetched = cur.fetchmany(max_rows + 1)
        truncated = len(fetched) > max_rows
        return columns, [dict(zip(columns, r)) for r in fetched[:max_rows]], truncated

    def freshness(self) -> tuple[dict[str, Any], ...]:
        """统一自检入口（05 文档 SQL：``sync_state`` 非 ok 行；空 = 数据面完整）。"""
        if not self.exists():
            return ({"task_key": "__market_db__", "last_status": "missing",
                     "detail": "本地市场数据库尚未建立"},)
        with self._readonly() as con:
            cur = con.execute(_FRESHNESS_SQL)
            columns = [d[0] for d in cur.description]
            return tuple(dict(zip(columns, r)) for r in cur.fetchall())

    def as_of(self, domain: str) -> str | None:
        """按数据域取实际截止时间（``kline`` / ``financial`` / ``company_report`` /
        ``sector`` / ``macro``；未知域 → ``MarketValidationError``）。"""
        if domain not in _AS_OF_SQL:
            raise MarketValidationError(
                f"未知数据域 {domain!r}；合法域 = {sorted(_AS_OF_SQL)}"
            )
        if not self.exists():
            return None
        with self._readonly() as con:
            row = con.execute(_AS_OF_SQL[domain]).fetchone()
        return row[0] if row else None

    def status_snapshot(
        self, *, dataset: str | None = None, max_codes: int = 50
    ) -> dict[str, Any]:
        """数据现状的**一次只读连接**快照（T-AGT-010.2；02 §5 / 05 新鲜度契约）。

        面向模型的数据自检面：逐任务状态与最后成功时间、逐表行数与
        「已知空集 / 数据缺失」区分、数据域实际截止时间、覆盖账本缺口。
        不发网络、不写任何存储。
        """
        if not self.exists():
            return {
                "db_exists": False,
                "snapshot_id": None,
                "as_of": {},
                "tasks": [],
                "tables": [],
                "freshness": [],
                "coverage": [],
                "gap_codes": [],
                "gap_count": 0,
                "last_updated_at": None,
                "notes": ["本地市场数据库尚未建立（首次同步未完成）"],
            }
        expected = _expected_task_tables()
        owners: dict[str, set[str]] = {}
        for task_key, table_names in expected:
            for name in table_names:
                owners.setdefault(name, set()).add(task_key)
        notes: list[str] = []
        with self._readonly() as con:
            try:
                states = self._sync_state_rows_in(con)
            except sqlite3.OperationalError as exc:
                states = ()
                notes.append(f"sync_state 读取失败：{exc}（旧 schema；需升级本地库）")
            counts = self._counts_in(con)
            existing = {
                row[0] for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            as_of: dict[str, Any] = {}
            for domain, statement in _AS_OF_SQL.items():
                try:
                    row = con.execute(statement).fetchone()
                    as_of[domain] = row[0] if row and row[0] else None
                except sqlite3.OperationalError:
                    as_of[domain] = None
            try:
                coverage_rows = con.execute(
                    "SELECT dataset_id, variant, unit, code, status, groups "
                    "FROM sync_coverage"
                ).fetchall()
            except sqlite3.OperationalError:
                coverage_rows = []
                notes.append("本地库缺少 sync_coverage 覆盖账本（旧 schema；需升级本地库）")

        states_by_key = {row["task_key"]: row for row in states}
        tasks = [
            _task_status(task_key, table_names, states_by_key.get(task_key))
            for task_key, table_names in expected
        ]
        tables = [
            _table_status(name, counts, existing, owners.get(name, set()), states_by_key)
            for name in _schema_table_names()
        ]
        freshness = [
            {
                "task_key": row["task_key"],
                "last_status": row.get("last_status"),
                "last_success_at": row.get("last_success_at"),
                "last_error": row.get("last_error"),
            }
            for row in states
            if row.get("last_status") != "ok" or not row.get("last_success_at")
        ]
        coverage, gap_codes, gap_count = _coverage_status(
            coverage_rows, dataset=dataset, max_codes=max_codes
        )
        if dataset and not coverage:
            notes.append(f"数据集 {dataset!r} 无覆盖账本记录（尚未按该数据集同步）")
        if not states:
            notes.append("sync_state 为空：尚未执行任何同步任务（数据缺失，不是已知空集）")
        last_success = max(
            (str(row["last_success_at"]) for row in states if row.get("last_success_at")),
            default=None,
        )
        components = "|".join(
            f"{row['task_key']}:{row.get('watermark') or '-'}@{row.get('last_success_at') or '-'}"
            for row in states
        )
        return {
            "db_exists": True,
            "snapshot_id": digest_id("snap", components),
            "as_of": as_of,
            "tasks": tasks,
            "tables": tables,
            "freshness": freshness,
            "coverage": coverage,
            "gap_codes": gap_codes,
            "gap_count": gap_count,
            "last_updated_at": last_success,
            "notes": notes,
        }

    def snapshot_id(self) -> str:
        """数据快照 ID（01 §1 ``dataset_snapshot_id``：形态 ``snap_<20 位十六进制>``）。

        取各任务水位的**确定性摘要**——同水位 → 同 ID、换任一水位 → 换 ID，可复核；
        长度有界（恒 25 字符，远低于 ``EvidenceRef.ref`` 与 ``DataAnchor`` 的 128
        上限），消费方可**直取**本值作证据引用 / 数据锚点，无需二次加工。摘要输入
        的原文见 :meth:`snapshot_components`。
        """
        return digest_id("snap", self.snapshot_components())

    def snapshot_components(self) -> str:
        """可读水位组合串（逐任务 ``task_key: watermark @ last_success_at`` 拼接）。

        :meth:`snapshot_id` 的**摘要输入**。02 §5「不另行维护快照登记表」——快照
        锚定于 ``sync_state`` 现状，本方法只如实呈现（不做判定、不缩略），故给定
        ID 可由本串复算出（「可解释」的落地面）。
        """
        if not self.exists():
            raise MarketValidationError("本地市场数据库尚未建立，无水位可锚定")
        with self._readonly() as con:
            rows = con.execute(
                "SELECT task_key, watermark, last_success_at FROM sync_state ORDER BY task_key"
            ).fetchall()
        return "|".join(f"{k}:{w or '-'}@{t or '-'}" for k, w, t in rows)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """只读工作连接（调用方 ``with`` 自行 ``close``；写操作走同步管道）。"""
        with self._readonly() as con:
            yield con

    # ───────────────────────── 内部：工作拷贝 ─────────────────────────

    @contextmanager
    def _readonly(self, *, timeout_ms: int | None = None) -> Iterator[sqlite3.Connection]:
        """读工作拷贝（blob → 临时文件 → 只读连接）。

        :param timeout_ms: 可选执行时限；给出时以 SQLite progress handler 在超时后中止
            当前语句（``OperationalError: interrupted``），调用方据此显式失败。
        """
        blob = self._store.get("data_cache", MARKET_DB_NAME)
        with tempfile.TemporaryDirectory(prefix="st-market-") as tmp:
            path = str(Path(tmp) / MARKET_DB_NAME)
            Path(path).write_bytes(blob)
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                con.execute("PRAGMA foreign_keys = ON")
                if timeout_ms is not None:
                    deadline = time.monotonic() + timeout_ms / 1000
                    con.set_progress_handler(
                        lambda: 1 if time.monotonic() >= deadline else 0, 1000
                    )
                yield con
            finally:
                con.close()

    @contextmanager
    def _work_copy(self, *, create: bool = False) -> Iterator[str]:
        """可写工作拷贝路径（调用方提交后由 ``_write_back`` 写回分区）。

        ``create=True`` 时从空文件起步（首次建库）；否则从现有 blob 起步。
        """
        with tempfile.TemporaryDirectory(prefix="st-market-") as tmp:
            path = str(Path(tmp) / MARKET_DB_NAME)
            if not create:
                Path(path).write_bytes(
                    self._store.get("data_cache", MARKET_DB_NAME)
                )
            yield path

    def _write_back(self, path: str) -> None:
        """把工作拷贝写回 ``data_cache`` 分区（清单校验和同步更新）。"""
        self._store.put("data_cache", MARKET_DB_NAME, Path(path).read_bytes())

    def _domain_as_of(self) -> datetime | None:
        """信封 ``as_of`` 默认口径：全库「最后更新时间 T」（无则 None）。"""
        return self.last_updated_at()

    # ───────────────────────── 内部：可写事务（同步管道） ─────────────────────────

    @contextmanager
    def transact(self) -> Iterator[sqlite3.Connection]:
        """可写工作事务（仅同步管道使用；提交后自动写回分区）。

        用法::

            with db.transact() as con:
                con.execute("INSERT ...")
                con.commit()

        退出时（无论提交与否）把工作拷贝写回 ``data_cache`` 分区。
        外键约束显式开启。
        """
        with self._work_copy() as path:
            con = sqlite3.connect(path)
            try:
                con.execute("PRAGMA foreign_keys = ON")
                yield con
            finally:
                try:
                    con.close()
                finally:
                    self._write_back(path)

    @staticmethod
    def check_readonly_sql(sql: str) -> str:
        """只读 SQL 形状校验（供同步管道复用同一口径；非法 → 异常）。"""
        head = sql.strip().split(";")[0].strip().upper()
        if ";" in sql.strip().rstrip(";") or not head.startswith(_READONLY_HEADS):
            raise MarketValidationError(
                "只读查询仅支持 SELECT / WITH / PRAGMA / EXPLAIN 单语句"
            )
        if not re.search(r"\w", sql):
            raise MarketValidationError("空查询")
        return sql
