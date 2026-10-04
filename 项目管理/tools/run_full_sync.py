#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_full_sync.py — 全市场首次全量同步长跑脚本（T-L0-007.1 + T-L0-017.1）

把「全量首次同步」从一次性手工操作变成**可重复、可中断、可续跑、有日预算**的流程：
参数与逐任务 / 逐分片结果据实留痕成一份 JSON 摘要。同步逻辑全部复用
``BaoStockSync``——本脚本只做装配、分片、计时与收手，不重写引擎
（执行序取公开常量 ``RUN_ORDER``，与 ``run_all`` 同源）。

**为什么要分片与预算**（T-L0-017 立项理由，2026-10-04 实测）：

- 逐码任务对每只证券各发一次抓取；全市场近一年口径约 **7.7–12.6 万次请求**，
  而源端**单日超 5 万次会被拉黑**——必须按日预算收手、次日续跑；
- 不分片则中断即白跑整任务，故按「任务 × 码分片」推进并逐片落 checkpoint；
- ``--batch-size`` 另把「一次事务＝整库 blob 加解密」的次数从「码数」降到「批数」。

零泄漏纪律（02 §6 / 宪法第 1 条）：主口令只经环境变量
``ST_AGENT_PASSPHRASE`` 传入，**不进 argv、不进摘要、不进 checkpoint、不落盘**；
真实数据只落本地加密库与 ``tmp/``，不进 Git。

用法（需真网 + 可选依赖 baostock）：
    pip install -e ".[market]"
    ST_AGENT_PASSPHRASE='...' python 项目管理/tools/run_full_sync.py \
        --init --start 2025-10-04 --quarters 1 --skip-delisted \
        --daily-request-budget 40000 --out tmp/full-sync-summary.json

中断后次日续跑（同一 ``--root`` 与同一组分片参数）：
    ... --resume

退出码：0 = 全部任务 ok；1 = 未完成（有失败 / 被预算收手，可续跑）；2 = 用法 / 环境 / 装配错误。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = REPO_ROOT / "tmp" / "live-market"
DEFAULT_OUT = REPO_ROOT / "tmp" / "full-sync-summary.json"
DEFAULT_CHECKPOINT_NAME = "full-sync-checkpoint.json"
PASSPHRASE_ENV = "ST_AGENT_PASSPHRASE"
CHECKPOINT_VERSION = 1

DEFAULT_SHARD_SIZE = 500
DEFAULT_BATCH_SIZE = 200
DEFAULT_DAILY_BUDGET = 40_000
DEFAULT_RETRY_ATTEMPTS = 3


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i:i + size] for i in range(0, len(items), size)] or [[]]


def _codes_fingerprint(codes: list[str]) -> str:
    """码表指纹（条数 + 首末 + 摘要）——续跑时校验「跑的是不是同一批码」。"""
    blob = ",".join(codes).encode("utf-8")
    head = f"{len(codes)}:{codes[0] if codes else '-'}:{codes[-1] if codes else '-'}"
    return f"{head}:{hashlib.sha1(blob).hexdigest()[:12]}"


# ───────────────────────── 摘要（逐任务一次性跑；T-L0-007.1 既有形态） ─────────────────────────

