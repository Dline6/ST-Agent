"""手动同步与数据总览的执行面（[02 §5](../../../docs/技术架构-v2/02-L0-本地优先基座.md)；[D-129] / [D-130]）。

**只做 L0 的事**：读 ``sync_state`` / 表行数、按任务清单发起 ``BaoStockSync`` /
``InfoSync``，并把进度与逐任务结果留在内存。表现层经组合根的鸭子端口消费。

五条硬口径：

- **单飞**——``MarketDb.transact()`` 是「取整库工作副本 → 改 → 写回」，并发任务会互相
  覆盖；故同一时刻只允许一个批次，第二次 ``start`` 直接拒。
- **前置自动补齐**——行情任务的 ``needs``（日历 + 全量快照）与信息面任务依赖的
  ``security`` 主档由本模块自动补入，并在回报里标注 ``auto_added``，不静默多跑。
- **失败不连坐**——单个任务失败只记该任务的结果，其余任务继续；进程退出不保证长跑
  任务继续，下次按水位续跑。
- **范围不静默**——``[start, end]`` 只给 ``supports_range`` 的任务、``codes`` 只给
  ``per_code`` 的任务；其余任务在 ``preflight`` 与逐任务结果里显式回报忽略（[D-130]）。
- **可取消**——取消令牌在任务间与网关调用前后检查；终态 ``cancelled``，已完成结果保留。
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime
from typing import Any

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.info.sources import (
    ATTENTION_TASKS,
    INFO_TASK_KEYS,
    INFO_TASKS,
    get_info_task,
)
from st_agent.l0.info.switch_config import enabled_task_map
from st_agent.l0.info.sync import InfoSync
from st_agent.l0.market.db import MarketDb
from st_agent.l0.market.sync import DOMAIN_TASKS, RUN_ORDER, SOURCE_ID, BaoStockSync
from st_agent.l0.market.tasks import TASK_KEYS as MARKET_TASK_KEYS
from st_agent.l0.market.tasks import get_task

__all__ = ["DataSyncRunner", "SyncBusyError", "SyncJobError", "SCOPES"]

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

SCOPES: tuple[str, ...] = ("market", "st", "watchlist")
"""证券范围（[D-130]）：全市场 / 仅 ST 股票池 / 仅关注池。"""

_SCOPE_START_REQUIRED: frozenset[str] = frozenset({
    "bs_k_daily", "bs_k_period", "bs_k_minute", "bs_dividend",
    "bs_perf_express", "bs_forecast",
})
"""窗口按**全库水位**推导的任务：范围同步必须给显式起点，否则新纳入代码拿到空窗口。

