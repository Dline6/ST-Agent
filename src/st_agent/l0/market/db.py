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
- ``snapshot_id``：读取时各任务 ``(task_key, watermark, last_success_at)``
  组合（02 §5「不另行维护快照登记表」）
"""

from __future__ import annotations

import re
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Any

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


def _now() -> datetime:
    """用户本地时区当前时刻（01 §8：时间统一用用户本地时区存储与展示）。"""
    return datetime.now().astimezone()


def _schema_text() -> str:
    return resources.files(__package__).joinpath(_SCHEMA_RESOURCE).read_text(
        encoding="utf-8"
    )


class MarketDb:
    """本地市场数据库门面（02 §5；只经 ``Store`` 读写 ``data_cache`` 分区）。

    :param store: ``Store`` 句柄（单库 blob 的唯一出入口）
    """

    def __init__(self, store) -> None:
        self._store = store

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

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> ResultEnvelope:
        """执行只读 SQL（白名单：SELECT / WITH / PRAGMA / EXPLAIN 单语句）。

        空结果走 ``empty``（带原因）；库缺失/表缺失走 ``unavailable``（标注
        最后更新时间）；非法 SQL 走 ``validation_failed``。
        """
        head = sql.strip().split(";")[0].strip().upper()
        if ";" in sql.strip().rstrip(";") or not head.startswith(
            ("SELECT", "WITH", "PRAGMA", "EXPLAIN")
        ):
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
            with self._readonly() as con:
                cur = con.execute(sql, params)
                columns = [d[0] for d in (cur.description or [])]
                rows = [dict(zip(columns, r)) for r in cur.fetchall()]
        except sqlite3.OperationalError as exc:
            message = str(exc)
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
        return ResultEnvelope.ok({"columns": columns, "rows": rows}, as_of=as_of)

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

    def snapshot_id(self) -> str:
        """读取时水位组合（``task_key: watermark @ last_success_at`` 拼接；
        02 §5「不另行维护快照登记表」）。"""
        if not self.exists():
            raise MarketValidationError("本地市场数据库尚未建立，无水位可锚定")
        with self._readonly() as con:
            rows = con.execute(
                "SELECT task_key, watermark, last_success_at FROM sync_state ORDER BY task_key"
            ).fetchall()
        parts = [f"{k}:{w or '-'}@{t or '-'}" for k, w, t in rows]
        return "snap|" + "|".join(parts)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """只读工作连接（调用方 ``with`` 自行 ``close``；写操作走同步管道）。"""
        with self._readonly() as con:
            yield con

    # ───────────────────────── 内部：工作拷贝 ─────────────────────────

    @contextmanager
    def _readonly(self) -> Iterator[sqlite3.Connection]:
        """读工作拷贝（blob → 临时文件 → 只读连接）。"""
        blob = self._store.get("data_cache", MARKET_DB_NAME)
        with tempfile.TemporaryDirectory(prefix="st-market-") as tmp:
            path = str(Path(tmp) / MARKET_DB_NAME)
            Path(path).write_bytes(blob)
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                con.execute("PRAGMA foreign_keys = ON")
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
        if ";" in sql.strip().rstrip(";") or not head.startswith(
            ("SELECT", "WITH", "PRAGMA", "EXPLAIN")
        ):
            raise MarketValidationError(
                "只读查询仅支持 SELECT / WITH / PRAGMA / EXPLAIN 单语句"
            )
        if not re.search(r"\w", sql):
            raise MarketValidationError("空查询")
        return sql
