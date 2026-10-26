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

import json
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime, timedelta
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
from st_agent.l0.market.datasets import (
    dataset_for_task,
    get_dataset,
    normalize_groups,
)
from st_agent.l0.market.sync import (
    DOMAIN_TASKS,
    RUN_ORDER,
    SOURCE_ID,
    BaoStockSync,
    estimate_requests,
)
from st_agent.l0.market.targets import TARGETS, resolve_targets
from st_agent.l0.market.tasks import TASK_KEYS as MARKET_TASK_KEYS
from st_agent.l0.market.tasks import get_task, last_n_quarters

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
"""证券范围（[D-130]）：全市场 / 仅 ST 股票池 / 仅关注池（旧接口适配用）。"""

DEFAULT_DAILY_BUDGET = 40_000
"""源端日请求预算提示值（超限只警告不拦，[D-133]）。"""


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


def _today() -> date:
    return datetime.now().astimezone().date()


def _quarter_start(unit: str) -> str:
    year_text, _, quarter_text = unit.partition("Q")
    try:
        year, quarter = int(year_text), int(quarter_text)
    except (TypeError, ValueError):
        raise SyncJobError(f"季度须为 YYYYQn，实际 {unit!r}") from None
    if quarter not in (1, 2, 3, 4):
        raise SyncJobError(f"非法季度 {unit!r}")
    return f"{year}-{(quarter - 1) * 3 + 1:02d}-01"


def _quarter_end(unit: str) -> str:
    year_text, _, quarter_text = unit.partition("Q")
    try:
        year, quarter = int(year_text), int(quarter_text)
    except (TypeError, ValueError):
        raise SyncJobError(f"季度须为 YYYYQn，实际 {unit!r}") from None
    if quarter not in (1, 2, 3, 4):
        raise SyncJobError(f"非法季度 {unit!r}")
    return f"{year}-{quarter * 3:02d}-{'31' if quarter in (1, 4) else '30'}"