``bs_adjust_factor``（基线起点）与 ``bs_fin_quarter``（固定 8 季度）不在此列——
它们的默认窗口对新增代码本就可取。
"""


class SyncJobError(ValueError):
    """同步请求非法（未知任务 / 空选择 / 范围参数不合法）。"""


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


def _validate_scope_args(
    scope: str, start: str | None, end: str | None
) -> str:
    """范围与窗口参数的形状校验（空串＝通过）。"""
    if scope not in SCOPES:
        return f"未知证券范围 {scope!r}；合法值 = {list(SCOPES)}"
    for label, value in (("起点", start), ("终点", end)):
        if value is None:
            continue
        try:
            date.fromisoformat(value)
        except ValueError:
            return f"显式窗口{label}须为 YYYY-MM-DD，实际 {value!r}"
    if start is not None and end is not None and start > end:
        return f"显式窗口起点 {start} 晚于终点 {end}"
    return ""


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
        self._cancel = threading.Event()

    # ───────────────────────── 只读总览 ─────────────────────────

    def overview(self) -> dict[str, Any]:
        """逐任务 / 逐表的数据现状（**一次**只读连接内取齐；[D-130]）。"""
        db = MarketDb(self._store)
        snapshot = db.overview_snapshot()
        states = {row["task_key"]: row for row in snapshot["states"]}
        counts: dict[str, int] = snapshot["counts"]
        tasks = self._task_rows(states)
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
        scope = self._scope_info(snapshot)
        summary = {
            "db_exists": db.exists(),
            "task_count": len(tasks),
            "ok_task_count": ok_count,
            "table_count": existing_table_count,
            "covered_table_count": len(tables),
            "total_rows": total_rows,
            "last_success_at": last_success,
            "scope": scope,
            "note": (
                "本地市场数据库尚未建立（首次同步未完成）"
                if not snapshot["counts"] and not snapshot["states"]
                else ""
            ),
        }
        return {
            "summary": summary,
            "tasks": tasks,
            "tables": tables,
            "scope": scope,
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
                "per_code": bool(spec.per_code),
                "supports_range": bool(spec.supports_range),
                "window_desc": spec.window_desc,
                "source_id": SOURCE_ID,
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
                "per_code": False,
                "supports_range": False,
                "window_desc": spec.window_desc
                or "按水位续跑（首次回看 90 天）；快照型按次追加",
                "source_id": spec.source_id,
                "status": state.get("last_status"),
                "watermark": state.get("watermark"),
                "last_success_at": state.get("last_success_at"),
                "last_error": state.get("last_error"),
                "registered": bool(state),
            })
        return rows

    def _scope_info(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        """三种证券范围的可选性与规模（ST 池 / 关注池 / 全市场；[D-130]）。"""
        st = snapshot.get("st") or {}
        watch = self._watch()
        return {
            "market": {
                "count": snapshot.get("listed_count"),
                "available": snapshot.get("listed_count") is not None,
                "as_of": None,
                "reason": "",
            },
            "st": {
                "count": int(st.get("count") or 0),
                "available": bool(st.get("available")),
                "as_of": st.get("as_of"),
                "reason": st.get("reason") or "",
            },
            "watchlist": {
                "count": len(watch),
                "available": bool(watch),
                "as_of": None,
                "reason": "" if watch else "关注池为空（持仓 + 关注均无证券）",
            },
        }

    # ───────────────────────── 预检与发批 ─────────────────────────

    def preflight(
        self,
        task_keys: Iterable[str],
        *,
        start: str | None = None,
        end: str | None = None,
        scope: str = "market",
    ) -> dict[str, Any]:
        """发起前的计划与警告（不写库、不起线程；[D-130]）。

        返回 ``order`` / ``auto_added`` / ``scope`` / ``range_applied`` /
        ``range_ignored`` / ``scope_ignored`` / ``warnings`` / ``errors``。
        ``errors`` 非空时 ``start`` 会拒绝同样的请求；界面据此禁用开始按钮。
        """
        keys = self._normalize_keys(task_keys)
        errors: list[str] = []
        problem = _validate_scope_args(scope, start, end)
        if problem:
            errors.append(problem)
        db = MarketDb(self._store)
        snapshot = db.preflight_snapshot()
        states = {row["task_key"]: row for row in snapshot["states"]}

        codes: list[str] | None = None
        scope_detail: dict[str, Any] = {"scope": scope, "count": None}
        if not problem:
            try:
                codes, scope_detail = self._resolve_scope(scope, db, snapshot)
            except SyncJobError as exc:
                errors.append(str(exc))

        try:
            order, auto_added = self._plan(keys, db)
        except SyncJobError as exc:
            errors.append(str(exc))
            order, auto_added = [], []
        requires_start = bool(
            order and scope != "market"
            and any(key in _SCOPE_START_REQUIRED for key in order)
        )
        if requires_start and start is None:
            required = [key for key in order if key in _SCOPE_START_REQUIRED]
            errors.append(
                "范围同步需指定起始日期：" + "、".join(required)
                + " 的窗口按全库水位推导，不带起点会让新纳入的证券拿到空窗口"
            )

        range_applied = [
            key for key in order
            if key in MARKET_TASK_KEYS and get_task(key).supports_range
            and (start is not None or end is not None)
        ]
        range_ignored = [
            key for key in order if (start is not None or end is not None)
            and (key in INFO_TASK_KEYS
                 or (key in MARKET_TASK_KEYS and not get_task(key).supports_range))
        ]
        scope_applied = [
            key for key in order
            if codes is not None and key in MARKET_TASK_KEYS and get_task(key).per_code
        ]
        scope_ignored = [
            key for key in order
            if codes is not None
            and (key in INFO_TASK_KEYS
                 or (key in MARKET_TASK_KEYS and not get_task(key).per_code))
        ]

        warnings: list[dict[str, Any]] = []
        first_full = [
            key for key in order if _is_first_full(key, states.get(key))
        ]
        if first_full:
            warnings.append({"kind": "first_full", "task_keys": first_full})
        disabled = [
            key for key in keys
            if key in MARKET_TASK_KEYS and get_task(key).default_disabled
        ]
        if disabled:
            warnings.append({"kind": "default_disabled", "task_keys": disabled})
        backups = [
            key for key in order
            if key in INFO_TASK_KEYS and get_info_task(key).role == "backup"
        ]
        if backups:
            warnings.append({"kind": "backup", "task_keys": backups})
        if range_ignored:
            warnings.append({"kind": "range_ignored", "task_keys": range_ignored})
        if scope_ignored:
            warnings.append({"kind": "scope_ignored", "task_keys": scope_ignored})

        return {
            "selected": keys,
            "order": order,
            "auto_added": auto_added,
            "scope": scope_detail,
            "range": {"start": start, "end": end},
            "requires_start": requires_start,
            "range_applied": range_applied,
            "range_ignored": range_ignored,
            "scope_applied": scope_applied,
            "scope_ignored": scope_ignored,
            "warnings": warnings,
            "errors": errors,
        }

    def start(
        self,
        task_keys: Iterable[str],
        *,
        start: str | None = None,
        end: str | None = None,
        scope: str = "market",
    ) -> dict[str, Any]:
        """发起一个批次；返回 ``{job_id, order, auto_added, scope, ...}``（异步执行）。"""
        keys = self._normalize_keys(task_keys)
        problem = _validate_scope_args(scope, start, end)
        if problem:
            raise SyncJobError(problem)
        with self._lock:
            if self._job is not None and self._job["state"] == "running":
                raise SyncBusyError(
                    f"已有同步批次在跑（{self._job['job_id']}）；请等它结束或取消"
                )
            db = MarketDb(self._store)
            codes, scope_detail = self._resolve_scope(scope, db)
            order, auto_added = self._plan(keys, db)
            if scope != "market" and start is None:
                required = [key for key in order if key in _SCOPE_START_REQUIRED]
                if required:
                    raise SyncJobError(
                        "范围同步需指定起始日期：" + "、".join(required)
                        + " 的窗口按全库水位推导，不带起点会让新纳入的证券拿到空窗口"
                    )
            plan = self._build_plan(order, codes, start, end)
            range_requested = start is not None or end is not None
            range_ignored = [
                item["key"] for item in plan
                if range_requested and (
                    item["kind"] == "info"
                    or not get_task(item["key"]).supports_range
                )
            ]
            scope_ignored = [
                item["key"] for item in plan
                if codes is not None and (
                    item["kind"] == "info"
                    or not get_task(item["key"]).per_code
                )
            ]
            self._cancel.clear()
            job = {
                "job_id": uuid.uuid4().hex[:12],
                "state": "running",
                "selected": keys,
                "order": order,
                "auto_added": auto_added,
                "scope": scope_detail,
                "start": start,
                "end": end,
                "codes_count": len(codes) if codes is not None else None,
                "range_ignored": range_ignored,
                "scope_ignored": scope_ignored,
                "results": {},
                "current": None,
                "phase": "preparing",
                "cancel_requested": False,
                "started_at": _now_text(),
                "finished_at": None,
                "error": "",
            }
            self._job = job
            thread = threading.Thread(
                target=self._run, args=(job, plan), name="st-agent-data-sync", daemon=True
            )
            self._thread = thread
            thread.start()
        return {
            "job_id": job["job_id"],
            "order": order,
            "auto_added": auto_added,
            "scope": scope_detail,
            "start": start,
            "end": end,
            "codes_count": job["codes_count"],
            "range_ignored": range_ignored,
            "scope_ignored": scope_ignored,
        }

    def cancel(self) -> dict[str, Any]:
        """请求停止当前批次（任务间与网关调用前后生效；不是瞬时中断）。"""
        with self._lock:
            if self._job is None or self._job["state"] != "running":
                return {
                    "cancelled": False,
                    "job_id": self._job["job_id"] if self._job else None,
                    "state": self._job["state"] if self._job else None,
                    "reason": "当前没有运行中的批次",
                }
            self._cancel.set()
            self._job["cancel_requested"] = True
            return {
                "cancelled": True,
                "job_id": self._job["job_id"],
                "state": self._job["state"],
                "note": "已请求停止：当前请求结束后停止",
            }

    def job(self) -> dict[str, Any] | None:
        """当前 / 最近一次批次的快照（无批次 → ``None``）。"""
        with self._lock:
            if self._job is None:
                return None
            snapshot = dict(self._job)
            snapshot["results"] = dict(self._job["results"])
            return snapshot

    # ───────────────────────── 计划 ─────────────────────────

    def _normalize_keys(self, task_keys: Iterable[str]) -> list[str]:
        keys = list(dict.fromkeys(str(item).strip() for item in task_keys if str(item).strip()))
        if not keys:
            raise SyncJobError("未选择任何同步任务")
        unknown = [key for key in keys if key not in MARKET_TASK_KEYS and key not in INFO_TASK_KEYS]
        if unknown:
            raise SyncJobError(f"未知同步任务：{', '.join(unknown)}")
        return keys

    def _resolve_scope(
        self, scope: str, db: MarketDb, snapshot: Mapping[str, Any] | None = None
    ) -> tuple[list[str] | None, dict[str, Any]]:
        """把范围解析成代码集；``market`` → ``None``（全市场，不传 codes）。"""
        if scope == "market":
            count = (
                snapshot.get("listed_count") if snapshot is not None
                else db.listed_stock_count()
            )
            return None, {
                "scope": "market", "count": count, "as_of": None, "reason": "",
            }
        if scope == "st":
            st = (snapshot or {}).get("st")
            if not st:
                st = db.st_pool()
            if not st.get("available"):
                raise SyncJobError(f"ST 范围不可用：{st.get('reason') or ''}")
            if not st.get("count"):
                raise SyncJobError(st.get("reason") or "ST 股票池为空")
            codes = list(st.get("codes") or [])
            if not codes:          # 快照未带代码集（旧调用）→ 再读一次
                codes = db.st_codes()
            return codes, {
                "scope": "st", "count": int(st.get("count") or len(codes)),
                "as_of": st.get("as_of"), "reason": "",
            }
        if scope == "watchlist":
            codes = self._watch()
            if not codes:
                raise SyncJobError("关注池为空（持仓 + 关注均无证券）")
            return codes, {
                "scope": "watchlist", "count": len(codes), "as_of": None, "reason": "",
            }
        raise SyncJobError(f"未知证券范围 {scope!r}；合法值 = {list(SCOPES)}")

    def _build_plan(
        self, order: list[str], codes: list[str] | None,
        start: str | None, end: str | None,
    ) -> list[dict[str, Any]]:
        """逐任务参数：区间只给窗口型任务，代码集只给逐码任务（[D-130]）。"""
        plan: list[dict[str, Any]] = []
        for key in order:
            if key in MARKET_TASK_KEYS:
                spec = get_task(key)
                plan.append({
                    "key": key,
                    "kind": "market",
                    "codes": list(codes) if (codes is not None and spec.per_code) else None,
                    "start": start if spec.supports_range else None,
                    "end": end if spec.supports_range else None,
                })
            else:
                plan.append({"key": key, "kind": "info"})
        return plan

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

    def _run(self, job: dict[str, Any], plan: list[dict[str, Any]]) -> None:
        results: dict[str, dict[str, Any]] = {}
        db = MarketDb(self._store)
        cancelled = False
        try:
            cancelled = self._run_market(job, db, plan, results)
            if not cancelled:
                cancelled = self._run_info(job, db, plan, results)
        except Exception as exc:        # noqa: BLE001 —— 批次级异常如实落到 job
            job["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            for item in plan:
                key = item["key"]
                if key not in results:
                    results[key] = {
                        "status": "cancelled" if cancelled else "failed",
                        "reason": (
                            "批次已取消" if cancelled
                            else (job["error"] or "批次提前结束，未执行")
                        ),
                        "rows": None,
                    }
            with self._lock:
                job["results"] = results
                job["current"] = None
                job["phase"] = "finished"
                job["finished_at"] = _now_text()
                job["state"] = (
                    "cancelled" if cancelled
                    else ("failed" if job["error"] else "done")
                )

    def _run_market(
        self, job: dict[str, Any], db: MarketDb, plan: list[dict[str, Any]],
        results: dict[str, dict[str, Any]],
    ) -> bool:
        """跑行情项；返回是否因取消而提前结束。"""
        items = [item for item in plan if item["kind"] == "market"]
        if not items:
            return False
        self._set_phase(job, "connecting")
        try:
            sync, close = self._make_market(db)
        except Exception as exc:        # noqa: BLE001 — 抓取器缺席 / 登录失败
            reason = str(exc) or type(exc).__name__
            for item in items:
                results[item["key"]] = {
                    "status": "unavailable", "reason": reason, "rows": None,
                }
            return False
        try:
            setup = sync.setup()
            if setup.status != "ok":
                for item in items:
                    results[item["key"]] = _summary(setup)
                return False
            self._set_phase(job, "running")
            watch = self._watch()
            enabled = {item["key"]: True for item in items}
            for item in items:
                if self._cancel.is_set():
                    return True
                self._set_current(job, item["key"])
                started = _now_text()
                began = time.monotonic()
                envelope = sync.run_task(
                    item["key"], enabled=enabled, watchlist=watch,
                    codes=item.get("codes"), start=item.get("start"),
                    end=item.get("end"), timeout_ms=self._timeout_ms,
                    cancel=self._cancel,
                )
                summary = _summary(envelope)
                summary.update(_timing(started, began, item))
                results[item["key"]] = summary
            return False
        finally:
            close()

    def _run_info(
        self, job: dict[str, Any], db: MarketDb, plan: list[dict[str, Any]],
        results: dict[str, dict[str, Any]],
    ) -> bool:
        """跑信息面项；返回是否因取消而提前结束。范围 / 区间对它不适用。"""
        items = [item for item in plan if item["kind"] == "info"]
        if not items:
            return False
        self._set_phase(job, "connecting")
        try:
            sync, close = self._make_info(db)
        except Exception as exc:        # noqa: BLE001
            reason = str(exc) or type(exc).__name__
            for item in items:
                results[item["key"]] = {
                    "status": "unavailable", "reason": reason, "rows": None,
                }
            return False
        try:
            setup = sync.setup()
            if setup.status != "ok":
                for item in items:
                    results[item["key"]] = _summary(setup)
                return False
            self._set_phase(job, "running")
            enabled = enabled_task_map(self._store)
            for item in items:
                if item["key"] in ATTENTION_TASKS:
                    enabled[item["key"]] = True
            watch = self._watch()
            for item in items:
                if self._cancel.is_set():
                    return True
                self._set_current(job, item["key"])
                started = _now_text()
                began = time.monotonic()
                envelope = sync.run_task(
                    item["key"], enabled=enabled, watchlist=watch,
                    timeout_ms=self._timeout_ms, cancel=self._cancel,
                )
                summary = _summary(envelope)
                summary.update(_timing(started, began, item))
                results[item["key"]] = summary
            return False
        finally:
            close()

    def _set_current(self, job: dict[str, Any], key: str) -> None:
        with self._lock:
            job["current"] = key

    def _set_phase(self, job: dict[str, Any], phase: str) -> None:
        """批次阶段（``preparing`` / ``connecting`` / ``running`` / ``finished``）。"""
        with self._lock:
            job["phase"] = phase

    def _watch(self) -> list[str]:
        if self._watchlist is None:
            return []
        try:
            # 持仓 + 关注可能重叠；逐码任务不应重复抓同一只码，故按首次出现去重。
            return list(dict.fromkeys(self._watchlist()))
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


def _timing(
    started: str, began: float, item: Mapping[str, Any]
) -> dict[str, Any]:
    """逐任务耗时与本次实际生效的范围 / 区间（[D-130]）。"""
    return {
        "started_at": started,
        "finished_at": _now_text(),
        "duration_ms": int((time.monotonic() - began) * 1000),
        "applied_range": bool(item.get("start") or item.get("end")),
        "applied_codes": len(item["codes"]) if item.get("codes") else None,
    }


def _is_first_full(
    key: str, state: Mapping[str, Any] | None
) -> bool:
    """该任务本次是否属「首次全量 / 首次回看」（无行、无水位、无成功时间）。"""
    if key in MARKET_TASK_KEYS:
        mode = get_task(key).mode
        if mode == "snapshot":
            return False
    else:
        mode = "info"
    if not state:
        return True
    return not state.get("watermark") and not state.get("last_success_at")