def summarize_run(
    sync: Any,
    *,
    watchlist: list[str] | None = None,
    enabled: dict[str, bool] | None = None,
    timeout_ms: int = 120_000,
    params: dict[str, Any] | None = None,
    start: str | None = None,
    quarters: int | None = None,
    batch_size: int | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    """按 ``RUN_ORDER`` 逐任务跑一次全量并计耗时，返回留痕摘要 dict。

    只读 ``BaoStockSync`` 公开接口；``validation_failed`` 即中断——与
    ``run_all`` 的执行语义一致（执行序同源 ``RUN_ORDER``）。
    分片 / 续跑 / 预算版见 :func:`run_sharded`。
    """
    from st_agent.l0.market import RUN_ORDER

    started_at = _now_iso()
    wall0 = clock()
    tasks: dict[str, dict[str, Any]] = {}

    setup_env = sync.setup()
    if setup_env.status != "ok":
        for task_key in RUN_ORDER:
            tasks[task_key] = {
                "status": setup_env.status,
                "rows": None,
                "watermark": None,
                "duration_seconds": 0.0,
                "reason": setup_env.reason or "建库失败，未执行同步",
                "log_ref": setup_env.log_ref or "",
            }
        return {
            "started_at": started_at,
            "finished_at": _now_iso(),
            "total_duration_seconds": round(clock() - wall0, 3),
            "run_order": list(RUN_ORDER),
            "params": dict(params or {}),
            "tasks": tasks,
        }

    for task_key in RUN_ORDER:
        t0 = clock()
        env = sync.run_task(
            task_key, enabled=enabled, watchlist=watchlist, timeout_ms=timeout_ms,
            start=start, batch_size=batch_size, quarters=quarters,
        )
        data = env.data or {}
        tasks[task_key] = {
            "status": env.status,
            "rows": data.get("rows"),
            "watermark": data.get("watermark"),
            "duration_seconds": round(clock() - t0, 3),
            "reason": env.reason or "",
            "log_ref": env.log_ref or "",
        }
        if env.status == "validation_failed":
            break

    return {
        "started_at": started_at,
        "finished_at": _now_iso(),
        "total_duration_seconds": round(clock() - wall0, 3),
        "run_order": list(RUN_ORDER),
        "params": dict(params or {}),
        "tasks": tasks,
    }


# ───────────────────────── 分片长跑（T-L0-017.1） ─────────────────────────

def run_sharded(
    sync: Any,
    *,
    codes: list[str] | None = None,
    start: str | None = None,
    quarters: int | None = None,
    batch_size: int | None = None,
    shard_size: int | None = None,
    watchlist: list[str] | None = None,
    enabled: dict[str, bool] | None = None,
    timeout_ms: int = 120_000,
    checkpoint: dict[str, Any] | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    audited_today: int = 0,
    daily_budget: int | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    """按「任务 × 码分片」推进，逐片落 checkpoint，并按日请求预算收手。

    与 :func:`summarize_run` 的差别只有两点——**分片可续**与**预算可停**；
    执行序同源 ``RUN_ORDER``，单任务语义同源 ``run_task``。

    :param checkpoint: 既有 checkpoint（``--resume`` 时传入）；**就地更新**
    :param on_progress: 每片结束后回调（落盘 checkpoint 用）
    :param audited_today: 本次开跑前「今天已发出的抓取次数」（日预算起算点）
    :param daily_budget: 单日抓取次数上限；``None`` ＝不限
    """
    from st_agent.l0.market import RUN_ORDER
    from st_agent.l0.market.sync import PER_CODE_TASKS, estimate_requests

    state = checkpoint if checkpoint is not None else {}
    tasks_state: dict[str, Any] = state.setdefault("tasks", {})
    started_at = _now_iso()
    wall0 = clock()
    tasks: dict[str, dict[str, Any]] = {}
    stop_reason = ""

    setup_env = sync.setup()
    if setup_env.status != "ok":
        for task_key in RUN_ORDER:
            tasks[task_key] = {
                "status": setup_env.status,
                "rows": None,
                "watermark": None,
                "duration_seconds": 0.0,
                "reason": setup_env.reason or "建库失败，未执行同步",
                "log_ref": setup_env.log_ref or "",
                "shards_done": 0,
                "shards_total": 0,
            }
        return _summary(started_at, wall0, clock, tasks, state, stop_reason,
                        sync.fetch_attempts, audited_today)

    universe = list(codes) if codes is not None else sync.security_codes()
    state["codes"] = _codes_fingerprint(universe)

    for task_key in RUN_ORDER:
        record = tasks_state.setdefault(
            task_key, {"shards_done": [], "shards_total": 0, "status": "",
                       "rows": 0, "watermark": None, "reason": ""})
        per_code = task_key in PER_CODE_TASKS
        shards: list[list[str]] = (
            _chunks(universe, shard_size) if per_code and shard_size
            else ([universe] if per_code else [[]])
        )
        record["shards_total"] = len(shards)
        done: list[int] = list(record.get("shards_done") or [])
        t_task = clock()
        task_rows = int(record.get("rows") or 0)
        halted = False

        for index, shard in enumerate(shards):
            if index in done:
                continue
            shard = _fit_to_budget(sync, task_key, shard, shard_size,
                                   audited_today, daily_budget, quarters)
            if shard is _BUDGET_EXHAUSTED:
                stop_reason = (f"日请求预算用尽（已发 {audited_today + sync.fetch_attempts}"
                               f" 次，上限 {daily_budget}）：{task_key} 第 {index} 片未跑")
                halted = True
                break
            env = sync.run_task(
                task_key, enabled=enabled, watchlist=watchlist,
                timeout_ms=timeout_ms, start=start, batch_size=batch_size,
                quarters=quarters, codes=shard if per_code else None,
            )
            data = env.data or {}
            if env.status == "ok":
                task_rows += int(data.get("rows") or 0)
                record["watermark"] = data.get("watermark")
                done.append(index)
                record.update({"shards_done": done, "status": "ok",
                               "rows": task_rows, "reason": ""})
            else:
                record.update({"status": env.status, "rows": task_rows,
                               "reason": env.reason or ""})
            if on_progress is not None:
                on_progress(state)
            if env.status != "ok" and env.status != "unavailable":
                stop_reason = f"{task_key} 第 {index} 片 {env.status}：{env.reason or ''}"
                halted = True
                break
            if env.status == "validation_failed":
                break

        tasks[task_key] = {
            "status": record.get("status") or "todo",
            "rows": task_rows,
            "watermark": record.get("watermark"),
            "duration_seconds": round(clock() - t_task, 3),
            "reason": record.get("reason") or "",
            "log_ref": "",
            "shards_done": len(record.get("shards_done") or []),
            "shards_total": len(shards),
        }
        if halted:
            break

    return _summary(started_at, wall0, clock, tasks, state, stop_reason,
                    sync.fetch_attempts, audited_today)


class _BudgetExhausted:
    """哨兵：本片在该预算下一点也跑不动。"""


_BUDGET_EXHAUSTED = _BudgetExhausted()


def _fit_to_budget(sync: Any, task_key: str, shard: list[str],
                   shard_size: int | None, audited_today: int,
                   daily_budget: int | None, quarters: int | None) -> Any:
    """按剩余预算收缩本片（跑得动就尽量跑满，跑不动返回哨兵）。

    收缩粒度是**码数**，故实际次数仍可能略超预算——上界＝本片码数 ×
    每码请求数。留余量靠把 ``--daily-request-budget`` 定在红线之下。
    """
    if daily_budget is None:
        return shard
    from st_agent.l0.market.sync import estimate_requests

    remaining = daily_budget - (audited_today + sync.fetch_attempts)
    if remaining <= 0:
        return _BUDGET_EXHAUSTED
    if not shard:
        return shard                       # 非逐码任务：一次调用，剩余预算 > 0 即可跑
    per_code = max(1, estimate_requests(task_key, code_count=1, quarters=quarters))
    affordable = remaining // per_code
    if affordable <= 0:
        return _BUDGET_EXHAUSTED
    if len(shard) <= affordable:
        return shard
    return shard[:affordable]


def _summary(started_at: str, wall0: float, clock: Callable[[], float],
             tasks: dict[str, Any], state: dict[str, Any], stop_reason: str,
             requests_issued: int, audited_today: int) -> dict[str, Any]:
    return {
        "started_at": started_at,
        "finished_at": _now_iso(),
        "total_duration_seconds": round(clock() - wall0, 3),
        "params": dict(state.get("params") or {}),
        "tasks": tasks,
        "stopped_reason": stop_reason,
        "requests_issued": requests_issued,
        "audited_today_before": audited_today,
    }


# ───────────────────────── checkpoint 与审计计量 ─────────────────────────

def load_checkpoint(path: Path) -> dict[str, Any]:
    """读 checkpoint（不存在 → 空骨架；损坏 → 显式抛错，不静默重来）。"""
    if not path.exists():
        return {"version": CHECKPOINT_VERSION, "tasks": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != CHECKPOINT_VERSION:
        raise ValueError(f"checkpoint 版本不匹配：{data.get('version')}")
    data.setdefault("tasks", {})
    return data


def save_checkpoint(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(data, ensure_ascii=False, indent=2)
    # 口令永不入 checkpoint：只写参数块（不含口令，来源只记口径）
    path.write_text(blob, encoding="utf-8")


def count_audited_today(gateway: Any, now: datetime | None = None) -> int:
    """今天已发出的 ``data_fetch`` 审计条数（日预算的起算点）。

    只调**一次** ``gateway.query(start, …)``，且带**今天 0 点**这条时间下界——
    T-L0-018.2 起审计按**段**落盘（一段装多条、段名带起止时戳），故本次查询
    **跳段**：只读今天那几段，不读全量历史（此前要逐条读并解密整个审计分区）。
    """
    start = (now or datetime.now().astimezone()).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return len(gateway.query(start, kind="data_fetch"))


# ───────────────────────── CLI ─────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="全市场首次全量同步长跑（分片 / 续跑 / 日预算；口令经环境变量传入）",
    )
    p.add_argument("--root", default=str(DEFAULT_ROOT),
                   help=f"存储根（缺省 {DEFAULT_ROOT}）")
    p.add_argument("--out", default=str(DEFAULT_OUT),
                   help=f"JSON 摘要落点（缺省 {DEFAULT_OUT}）")
    p.add_argument("--checkpoint", default="",
                   help=f"checkpoint 落点（缺省 <root 同级>/{DEFAULT_CHECKPOINT_NAME}）")
    p.add_argument("--init", action="store_true",
                   help="目标目录无存储时先 Store.create（首次全量必带）")
    p.add_argument("--resume", action="store_true",
                   help="按 checkpoint 续跑（分片参数须与上次一致）")
    p.add_argument("--start", default="",
                   help="逐码任务窗口起点 YYYY-MM-DD（缺省＝按水位推导，即全历史基线）")
    p.add_argument("--quarters", type=int, default=None,
                   help="财务域重拉季度数（缺省＝05 口径 8；每码每季度 6 次请求）")
    p.add_argument("--skip-delisted", action="store_true",
                   help="跳过窗口起点前已退市的码（近一年窗口下无损，可省约四成请求）")
    p.add_argument("--shard-size", type=int, default=DEFAULT_SHARD_SIZE,
                   help=f"每片码数（checkpoint 粒度；缺省 {DEFAULT_SHARD_SIZE}）")
    p.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                   help=f"每批码数（写事务粒度；缺省 {DEFAULT_BATCH_SIZE}，0 ＝逐码一次）")
    p.add_argument("--daily-request-budget", type=int, default=DEFAULT_DAILY_BUDGET,
                   help=f"单日抓取次数上限（源端超 5 万会拉黑；缺省 {DEFAULT_DAILY_BUDGET}）")
    p.add_argument("--retry-attempts", type=int, default=DEFAULT_RETRY_ATTEMPTS,
                   help=f"可重试错误的尝试次数上限（缺省 {DEFAULT_RETRY_ATTEMPTS}）")
    p.add_argument("--watchlist", default="",
                   help="分钟线关注池（逗号分隔代码；缺省不跑分钟线）")
    p.add_argument("--enable-minute", action="store_true",
                   help="启用分钟线任务（需同时给 --watchlist）")
    p.add_argument("--timeout-ms", type=int, default=120_000,
                   help="单次抓取超时（毫秒）")
    return p


def _checkpoint_path(args: argparse.Namespace, root: Path) -> Path:
    if args.checkpoint:
        return Path(args.checkpoint)
    return root.parent / DEFAULT_CHECKPOINT_NAME


def _params_block(args: argparse.Namespace, root: Path,
                  codes: list[str]) -> dict[str, Any]:
    return {
        "root": str(root),
        "start": args.start or None,
        "quarters": args.quarters,
        "shard_size": args.shard_size,
        "batch_size": args.batch_size or None,
        "skip_delisted": bool(args.skip_delisted),
        "codes": _codes_fingerprint(codes),
        "watchlist": [c.strip() for c in args.watchlist.split(",") if c.strip()] or None,
        "passphrase_source": f"env:{PASSPHRASE_ENV}",
    }


def _select_codes(db: Any, *, start: str, skip_delisted: bool) -> list[str]:
    """逐码任务的码表：缺省＝库内主档全量；``skip_delisted`` 时滤掉窗口前已退市的码。

    过滤判据是 ``security.out_date``（退市日）——**无损**：退市日早于窗口起点的码
    在窗口内本就没有数据。
    """
    if not skip_delisted:
        envelope = db.query("SELECT code FROM security ORDER BY code")
    else:
        envelope = db.query(
            "SELECT code FROM security WHERE out_date IS NULL OR out_date >= ? "
            "ORDER BY code", (start,))
    if envelope.status == "unavailable":
        return []
    rows = (envelope.data or {}).get("rows") or []
    return [r["code"] for r in rows]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    passphrase = os.environ.get(PASSPHRASE_ENV)
    if not passphrase:
        print(f"缺少主口令：请设置环境变量 {PASSPHRASE_ENV}", file=sys.stderr)
        return 2

    try:
        from st_agent.l0.market import BaoStockFetcher, BaoStockSync, MarketDb
        from st_agent.l0.market.errors import FetchUnavailableError
        from st_agent.l0.market.sync import RetryPolicy
        from st_agent.l0.net import EgressGateway
        from st_agent.l0.storage import Store
    except ImportError as exc:  # 包未安装
        print(f"无法导入 st_agent（先 pip install -e .）：{exc}", file=sys.stderr)
        return 2

    root = Path(args.root)
    try:
        store = Store.create(root, passphrase) if args.init else Store.open(root, passphrase)
    except Exception as exc:  # noqa: BLE001 — 装配面统一显式化
        print(f"打开存储失败（{root}）：{exc}", file=sys.stderr)
        return 2

    db = MarketDb(store)
    # 审计**必须开**：本脚本的日请求预算是从审计记录里数出来的
    # （``count_audited_today``）——关掉审计会静默丢掉这道「防源端拉黑」的护栏。
    # 产品缺省是关（02 §6 / D-073），而 T-L0-018.2 已把审计落盘改为分段日志，
    # 逐次留痕的 O(n²) 开销随之消失，故长跑照开。
    gateway = EgressGateway(store, audit=True)
    checkpoint_path = _checkpoint_path(args, root)

    start = args.start or "1990-01-01"
    codes = _select_codes(db, start=start, skip_delisted=args.skip_delisted)
    params = _params_block(args, root, codes)

    state: dict[str, Any] = {"version": CHECKPOINT_VERSION, "tasks": {}, "params": params}
    if args.resume:
        try:
            previous = load_checkpoint(checkpoint_path)
        except ValueError as exc:
            print(f"checkpoint 不可用：{exc}", file=sys.stderr)
            return 2
        if previous.get("params") != params:
            print("checkpoint 的分片参数与本次不一致，拒绝续跑：\n"
                  f"  上次 {previous.get('params')}\n  本次 {params}", file=sys.stderr)
            return 2
        state = previous

    watchlist = [c.strip() for c in args.watchlist.split(",") if c.strip()] or None
    enabled = {"bs_k_minute": True} if args.enable_minute else None

    try:
        with BaoStockFetcher() as fetcher:
            sync = BaoStockSync(
                db, gateway, fetcher,
                retry=RetryPolicy(attempts=args.retry_attempts),
            )
            audited_today = count_audited_today(gateway)
            summary = run_sharded(
                sync, codes=codes, start=args.start or None,
                quarters=args.quarters,
                batch_size=args.batch_size or None,
                shard_size=args.shard_size or None,
                watchlist=watchlist, enabled=enabled, timeout_ms=args.timeout_ms,
                checkpoint=state,
                on_progress=lambda s: save_checkpoint(checkpoint_path, s),
                audited_today=audited_today,
                daily_budget=args.daily_request_budget,
            )
    except FetchUnavailableError as exc:
        print(f"真实抓取器不可用（装 pip install -e \".[market]\"）：{exc}",
              file=sys.stderr)
        return 2

    summary["params"] = params
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    save_checkpoint(checkpoint_path, state)

    bad = [k for k, v in summary["tasks"].items()
           if v["status"] not in ("ok", "unavailable")]
    print(f"摘要已写入 {out}；checkpoint {checkpoint_path}")
    print(f"本次抓取 {summary['requests_issued']} 次（开跑前当日已有 "
          f"{summary['audited_today_before']} 次）；未完成任务：{bad or '无'}"
          + (f"；{summary['stopped_reason']}" if summary["stopped_reason"] else ""))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
