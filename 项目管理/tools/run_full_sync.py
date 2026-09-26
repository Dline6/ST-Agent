#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_full_sync.py — 全市场首次全量同步长跑脚本（T-L0-007.1）

把「全量首次同步」从一次性手工操作变成可重复流程：参数与逐任务结果据实留痕成
一份 JSON 摘要。同步逻辑全部复用 ``BaoStockSync``——本脚本只做装配与计时，
不重写引擎（执行序取公开常量 ``RUN_ORDER``，与 ``run_all`` 同源）。

零泄漏纪律（02 §6 / 宪法第 1 条）：主口令只经环境变量
``ST_AGENT_PASSPHRASE`` 传入，**不进 argv、不进摘要、不落盘**；真实数据只落
本地加密库与 ``tmp/``，不进 Git。

用法（需真网 + 可选依赖 baostock）：
    pip install -e ".[market]"
    ST_AGENT_PASSPHRASE='...' python 项目管理/tools/run_full_sync.py \
        --root tmp/live-market --out tmp/full-sync-summary.json

退出码：0 = 全量完成（无非 ok/跳过任务）；1 = 有任务失败；2 = 用法 / 环境 / 装配错误。
"""
from __future__ import annotations

import argparse
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
PASSPHRASE_ENV = "ST_AGENT_PASSPHRASE"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def summarize_run(
    sync: Any,
    *,
    watchlist: list[str] | None = None,
    enabled: dict[str, bool] | None = None,
    timeout_ms: int = 120_000,
    params: dict[str, Any] | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    """按 ``RUN_ORDER`` 逐任务跑一次全量并计耗时，返回留痕摘要 dict。

    只读 ``BaoStockSync`` 公开接口；``validation_failed`` 即中断——与
    ``run_all`` 的执行语义一致（执行序同源 ``RUN_ORDER``）。
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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="全市场首次全量同步长跑（T-L0-007.1；口令经环境变量传入）",
    )
    p.add_argument("--root", default=str(DEFAULT_ROOT),
                   help=f"存储根（缺省 {DEFAULT_ROOT}）")
    p.add_argument("--out", default=str(DEFAULT_OUT),
                   help=f"JSON 摘要落点（缺省 {DEFAULT_OUT}）")
    p.add_argument("--init", action="store_true",
                   help="目标目录无存储时先 Store.create（首次全量必带）")
    p.add_argument("--watchlist", default="",
                   help="分钟线关注池（逗号分隔代码；缺省不跑分钟线）")
    p.add_argument("--enable-minute", action="store_true",
                   help="启用分钟线任务（需同时给 --watchlist）")
    p.add_argument("--timeout-ms", type=int, default=120_000,
                   help="单次抓取超时（毫秒）")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    passphrase = os.environ.get(PASSPHRASE_ENV)
    if not passphrase:
        print(f"缺少主口令：请设置环境变量 {PASSPHRASE_ENV}", file=sys.stderr)
        return 2

    try:
        from st_agent.l0.market import BaoStockFetcher, BaoStockSync, MarketDb
        from st_agent.l0.market.errors import FetchUnavailableError
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

    watchlist = [c.strip() for c in args.watchlist.split(",") if c.strip()] or None
    enabled = {"bs_k_minute": True} if args.enable_minute else None
    params = {
        "root": str(root),
        "watchlist": watchlist,
        "enabled": enabled,
        "timeout_ms": args.timeout_ms,
        "passphrase_source": f"env:{PASSPHRASE_ENV}",
    }

    try:
        with BaoStockFetcher() as fetcher:
            sync = BaoStockSync(MarketDb(store), EgressGateway(store), fetcher)
            summary = summarize_run(
                sync, watchlist=watchlist, enabled=enabled,
                timeout_ms=args.timeout_ms, params=params,
            )
    except FetchUnavailableError as exc:
        print(f"真实抓取器不可用（装 pip install -e \".[market]\"）：{exc}",
              file=sys.stderr)
        return 2

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    bad = [k for k, v in summary["tasks"].items()
           if v["status"] not in ("ok", "unavailable")]
    print(f"摘要已写入 {out}；非 ok / 跳过任务：{bad or '无'}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
