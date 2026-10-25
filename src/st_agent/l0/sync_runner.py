"""手动同步与数据总览的执行面（[02 §5](../../../docs/技术架构-v2/02-L0-本地优先基座.md)；[D-129]）。

**只做 L0 的事**：读 ``sync_state`` / 表行数、按任务清单发起 ``BaoStockSync`` /
``InfoSync``，并把进度与逐任务结果留在内存。表现层经组合根的鸭子端口消费。

三条硬口径：

- **单飞**——``MarketDb.transact()`` 是「取整库工作副本 → 改 → 写回」，并发任务会互相
  覆盖；故同一时刻只允许一个批次，第二次 ``start`` 直接拒。
- **前置自动补齐**——行情任务的 ``needs``（日历 + 全量快照）与信息面任务依赖的
  ``security`` 主档由本模块自动补入，并在回报里标注 ``auto_added``，不静默多跑。
- **失败不连坐**——单个任务失败只记该任务的结果，其余任务继续；进程退出不保证长跑
  任务继续，下次按水位续跑。
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from typing import Any

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.info.sources import ATTENTION_TASKS, INFO_TASK_KEYS, INFO_TASKS
from st_agent.l0.info.switch_config import enabled_task_map
from st_agent.l0.info.sync import InfoSync
from st_agent.l0.market.db import MarketDb
from st_agent.l0.market.sync import DOMAIN_TASKS, RUN_ORDER, BaoStockSync
from st_agent.l0.market.tasks import TASK_KEYS as MARKET_TASK_KEYS
from st_agent.l0.market.tasks import get_task

__all__ = ["DataSyncRunner", "SyncBusyError", "SyncJobError"]

_MARKET_DOMAIN: dict[str, str] = {
    task_key: domain
    for domain, task_keys in DOMAIN_TASKS.items()
    for task_key in task_keys
}
_MARKET_DOMAIN.setdefault("bs_security_basic", "security")
_MARKET_DOMAIN.setdefault("bs_adjust_factor", "corporate_action")
_MARKET_DOMAIN.setdefault("bs_dividend", "corporate_action")
_MARKET_DOMAIN.setdefault("bs_k_minute", "kline")

# 信息面任务依赖的主档三件（05「前置：信息面 setup 要求市场库已建、security 主档在位」）。
_MASTER_TASKS: tuple[str, ...] = ("bs_calendar", "bs_security_basic", "bs_all_stock")


class SyncJobError(ValueError):
    """同步请求非法（未知任务 / 空选择）。"""


class SyncBusyError(SyncJobError):
    """已有批次在跑（单飞口径）。"""


def _now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _summary(envelope: ResultEnvelope) -> dict[str, Any]:
    data = envelope.data if isinstance(envelope.data, dict) else {}
    return {
        "status": envelope.status,
        "reason": envelope.reason or "",
        "rows": data.get("rows"),
        "as_of": envelope.as_of.isoformat() if envelope.as_of else None,
        "last_updated_at": (
            envelope.last_updated_at.isoformat() if envelope.last_updated_at else None
        ),
    }


class DataSyncRunner:
    """一次会话内的同步批次管理器（线程安全；只持内存状态）。"""

    def __init__(
        self,
        *,
        store,
        gateway,
        watchlist: Callable[[], tuple[str, ...]] | None = None,
        market_factory: Callable[[MarketDb, Any], tuple[Any, Callable[[], None]]] | None = None,
        info_factory: Callable[[MarketDb, Any], tuple[Any, Callable[[], None]]] | None = None,
        timeout_ms: int = 120_000,
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._watchlist = watchlist
        self._market_factory = market_factory
        self._info_factory = info_factory
        self._timeout_ms = timeout_ms
        self._lock = threading.Lock()
        self._job: dict[str, Any] | None = None
        self._thread: threading.Thread | None = None

    # ───────────────────────── 只读总览 ─────────────────────────

    def overview(self) -> dict[str, Any]:
        """逐任务 / 逐表的数据现状（一次只读连接内取行数）。"""
        db = MarketDb(self._store)
        exists = db.exists()
        states = {row["task_key"]: row for row in db.sync_state_rows()} if exists else {}
        tasks = self._task_rows(states)
        counts = db.table_counts(
            [table for task in tasks for table in task["tables"]]
        ) if exists else {}
        for task in tasks:
            task["table_rows"] = [
                {"table": name, "rows": counts.get(name)} for name in task["tables"]
            ]
        table_names = sorted({name for task in tasks for name in task["tables"]})
        tables = [
            {
                "table": name,
                "rows": counts.get(name),
                "domains": sorted({t["domain"] for t in tasks if name in t["tables"]}),
                "tasks": [t["task_key"] for t in tasks if name in t["tables"]],
            }
            for name in table_names
        ]
        total_rows = sum(value for value in counts.values() if isinstance(value, int))
        existing_table_count = sum(
            1 for item in tables if item["rows"] is not None
        )
        last_success = max(
            (
                str(row["last_success_at"])
                for row in states.values()
                if row.get("last_success_at")
            ),
            default=None,
        )
        ok_count = sum(
            1 for row in states.values() if row.get("last_status") == "ok"
        )
        summary = {
            "db_exists": exists,
            "task_count": len(tasks),
            "ok_task_count": ok_count,
            "table_count": existing_table_count,
            "covered_table_count": len(tables),
            "total_rows": total_rows,
            "last_success_at": last_success,
            "note": (
                "本地市场数据库尚未建立（首次同步未完成）"
                if not exists
                else ""
            ),
        }
        return {
            "summary": summary,
            "tasks": tasks,
            "tables": tables,
            "job": self.job(),
        }

    def _task_rows(self, states: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for task_key in RUN_ORDER:
            spec = get_task(task_key)
            state = states.get(task_key, {})
            rows.append({
                "kind": "market",
                "task_key": task_key,
                "domain": _MARKET_DOMAIN.get(task_key, spec.table_name),
                "label": task_key,
                "tables": [spec.table_name],
                "mode": spec.mode,
                "schedule": spec.schedule_desc,
                "coverage": "all",
                "role": "sole",
                "needs": list(spec.needs),
                "default_disabled": bool(spec.default_disabled),
                "status": state.get("last_status"),
                "watermark": state.get("watermark"),
                "last_success_at": state.get("last_success_at"),
                "last_error": state.get("last_error"),
                "registered": bool(state),
            })
        for spec in INFO_TASKS:
            state = states.get(spec.task_key, {})
            rows.append({
                "kind": "info",
                "task_key": spec.task_key,
                "domain": spec.domain,
                "label": spec.task_key,
                "tables": list(spec.written_tables),
                "mode": spec.mode,
                "schedule": spec.schedule_desc,
                "coverage": spec.coverage,
                "role": spec.role,
                "needs": [],
                "default_disabled": spec.task_key in ATTENTION_TASKS,
                "status": state.get("last_status"),
                "watermark": state.get("watermark"),
                "last_success_at": state.get("last_success_at"),
                "last_error": state.get("last_error"),
                "registered": bool(state),
            })
        return rows

    # ───────────────────────── 发批 ─────────────────────────

    def start(self, task_keys: Iterable[str]) -> dict[str, Any]:
        """发起一个批次；返回 ``{job_id, order, auto_added}``（异步执行）。"""
        keys = list(dict.fromkeys(str(item).strip() for item in task_keys if str(item).strip()))
        if not keys:
            raise SyncJobError("未选择任何同步任务")
        unknown = [key for key in keys if key not in MARKET_TASK_KEYS and key not in INFO_TASK_KEYS]
        if unknown:
            raise SyncJobError(f"未知同步任务：{', '.join(unknown)}")
        with self._lock:
            if self._job is not None and self._job["state"] == "running":
                raise SyncBusyError(
                    f"已有同步批次在跑（{self._job['job_id']}）；请等它结束或重启应用"
                )
            db = MarketDb(self._store)
            order, auto_added = self._plan(keys, db)
            job = {
                "job_id": uuid.uuid4().hex[:12],
                "state": "running",
                "selected": keys,
                "order": order,
                "auto_added": auto_added,
                "results": {},
                "current": None,
                "started_at": _now_text(),
                "finished_at": None,
                "error": "",
            }
            self._job = job
            thread = threading.Thread(
                target=self._run, args=(job, order), name="st-agent-data-sync", daemon=True
            )
            self._thread = thread
            thread.start()
        return {"job_id": job["job_id"], "order": order, "auto_added": auto_added}

    def job(self) -> dict[str, Any] | None:
        """当前 / 最近一次批次的快照（无批次 → ``None``）。"""
        with self._lock:
            if self._job is None:
                return None
            snapshot = dict(self._job)
            snapshot["results"] = dict(self._job["results"])
            return snapshot

    def _plan(self, keys: list[str], db: MarketDb) -> tuple[list[str], list[str]]:
        """展开依赖并给出执行顺序；返回 ``(order, auto_added)``。"""
        selected = set(keys)
        auto: list[str] = []
        market = {key for key in selected if key in MARKET_TASK_KEYS}
        info = {key for key in selected if key in INFO_TASK_KEYS}
        pending = list(market)
        while pending:
            key = pending.pop()
            for need in get_task(key).needs:
                if need not in market:
                    market.add(need)
                    auto.append(need)
                    pending.append(need)
        if info and self._master_missing(db):
            for key in _MASTER_TASKS:
                if key not in market:
                    market.add(key)
                    auto.append(key)
        order = [key for key in RUN_ORDER if key in market]
        order.extend(spec.task_key for spec in INFO_TASKS if spec.task_key in info)
        if not order:
            raise SyncJobError("选择的任务均被禁用或不可执行")
        return order, list(dict.fromkeys(auto))

    @staticmethod
    def _master_missing(db: MarketDb) -> bool:
        """信息面任务依赖的 ``security`` 主档是否为空（库不存在也算缺）。"""
        if not db.exists():
            return True
        try:
            return db.table_counts(["security"]).get("security", 0) == 0
        except Exception:      # noqa: BLE001 — 读不出就按「缺」处理，交给自动补齐
            return True

    # ───────────────────────── 后台执行 ─────────────────────────

    def _run(self, job: dict[str, Any], order: list[str]) -> None:
        results: dict[str, dict[str, Any]] = {}
        db = MarketDb(self._store)
        market_keys = [key for key in order if key in MARKET_TASK_KEYS]
        info_keys = [key for key in order if key in INFO_TASK_KEYS]
        try:
            if market_keys:
                self._run_market(job, db, market_keys, results)
            if info_keys:
                self._run_info(job, db, info_keys, results)
        except Exception as exc:        # noqa: BLE001 —— 批次级异常如实落到 job
            job["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            for key in order:
                results.setdefault(key, {
                    "status": "failed",
                    "reason": job["error"] or "批次提前结束，未执行",
                    "rows": None,
                })
            with self._lock:
                job["results"] = results
                job["current"] = None
                job["finished_at"] = _now_text()
                job["state"] = "failed" if job["error"] else "done"

    def _run_market(
        self, job: dict[str, Any], db: MarketDb, keys: list[str],
        results: dict[str, dict[str, Any]],
    ) -> None:
        try:
            sync, close = self._make_market(db)
        except Exception as exc:        # noqa: BLE001 — 抓取器缺席 / 登录失败
            reason = str(exc) or type(exc).__name__
            for key in keys:
                results[key] = {
                    "status": "unavailable", "reason": reason, "rows": None,
                }
            return
        try:
            setup = sync.setup()
            if setup.status != "ok":
                for key in keys:
                    results[key] = _summary(setup)
                return
            watch = self._watch()
            enabled = {key: True for key in keys}
            for key in keys:
                self._set_current(job, key)
                envelope = sync.run_task(
                    key, enabled=enabled, watchlist=watch, timeout_ms=self._timeout_ms
                )
                results[key] = _summary(envelope)
        finally:
            close()

    def _run_info(
        self, job: dict[str, Any], db: MarketDb, keys: list[str],
        results: dict[str, dict[str, Any]],
    ) -> None:
        try:
            sync, close = self._make_info(db)
        except Exception as exc:        # noqa: BLE001
            reason = str(exc) or type(exc).__name__
            for key in keys:
                results[key] = {
                    "status": "unavailable", "reason": reason, "rows": None,
                }
            return
        try:
            setup = sync.setup()
            if setup.status != "ok":
                for key in keys:
                    results[key] = _summary(setup)
                return
            enabled = enabled_task_map(self._store)
            for key in keys:
                if key in ATTENTION_TASKS:
                    enabled[key] = True
            watch = self._watch()
            for key in keys:
                self._set_current(job, key)
                envelope = sync.run_task(
                    key, enabled=enabled, watchlist=watch, timeout_ms=self._timeout_ms
                )
                results[key] = _summary(envelope)
        finally:
            close()

    def _set_current(self, job: dict[str, Any], key: str) -> None:
        with self._lock:
            job["current"] = key

    def _watch(self) -> list[str]:
        if self._watchlist is None:
            return []
        try:
            return list(self._watchlist())
        except Exception:      # noqa: BLE001 — 关注面读不出时按空集处理（已知空集，非缺失）
            return []

    # ───────────────────────── 真实抓取器（惰性导入） ─────────────────────────

    def _make_market(self, db: MarketDb) -> tuple[Any, Callable[[], None]]:
        if self._market_factory is not None:
            return self._market_factory(db, self._gateway)
        from st_agent.l0.market.live import BaoStockFetcher

        fetcher = BaoStockFetcher()
        fetcher.open()
        return BaoStockSync(db, self._gateway, fetcher), fetcher.close

    def _make_info(self, db: MarketDb) -> tuple[Any, Callable[[], None]]:
        if self._info_factory is not None:
            return self._info_factory(db, self._gateway)
        from st_agent.l0.info.fetch import HttpInfoFetcher

        fetcher = HttpInfoFetcher()
        return InfoSync(db, self._gateway, fetcher), lambda: None