def _check_date(value: str | None, label: str) -> str | None:
    if value in (None, ""):
        return None
    try:
        date.fromisoformat(str(value))
    except ValueError:
        raise SyncJobError(f"{label}须为 YYYY-MM-DD，实际 {value!r}") from None
    return str(value)


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
        self._schema_ready = False

    def _ensure_schema(self, db: MarketDb) -> None:
        """旧库读面前补齐新表/视图（每个 Runner 实例至多一次）。"""
        if self._schema_ready:
            return
        if db.exists():
            db.ensure_schema()
        self._schema_ready = True

    # ───────────────────────── 只读总览 ─────────────────────────

    def overview(self) -> dict[str, Any]:
        """逐任务 / 逐表的数据现状（**一次**只读连接内取齐；[D-130]）。"""
        db = MarketDb(self._store)
        self._ensure_schema(db)
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
        targets = {
            key: pool.payload()
            for key, pool in resolve_targets(db, watchlist=self._watch).items()
        }
        summary = {
            "db_exists": db.exists(),
            "task_count": len(tasks),
            "ok_task_count": ok_count,
            "table_count": existing_table_count,
            "covered_table_count": len(tables),
            "total_rows": total_rows,
            "last_success_at": last_success,
            "scope": scope,
            "targets": targets,
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
            "targets": targets,
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
        """三种证券范围的可选性与规模（旧接口适配；新 UI 用 ``targets``）。"""
        st = snapshot.get("st") or {}
        st_sync = snapshot.get("st_sync") or {}
        watch = self._watch()
        return {
            "market": {
                "count": snapshot.get("listed_count"),
                "available": snapshot.get("listed_count") is not None,
                "as_of": None,
                "reason": "",
            },
            "st": {
                "count": int(st_sync.get("count") or st.get("count") or 0),
                "available": bool(st_sync.get("available") or st.get("available")),
                "as_of": st_sync.get("as_of") or st.get("as_of"),
                "reason": st_sync.get("reason") or st.get("reason") or "",
            },
            "watchlist": {
                "count": len(watch),
                "available": bool(watch),
                "as_of": None,
                "reason": "" if watch else "关注池为空（持仓 + 关注均无证券）",
            },
        }

    # ───────────────────────── 预检与发批 ─────────────────────────

    # ───────────────────────── 标的 × 数据集 预检与发批（D-132） ─────────────────────────

    def _normalize_items(self, items: Any) -> list[dict[str, Any]]:
        if not isinstance(items, list) or not items:
            raise SyncJobError("未选择任何同步数据集")
        out: list[dict[str, Any]] = []
        for index, raw in enumerate(items):
            if not isinstance(raw, Mapping):
                raise SyncJobError(f"第 {index + 1} 项不是对象")
            dataset = str(raw.get("dataset") or "").strip()
            if not dataset:
                raise SyncJobError(f"第 {index + 1} 项缺少 dataset")
            spec = get_dataset(dataset)
            target = str(raw.get("target") or (
                "other" if spec.target_kind == "global" else "all_a"
            )).strip()
            if target not in {t.target_id for t in TARGETS}:
                raise SyncJobError(f"未知标的 {target!r}")
            groups = normalize_groups(dataset, raw.get("groups"))
            time = raw.get("time") or spec.default_time
            resolved = self._resolve_time(spec, time)
            out.append({
                "item_id": str(raw.get("item_id") or f"item-{index + 1}"),
                "target": target,
                "dataset": dataset,
                "groups": list(groups),
                "time": resolved,
                "force_refresh": bool(raw.get("force_refresh", False)),
            })
        return out

    def _resolve_time(self, spec: Any, time: Any) -> dict[str, Any]:
        if not isinstance(time, Mapping):
            raise SyncJobError(f"数据集 {spec.dataset_id} 的时间参数不是对象")
        kind = str(time.get("kind") or spec.time_kind)
        if kind != spec.time_kind:
            raise SyncJobError(
                f"数据集 {spec.dataset_id} 的时间类型应为 {spec.time_kind}，实际 {kind!r}"
            )
        today = _today()
        if kind == "none":
            return {"kind": "none"}
        if kind == "date_range":
            mode = str(time.get("mode") or "incremental")
            if mode == "incremental":
                return {"kind": kind, "mode": mode, "start": None, "end": None}
            if mode in ("last_years", "last_days"):
                unit = 365 if mode == "last_years" else 1
                count = max(1, int(time.get("n") or 1))
                start = (today - timedelta(days=unit * count)).isoformat()
                return {"kind": kind, "mode": mode, "start": start,
                        "end": today.isoformat()}
            if mode == "full":
                return {"kind": kind, "mode": mode,
                        "start": "1990-01-01", "end": today.isoformat()}
            if mode == "today":
                return {"kind": kind, "mode": mode,
                        "start": today.isoformat(), "end": today.isoformat()}
            if mode == "custom":
                start = _check_date(time.get("start"), "起始日期")
                end = _check_date(time.get("end"), "结束日期")
                if start and end and start > end:
                    raise SyncJobError(f"起始日期 {start} 晚于结束日期 {end}")
                return {"kind": kind, "mode": mode, "start": start, "end": end}
            raise SyncJobError(f"未知日期模式 {mode!r}")
        if kind == "quarter_range":
            mode = str(time.get("mode") or "last_n")
            if mode == "last_n":
                n = max(1, int(time.get("n") or 8))
                plan = last_n_quarters(n, today)
                return {
                    "kind": kind, "mode": mode, "quarters": n,
                    "start": _quarter_start(f"{plan[0][0]}Q{plan[0][1]}"),
                    "end": _quarter_end(f"{plan[-1][0]}Q{plan[-1][1]}"),
                }
            if mode == "range":
                first = str(time.get("from") or "")
                last = str(time.get("to") or "")
                start = _quarter_start(first)
                end = _quarter_end(last)
                if start > end:
                    raise SyncJobError(f"季度起点 {first} 晚于终点 {last}")
                return {"kind": kind, "mode": mode, "start": start, "end": end,
                        "quarters": None}
            raise SyncJobError(f"未知季度模式 {mode!r}")
        if kind == "year_range":
            mode = str(time.get("mode") or "last_n")
            if mode == "last_n":
                n = max(1, int(time.get("n") or 3))
                return {
                    "kind": kind, "mode": mode,
                    "start": f"{today.year - n + 1}-01-01",
                    "end": f"{today.year}-12-31",
                }
            if mode == "range":
                try:
                    first = int(time.get("from"))
                    last = int(time.get("to"))
                except (TypeError, ValueError):
                    raise SyncJobError("年份区间须给起止年份（整数）") from None
                if first > last:
                    raise SyncJobError(f"年份起点 {first} 晚于终点 {last}")
                return {"kind": kind, "mode": mode,
                        "start": f"{first}-01-01", "end": f"{last}-12-31"}
            raise SyncJobError(f"未知年份模式 {mode!r}")
        raise SyncJobError(f"未知时间类型 {kind!r}")

    def _group_items(
        self, normalized: list[dict[str, Any]], targets: Mapping[str, Any]
    ) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
        grouped: dict[tuple, dict[str, Any]] = {}
        errors: list[str] = []
        raw_codes = 0
        for item in normalized:
            spec = get_dataset(item["dataset"])
            pool = targets[item["target"]]
            if spec.target_kind == "target":
                if item["target"] == "other":
                    errors.append(f"{spec.label} 需要证券标的，不能放在「其他」")
                    continue
                if not pool.available or not pool.codes:
                    errors.append(
                        f"{spec.label} 的标的 {pool.label} 不可用："
                        f"{pool.reason or '代码集为空'}"
                    )
                    continue
                codes = set(pool.codes)
                raw_codes += len(codes)
            else:
                codes = None
            key = (
                item["dataset"],
                json.dumps(item["time"], sort_keys=True, ensure_ascii=False),
                ",".join(item["groups"]),
            )
            group = grouped.get(key)
            if group is None:
                group = {
                    "group_id": f"g{len(grouped) + 1}",
                    "dataset": item["dataset"],
                    "targets": set(),
                    "codes": set(),
                    "groups": item["groups"],
                    "time": item["time"],
                    "force_refresh": False,
                }
                grouped[key] = group
            if codes is not None:
                group["codes"].update(codes)
            group["targets"].add(item["target"])
            group["force_refresh"] = (
                group["force_refresh"] or item["force_refresh"]
            )
        groups = list(grouped.values())
        union_codes = sum(len(g["codes"]) for g in groups if g["codes"])
        overlap = {
            "raw_code_count": raw_codes,
            "union_code_count": union_codes,
            "duplicate_code_count": max(0, raw_codes - union_codes),
        }
        return groups, errors, overlap

    def _auto_plan(
        self, groups: list[dict[str, Any]], targets: Mapping[str, Any], db: MarketDb
    ) -> tuple[list[str], list[str]]:
        task_keys: set[str] = set()
        for group in groups:
            task_keys.update(get_dataset(group["dataset"]).task_keys)
        auto: list[str] = []
        selected = {target for group in groups for target in group["targets"]}
        if "st" in selected:
            pool = targets["st"]
            stale = False
            if pool.as_of:
                try:
                    stale = date.fromisoformat(pool.as_of[:10]) < _today() - timedelta(days=7)
                except ValueError:
                    stale = True
            if (not pool.available or stale) and "bs_st_list" not in task_keys:
                task_keys.add("bs_st_list")
                auto.append("bs_st_list")
        for index_key in ("hs300", "sz50", "zz500"):
            if index_key in selected and not targets[index_key].codes:
                key = f"bs_{index_key}"
                if key not in task_keys:
                    task_keys.add(key)
                    auto.append(key)
        pending = [key for key in task_keys if key in MARKET_TASK_KEYS]
        while pending:
            key = pending.pop()
            for need in get_task(key).needs:
                if need not in task_keys:
                    task_keys.add(need)
                    auto.append(need)
                    pending.append(need)
        if any(key in INFO_TASK_KEYS for key in task_keys) and self._master_missing(db):
            for key in _MASTER_TASKS:
                if key not in task_keys:
                    task_keys.add(key)
                    auto.append(key)
        order = [key for key in RUN_ORDER if key in task_keys]
        order.extend(spec.task_key for spec in INFO_TASKS if spec.task_key in task_keys)
        return order, list(dict.fromkeys(auto))

    def _estimate_groups(
        self, groups: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], dict[str, int]]:
        by_group: dict[str, int] = {}
        by_dataset: dict[str, int] = {}
        total = 0
        for group in groups:
            count = len(group["codes"]) if group["codes"] else 1
            quarters = group["time"].get("quarters") or 8
            requests = 0
            for key in get_dataset(group["dataset"]).task_keys:
                requests += estimate_requests(
                    key, code_count=count, quarters=quarters
                )
            by_group[group["group_id"]] = requests
            by_dataset[group["dataset"]] = (
                by_dataset.get(group["dataset"], 0) + requests
            )
            total += requests
        return {
            "requests": total,
            "budget": DEFAULT_DAILY_BUDGET,
            "over_budget": total > DEFAULT_DAILY_BUDGET,
            "by_dataset": by_dataset,
        }, by_group

    def preflight_items(
        self, items: Any, *, custom_text: str = ""
    ) -> dict[str, Any]:
        """按 target × dataset × groups × time 的预检（不写库、不起线程）。"""
        db = MarketDb(self._store)
        self._ensure_schema(db)
        targets = resolve_targets(
            db, watchlist=self._watch, custom_text=custom_text
        )
        errors: list[str] = []
        warnings: list[dict[str, Any]] = []
        try:
            normalized = self._normalize_items(items)
        except SyncJobError as exc:
            return {
                "items": [], "targets": {k: v.payload() for k, v in targets.items()},
                "plan": [], "order": [], "auto_added": [], "overlap": {},
                "estimate": {}, "warnings": [], "errors": [str(exc)],
            }
        groups, group_errors, overlap = self._group_items(normalized, targets)
        errors.extend(group_errors)
        order, auto = self._auto_plan(groups, targets, db)
        estimate, by_group = self._estimate_groups(groups)
        states = {
            row["task_key"]: row
            for row in db.preflight_snapshot()["states"]
        }
        first_full = [key for key in order if _is_first_full(key, states.get(key))]
        if first_full:
            warnings.append({"kind": "first_full", "task_keys": first_full})
        disabled = [
            key for key in order
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
        if overlap.get("duplicate_code_count"):
            warnings.append({"kind": "overlap", **overlap})
        selected_targets = {item["target"] for item in normalized}
        for pool in targets.values():
            if pool.target_id == "other" or pool.target_id not in selected_targets:
                continue
            if not pool.available:
                warnings.append({
                    "kind": "target_unavailable",
                    "target": pool.target_id,
                    "reason": pool.reason,
                })
        if estimate.get("over_budget"):
            warnings.append({"kind": "over_budget", **estimate})
        plan = [
            {
                "group_id": group["group_id"],
                "dataset": group["dataset"],
                "label": get_dataset(group["dataset"]).label,
                "targets": sorted(group["targets"]),
                "codes_count": len(group["codes"]) if group["codes"] else None,
                "groups": list(group["groups"]),
                "time": group["time"],
                "task_keys": list(get_dataset(group["dataset"]).task_keys),
                "planned_requests": by_group.get(group["group_id"], 0),
                "force_refresh": group["force_refresh"],
            }
            for group in groups
        ]
        return {
            "items": normalized,
            "targets": {key: pool.payload() for key, pool in targets.items()},
            "plan": plan,
            "order": order,
            "auto_added": auto,
            "overlap": overlap,
            "estimate": estimate,
            "warnings": warnings,
            "errors": errors,
        }

    def start_items(self, items: Any, *, custom_text: str = "") -> dict[str, Any]:
        """按 target × dataset × groups × time 发起批次。"""
        preflight = self.preflight_items(items, custom_text=custom_text)
        if preflight["errors"]:
            raise SyncJobError("；".join(preflight["errors"]))
        normalized = preflight["items"]
        targets = resolve_targets(
            MarketDb(self._store), watchlist=self._watch, custom_text=custom_text
        )
        groups, _, _ = self._group_items(normalized, targets)
        order = preflight["order"]
        auto = preflight["auto_added"]
        execution: list[dict[str, Any]] = []
        for key in order:
            matched = [
                group for group in groups
                if key in get_dataset(group["dataset"]).task_keys
            ]
            if not matched:
                execution.append({
                    "key": key,
                    "kind": "market" if key in MARKET_TASK_KEYS else "info",
                    "result_key": key,
                })
                continue
            for group in matched:
                result_key = f"{group['group_id']}:{key}"
                if key in MARKET_TASK_KEYS:
                    task = get_task(key)
                    execution.append({
                        "key": key,
                        "kind": "market",
                        "result_key": result_key,
                        "group_id": group["group_id"],
                        "codes": sorted(group["codes"]) if (
                            task.per_code and group["codes"]
                        ) else None,
                        "start": group["time"].get("start") if task.supports_range else None,
                        "end": group["time"].get("end") if task.supports_range else None,
                        "quarters": group["time"].get("quarters"),
                        "groups": list(group["groups"]),
                        "force_refresh": group["force_refresh"],
                    })
                else:
                    execution.append({
                        "key": key, "kind": "info", "result_key": result_key,
                        "group_id": group["group_id"],
                    })
        with self._lock:
            if self._job is not None and self._job["state"] == "running":
                raise SyncBusyError(
                    f"已有同步批次在跑（{self._job['job_id']}）；请等它结束或取消"
                )
            self._cancel.clear()
            job = {
                "job_id": uuid.uuid4().hex[:12],
                "state": "running",
                "items": normalized,
                "groups": preflight["plan"],
                "order": order,
                "auto_added": auto,
                "targets": preflight["targets"],
                "overlap": preflight["overlap"],
                "estimate": preflight["estimate"],
                "results": {},
                "current": None,
                "phase": "preparing",
                "progress": {
                    "group_index": 0, "group_total": len(groups),
                    "unit_done": 0, "unit_total": None,
                    "requests": 0, "rows": 0, "eta_seconds": None,
                },
                "cancel_requested": False,
                "started_at": _now_text(),
                "finished_at": None,
                "error": "",
            }
            self._job = job
            thread = threading.Thread(
                target=self._run, args=(job, execution),
                name="st-agent-data-sync", daemon=True,
            )
            self._thread = thread
            thread.start()
        return {
            "job_id": job["job_id"],
            "order": order,
            "auto_added": auto,
            "groups": preflight["plan"],
            "targets": preflight["targets"],
            "overlap": preflight["overlap"],
            "estimate": preflight["estimate"],
        }

    def preflight(
        self,
        task_keys: Iterable[str],
        *,
        start: str | None = None,
        end: str | None = None,
        scope: str = "market",
    ) -> dict[str, Any]:
        """旧接口适配：task_keys + 全局 scope/区间 → 新 items 预检。"""
        return self.preflight_items(
            self._legacy_items(task_keys, start=start, end=end, scope=scope)
        )

    def start(
        self,
        task_keys: Iterable[str],
        *,
        start: str | None = None,
        end: str | None = None,
        scope: str = "market",
    ) -> dict[str, Any]:
        """旧接口适配：task_keys + 全局 scope/区间 → 新 items 批次。"""
        problem = _validate_scope_args(scope, start, end)
        if problem:
            raise SyncJobError(problem)
        return self.start_items(
            self._legacy_items(task_keys, start=start, end=end, scope=scope)
        )

    def _legacy_items(
        self,
        task_keys: Iterable[str],
        *,
        start: str | None = None,
        end: str | None = None,
        scope: str = "market",
    ) -> list[dict[str, Any]]:
        """把旧的 ``task_keys + scope + [start,end]`` 翻译成 items（过渡适配）。"""
        target = {"market": "all_a", "st": "st", "watchlist": "watchlist"}.get(
            scope, "all_a"
        )
        items: list[dict[str, Any]] = []
        for key in self._normalize_keys(task_keys):
            try:
                spec = dataset_for_task(key)
            except ValueError:
                continue
            time: dict[str, Any] = dict(spec.default_time)
            if (start or end) and spec.time_kind == "date_range":
                time = {"kind": "date_range", "mode": "custom",
                        "start": start, "end": end}
            elif (start or end) and spec.time_kind == "quarter_range":
                first = date.fromisoformat(start or end)
                last = date.fromisoformat(end or start)
                time = {
                    "kind": "quarter_range", "mode": "range",
                    "from": f"{first.year}Q{(first.month - 1) // 3 + 1}",
                    "to": f"{last.year}Q{(last.month - 1) // 3 + 1}",
                }
            elif (start or end) and spec.time_kind == "year_range":
                first = date.fromisoformat(start or end)
                last = date.fromisoformat(end or start)
                time = {"kind": "year_range", "mode": "range",
                        "from": first.year, "to": last.year}
            items.append({
                "target": target if spec.target_kind == "target" else "other",
                "dataset": spec.dataset_id,
                "groups": None,
                "time": time,
            })
        return items

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

    def clear_market(self) -> dict[str, Any]:
        """一级清空市场数据（[D-133]）：只删 ``market.db`` 与``info`` 正文缓存。

        保留其余分区与 ``data_cache`` 内其他文件；运行中批次拒绝。清空后
        下一次 ``setup()`` 会重新建库，不需要重启进程。
        """
        with self._lock:
            if self._job is not None and self._job["state"] == "running":
                raise SyncBusyError(
                    f"同步批次运行中（{self._job['job_id']}）；请先停止或等待"
                )
            removed: list[str] = []
            for name in self._store.list_files("data_cache"):
                if name == "market.db" or name == "info" or name.startswith("info/"):
                    self._store.delete("data_cache", name)
                    removed.append(name)
            self._job = None
            self._thread = None
            self._schema_ready = False
            return {
                "removed_count": len(removed),
                "removed": removed,
                "kept": [
                    "memory", "config", "chat_history", "execution_log",
                    "reflection", "secrets",
                ],
                "data_cache_kept": [
                    name for name in self._store.list_files("data_cache")
                ],
                "db_exists": False,
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
                key = item.get("result_key") or item["key"]
                if key not in results:
                    results[key] = {
                        "status": "cancelled" if cancelled else "failed",
                        "reason": (
                            "批次已取消" if cancelled
                            else (job["error"] or "批次提前结束，未执行")
                        ),
                        "rows": None,
                        "task_key": item["key"],
                        "group_id": item.get("group_id", ""),
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

    def _progress_callback(self, job: dict[str, Any], item: Mapping[str, Any]):
        """L0 进度 → 批次快照（节流 500ms，只含计数/标签）。"""
        started = time.monotonic()
        last = {"at": 0.0}

        def callback(payload: dict[str, Any]) -> None:
            now = time.monotonic()
            if now - last["at"] < 0.5:
                return
            last["at"] = now
            done = int(payload.get("unit_done") or 0)
            total = payload.get("unit_total")
            eta = None
            if isinstance(total, int) and total > 0 and done >= 10:
                eta = int((now - started) / done * max(0, total - done))
            with self._lock:
                progress = dict(job.get("progress") or {})
                progress.update({
                    "task": item["key"],
                    "group_id": item.get("group_id", ""),
                    "unit_done": done,
                    "unit_total": total,
                    "requests": payload.get("requests", 0),
                    "rows": payload.get("rows", 0),
                    "phase": payload.get("phase", "fetch"),
                    "label": payload.get("label", ""),
                    "eta_seconds": eta,
                })
                job["progress"] = progress

        return callback

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
                results[item.get("result_key") or item["key"]] = {
                    "status": "unavailable", "reason": reason, "rows": None,
                    "task_key": item["key"],
                    "group_id": item.get("group_id", ""),
                }
            return False
        try:
            setup = sync.setup()
            if setup.status != "ok":
                for item in items:
                    results[item.get("result_key") or item["key"]] = _summary(setup)
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
                    groups=item.get("groups"), quarters=item.get("quarters"),
                    force_refresh=bool(item.get("force_refresh", False)),
                    progress=self._progress_callback(job, item),
                    cancel=self._cancel,
                )
                summary = _summary(envelope)
                summary.update(_timing(started, began, item))
                summary["task_key"] = item["key"]
                summary["group_id"] = item.get("group_id", "")
                results[item.get("result_key") or item["key"]] = summary
                with self._lock:
                    progress = dict(job.get("progress") or {})
                    progress["group_index"] = int(progress.get("group_index") or 0) + 1
                    job["progress"] = progress
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
                results[item.get("result_key") or item["key"]] = {
                    "status": "unavailable", "reason": reason, "rows": None,
                    "task_key": item["key"],
                    "group_id": item.get("group_id", ""),
                }
            return False
        try:
            setup = sync.setup()
            if setup.status != "ok":
                for item in items:
                    results[item.get("result_key") or item["key"]] = _summary(setup)
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
                summary["task_key"] = item["key"]
                summary["group_id"] = item.get("group_id", "")
                results[item.get("result_key") or item["key"]] = summary
                with self._lock:
                    progress = dict(job.get("progress") or {})
                    progress["group_index"] = int(progress.get("group_index") or 0) + 1
                    job["progress"] = progress
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
