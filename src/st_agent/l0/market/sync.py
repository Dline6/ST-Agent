"""BaoStock 同步引擎（02 §5 数据源缓存执行面 + 数据库设计 05 同步策略）。

唯一出口纪律（02 §6，GWT-3）：
- 每次抓取经 ``EgressGateway.execute(kind="data_fetch", target_host="baostock")``
  发出并审计；按次 ``sender`` 闭包内调用 ``Fetcher`` 协议并翻译异常：
  ``FetchUnavailableError`` → ``EgressUnavailableError``（→ 信封
  ``unavailable``）；``FetchError`` / 裸异常 → ``EgressError``（→ 信封
  ``failed``，带 ``log_ref``）
- 审计只含字节数（行字符量累加），行内容永不进网关、不进审计（GWT-5 零泄漏）

执行语义（05 时序前置 / 水位 / 完整性）：
- ``setup``：首次建库（``MarketDb.init_db``）+ ``data_source`` 注册 +
  20 个 ``sync_state`` 种子行（幂等，可重入）
- ``run_task``：前置检查（``needs`` 行 ``last_status='ok'``）→ 水位推导窗口
  → 抓取（经网关）→ 清洗映射 → UPSERT 入库（``transact``）→ 水位回写 →
  完整性校验（05 五项；失败记 ``partial`` + ``last_error``，**不回滚**已写
  数据，下次同步自愈）→ 返回 ``ResultEnvelope``
- ``run_all``：按 05 初始化顺序执行（元数据 → 日历 → 主档 → 全量快照 →
  板块基线 → 行情基线 → 财务基线 → 宏观基线 → 可选分钟线）；禁用的任务
  跳过并记 ``unavailable``（GWT-5 开关语义）
- ``freshness_verdict``：按数据域查 ``sync_state`` 自检 → ``StalenessVerdict``
  （异常 → 上层走 ``unavailable`` 并标注最后更新；02 §5 / 05 新鲜度契约）

开关状态由调用方以 ``enabled`` 映射传入（配置注册表职责，不落库，02 §5）。
分钟线任务默认禁用，无关注池时拒绝执行（``MarketValidationError``）。
"""

from __future__ import annotations

import sqlite3
import time
from datetime import date, datetime
from typing import Any, Callable, NamedTuple

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.time_events import StalenessVerdict
from st_agent.l0.market.db import MarketDb
from st_agent.l0.market.errors import (
    FetchError,
    FetchUnavailableError,
    MarketValidationError,
)
from st_agent.l0.market.fetch import (
    FIN_QUARTER_COLUMNS,
    map_adjust_factor,
    map_all_stock,
    map_calendar,
    map_constituent,
    map_dividend,
    map_financial,
    map_forecast,
    map_industry,
    map_k_daily,
    map_k_minute,
    map_k_period,
    map_macro_deposit,
    map_macro_loan,
    map_macro_money_month,
    map_macro_money_year,
    map_macro_reserve,
    map_performance_express,
    map_security_basic,
    result_shape,
    source_page_size,
    truncation_suspect,
)
from st_agent.l0.market.tasks import (
    SYNC_TASKS,
    get_task,
    last_monday,
    last_n_quarters,
    window_for,
)

__all__ = [
    "BAOSTOCK_HOST",
    "BaoStockSync",
    "DOMAIN_TASKS",
    "PER_CODE_TASKS",
    "RUN_ORDER",
    "RetryPolicy",
    "estimate_requests",
]

BAOSTOCK_HOST = "baostock"
"""网关审计的 ``target_host``（只记主机，不记完整 URL，02 §6）。"""

SOURCE_ID = "baostock"
SOURCE_NAME = "BaoStock 本地市场数据源"

#: 数据域 → 相关同步任务（新鲜度判定与 ``unavailable`` 标注口径，05 陈旧判据）。
DOMAIN_TASKS: dict[str, tuple[str, ...]] = {
    "kline": ("bs_calendar", "bs_all_stock", "bs_k_daily", "bs_k_period"),
    "financial": ("bs_fin_quarter",),
    "company_report": ("bs_perf_express", "bs_forecast"),
    "sector": ("bs_industry", "bs_sz50", "bs_hs300", "bs_zz500"),
    "macro": ("bs_macro_deposit", "bs_macro_loan", "bs_macro_reserve",
              "bs_money_month", "bs_money_year"),
    "announcement": (),
}

#: 05 初始化顺序（``run_all`` 执行序；分钟线按需附后）。
RUN_ORDER: tuple[str, ...] = (
    "bs_calendar", "bs_security_basic", "bs_all_stock",
    "bs_industry", "bs_sz50", "bs_hs300", "bs_zz500",
    "bs_k_daily", "bs_k_period", "bs_adjust_factor", "bs_dividend",
    "bs_fin_quarter", "bs_perf_express", "bs_forecast",
    "bs_macro_deposit", "bs_macro_loan", "bs_macro_reserve",
    "bs_money_month", "bs_money_year",
    "bs_k_minute",
)
"""全量同步执行序（公开：长跑脚本 T-L0-007.1 据此逐任务计时，不重写 `run_all` 语义）。"""

PER_CODE_TASKS: tuple[str, ...] = (
    "bs_k_daily", "bs_k_period", "bs_k_minute", "bs_adjust_factor",
    "bs_dividend", "bs_fin_quarter", "bs_perf_express", "bs_forecast",
)
"""逐码任务（对每只证券各发一次抓取）——长跑脚本据此切「码分片」（T-L0-017.1）。"""

_REQUESTS_PER_CODE: dict[str, int] = {
    "bs_k_daily": 1,
    "bs_k_period": 2,          # 周线 + 月线
    "bs_k_minute": 4,          # 5 / 15 / 30 / 60 分钟
    "bs_adjust_factor": 1,
    "bs_perf_express": 1,
    "bs_forecast": 1,
}


def estimate_requests(task_key: str, *, code_count: int,
                      years: int = 2, quarters: int | None = None) -> int:
    """该任务对 ``code_count`` 只码的**计划抓取次数**（不含重试与截断复核）。

    只供长跑脚本估日请求预算用（源端单日超 5 万会拉黑）。**实际次数以
    ``BaoStockSync.fetch_attempts`` 为准**——重试与截断复核都会使其上浮。
    非逐码任务恒为 1（一次全量接口调用）。
    """
    if task_key == "bs_fin_quarter":
        return 6 * (quarters or 8) * code_count       # 六张表 × 季度数
    if task_key == "bs_dividend":
        return years * code_count                     # 按年拉取
    per_code = _REQUESTS_PER_CODE.get(task_key)
    if per_code is None:
        return 1          # 非逐码任务：一次全量接口调用，与码数无关
    return per_code * code_count


class RetryPolicy(NamedTuple):
    """抓取重试策略（T-L0-017.2 GWT-3；仅作用于**可重试**错误）。

    可重试性由抓取器判定（``FetchError.retryable``）——引擎不解析错误码，
    故换源只需换抓取器，不动本策略。每次重试都是**一次独立的网关 ``execute``**，
    审计条数如实增长（02 §6 唯一出口，不合并、不隐藏）。
    """

    attempts: int = 3
    """总尝试次数（含首次）；``1`` ＝不重试。"""

    base_seconds: float = 1.0
    max_seconds: float = 30.0

    def delay_for(self, attempt: int) -> float:
        """第 ``attempt`` 次失败（1 起算）后到下一次尝试的等待：指数退避、封顶。"""
        return min(self.base_seconds * (2 ** (attempt - 1)), self.max_seconds)


DEFAULT_RETRY = RetryPolicy()


class _BatchWriter:
    """按「码数」攒批落库（T-L0-017.1 GWT-1）。

    ``batch_size=None`` ⇒ 每只码提交一次事务（**既有行为，逐字节不变**）；
    给出时攒满 ``batch_size`` 只码才提交一次 —— 把「一次事务＝整库 blob
    解密 + 加密 + 写回」的次数从「码数」降到「批数」（全市场全历史实测该开销
    ≈5.3 秒/GB/次，逐码提交使长跑呈 n² 行为）。

    失败时**当前未提交批**的行随之丢失（已完成批不受影响）——这正是重跑
    只回退一批的代价上界。
    """

    def __init__(self, write: Callable[[list[tuple]], None],
                 batch_size: int | None) -> None:
        if batch_size is not None and batch_size < 1:
            raise MarketValidationError(f"批大小须 ≥ 1，实际 {batch_size!r}")
        self._write = write
        self._size = batch_size if batch_size is not None else 1
        self._pending: list[tuple] = []
        self._codes = 0

    def add(self, rows: list[tuple]) -> None:
        """记一只码的抓取结果（空行也计入码数），攒满即提交。"""
        self._pending.extend(rows)
        self._codes += 1
        if self._codes >= self._size:
            self.flush()

    def flush(self) -> None:
        """提交当前批（无未提交码时不动库）。"""
        if self._codes == 0:
            return
        self._write(self._pending)
        self._pending = []
        self._codes = 0


def _now() -> datetime:
    """用户本地时区当前时刻（01 §8）。"""
    return datetime.now().astimezone()


def _now_iso() -> str:
    return _now().isoformat()


class BaoStockSync:
    """BaoStock 同步引擎（02 §5 执行面；只经 ``MarketDb`` 与网关读写）。

    :param db: 市场数据库门面
    :param gateway: 出网审计网关（抓取唯一出口）
    :param fetcher: 抓取器协议实现（真实管道注入 baostock 封装；测试注入 Fake）
    :param initiator: 审计 ``initiator``（默认 ``market-sync``）
    :param retry: 抓取重试策略（仅作用于可重试错误；``RetryPolicy(attempts=1)`` ＝不重试）
    :param sleep: 退避等待的可注入实现（测试注入记录器，不真等）
    """

    def __init__(self, db: MarketDb, gateway, fetcher, *,
                 initiator: str = "market-sync",
                 retry: RetryPolicy | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._db = db
        self._gateway = gateway
        self._fetcher = fetcher
        self._initiator = initiator
        self._retry = retry or DEFAULT_RETRY
        self._sleep = sleep
        self._fetch_attempts = 0

    @property
    def fetch_attempts(self) -> int:
        """本实例经网关发出的抓取次数（**含重试与截断复核**）。

        与审计条数**同源同数**——每次 ``_execute_once`` 恰对应一条 ``data_fetch``
        审计记录。长跑脚本据此计日请求预算（源端有单日上限，超限会被拉黑）。
        """
        return self._fetch_attempts

    # ───────────────────────── 建库与种子 ─────────────────────────

    def setup(self) -> ResultEnvelope:
        """首次建库 + 元数据种子（幂等；已建库仅补种子行）。"""
        if not self._db.exists():
            self._db.init_db()
        with self._db.transact() as con:
            con.execute(
                "INSERT OR IGNORE INTO data_source(source_id, name, manual_ref) "
                "VALUES (?, ?, ?)",
                (SOURCE_ID, SOURCE_NAME, "docs/BaoStock-API手册/README.md"),
            )
            for spec in SYNC_TASKS:
                con.execute(
                    "INSERT OR IGNORE INTO sync_state(task_key, table_name, source_id,"
                    " mode, schedule_desc) VALUES (?, ?, ?, ?, ?)",
                    (spec.task_key, spec.table_name, SOURCE_ID,
                     spec.mode, spec.schedule_desc),
                )
            con.commit()
        return ResultEnvelope.ok(
            {"tasks": len(SYNC_TASKS), "source": SOURCE_ID},
            as_of=_now(),
        )

    # ───────────────────────── 单任务执行 ─────────────────────────

    def run_task(self, task_key: str, *,
                 enabled: dict[str, bool] | None = None,
                 day: str | None = None,
                 codes: list[str] | None = None,
                 watchlist: list[str] | None = None,
                 timeout_ms: int = 120_000,
                 start: str | None = None,
                 batch_size: int | None = None,
                 quarters: int | None = None,
                 cancel=None) -> ResultEnvelope:
        """执行单个同步任务（前置 → 窗口 → 抓取 → 入库 → 水位 → 校验）。

        :param enabled: 开关映射（缺省全开，分钟线默认关）；关闭 → ``unavailable``
        :param day: ``bs_all_stock`` 指定交易日（缺省今日）
        :param codes: 逐码任务的证券范围（缺省库内全量主档）
        :param watchlist: 分钟线关注池（必传，否则 ``MarketValidationError``）
        :param start: **显式窗口起点**（``YYYY-MM-DD``）——缺省＝按水位推导（行为不变）。
            给出时本任务按 ``[start, 今日]`` 取数，**不受全库水位影响**：这是「码分片」
            可行的前提（分片 2 不会被分片 1 推高的水位跳成空窗口）。水位回写口径不变。
        :param batch_size: 逐码任务的**写入批大小**（每多少只码提交一次事务）。
            ``None`` ＝逐码一次事务（既有行为，逐字节不变）；给出时按批合并，
            使整库 blob 加解密次数从「码数」降到「批数」。
        :param quarters: ``bs_fin_quarter`` 的重拉季度数（缺省＝05 口径的 8）
        """
        try:
            spec = get_task(task_key)
        except MarketValidationError as exc:
            return ResultEnvelope.validation_failed(str(exc))
        on = self._is_enabled(task_key, enabled)
        if not on:
            return self._disabled_envelope(task_key)
        if start is not None:
            try:
                date.fromisoformat(start)
            except ValueError:
                return ResultEnvelope.validation_failed(
                    f"显式窗口起点须为 YYYY-MM-DD，实际 {start!r}"
                )
        if not self._db.exists():
            now = _now()
            return ResultEnvelope.unavailable(
                "本地市场数据库尚未建立（先 setup 全量建库）",
                last_updated_at=now, as_of=now,
            )
        problems = self._check_prereqs(spec.needs)
        if problems:
            now = _now()
            # 分钟线缺关注池属调用方缺陷（validation_failed），优先于前置未就绪
            if task_key == "bs_k_minute" and not (watchlist or codes):
                return ResultEnvelope.validation_failed(
                    "分钟线任务需关注池（watchlist 非空；无关注池请保持禁用）"
                )
            return ResultEnvelope.unavailable(
                f"任务 {task_key!r} 前置未就绪：{'; '.join(problems)}",
                last_updated_at=self._db.last_updated_at() or now, as_of=now,
            )
        with self._db.transact() as con:
            watermark = self._watermark(con, task_key)
        try:
            loaded, new_watermark, expected = self._fetch_and_load(
                task_key, watermark, day=day, codes=codes, watchlist=watchlist,
                timeout_ms=timeout_ms, start=start, batch_size=batch_size,
                quarters=quarters, cancel=cancel,
            )
        except MarketValidationError as exc:
            return ResultEnvelope.validation_failed(str(exc))
        except FetchUnavailableError as exc:
            self._mark(con_task_key=task_key, status="failed", error=str(exc) or "源不可达",
                       row_count=0, watermark=watermark)
            stamp = self._db.last_updated_at() or _now()
            return ResultEnvelope.unavailable(str(exc) or "数据源不可达",
                                              last_updated_at=stamp, as_of=stamp)
        except (FetchError, Exception) as exc:
            log_ref = self._mark(con_task_key=task_key, status="failed",
                                 error=f"抓取失败：{exc}", row_count=0,
                                 watermark=watermark)
            return ResultEnvelope.failed(f"任务 {task_key!r} 抓取失败：{exc}",
                                         log_ref=log_ref)
        failed_checks = self._integrity_checks(task_key, expected=expected)
        status = "partial" if failed_checks else "ok"
        self._mark(con_task_key=task_key, status=status,
                   error="; ".join(failed_checks) if failed_checks else "",
                   row_count=loaded, watermark=new_watermark)
        stamp = self._db.last_updated_at() or _now()
        if failed_checks:
            return ResultEnvelope.unavailable(
                f"任务 {task_key!r} 数据面不完整：{'; '.join(failed_checks)}",
                last_updated_at=stamp, as_of=stamp,
            )
        return ResultEnvelope.ok(
            {"task": task_key, "rows": loaded, "watermark": new_watermark},
            as_of=stamp,
        )

    def run_all(self, *,
                enabled: dict[str, bool] | None = None,
                watchlist: list[str] | None = None,
                timeout_ms: int = 120_000) -> dict[str, ResultEnvelope]:
        """按 05 初始化顺序执行全部任务（禁用的跳过记 ``unavailable``）。"""
        results: dict[str, ResultEnvelope] = {}
        setup_env = self.setup()
        if setup_env.status != "ok":
            return {t: setup_env for t in RUN_ORDER}
        for task_key in RUN_ORDER:
            if task_key == "bs_k_minute" and not (watchlist or (enabled or {}).get(
                    "bs_k_minute", False)):
                results[task_key] = self._disabled_envelope(
                    task_key, reason="分钟线默认禁用（无关注池）")
                continue
            results[task_key] = self.run_task(
                task_key, enabled=enabled, watchlist=watchlist,
                timeout_ms=timeout_ms,
            )
            if results[task_key].status == "validation_failed":
                break
        return results

    # ───────────────────────── 新鲜度判定 ─────────────────────────

    def freshness_verdict(self, domain: str) -> StalenessVerdict:
        """按数据域判定新鲜度（05 陈旧判据 → ``StalenessVerdict`` 载体）。

        相关任务任一非 ok → ``stale=True``（上层走 ``unavailable`` 并标注
        最后更新）；未知域 → ``MarketValidationError``。
        """
        if domain not in DOMAIN_TASKS:
            raise MarketValidationError(
                f"未知数据域 {domain!r}；合法域 = {sorted(DOMAIN_TASKS)}"
            )
        last = self._db.as_of(domain) if self._db.exists() else None
        stamp = _now()
        if last is not None:
            try:
                stamp = datetime.fromisoformat(last)
                if stamp.tzinfo is None:
                    stamp = stamp.astimezone()
            except ValueError:
                stamp = _now()
        bad = [r for r in self._db.freshness()
               if r.get("task_key") in DOMAIN_TASKS[domain]]
        if bad or last is None:
            detail = ("数据面不完整：" + "; ".join(
                f"{r.get('task_key')}({r.get('last_status')})" for r in bad)
                if bad else "该域尚无数据（首次同步未完成）")
            return StalenessVerdict(stale=True, last_updated_at=stamp, detail=detail)
        return StalenessVerdict(
            stale=False, last_updated_at=stamp,
            detail=f"{domain} 数据面完整，截止 {last}",
        )

    def dataset_snapshot(self) -> str:
        """当前 ``dataset_snapshot_id``（01 §1 形态；水位组合的确定性摘要，02 §5）。

        可读原文经 ``MarketDb.snapshot_components()`` 取。
        """
        return self._db.snapshot_id()

    # ───────────────────────── 内部：开关与前置 ─────────────────────────

    @staticmethod
    def _is_enabled(task_key: str, enabled: dict[str, bool] | None) -> bool:
        if enabled is not None and task_key in enabled:
            return bool(enabled[task_key])
        return task_key != "bs_k_minute"  # 分钟线默认禁用（05）

    def _disabled_envelope(self, task_key: str, reason: str = "") -> ResultEnvelope:
        _ = reason
        stamp = self._db.last_updated_at() if self._db.exists() else None
        stamp = stamp or _now()
        return ResultEnvelope.unavailable(
            reason or f"任务 {task_key!r} 已禁用（数据源开关关闭），数据停留于标注时间",
            last_updated_at=stamp, as_of=stamp,
        )

    def _check_prereqs(self, needs: tuple[str, ...]) -> list[str]:
        problems: list[str] = []
        if not needs or not self._db.exists():
            return problems
        with self._db.connect() as con:
            for key in needs:
                row = con.execute(
                    "SELECT last_status, last_success_at FROM sync_state WHERE task_key=?",
                    (key,),
                ).fetchone()
                if row is None or row[0] != "ok" or row[1] is None:
                    problems.append(f"前置 {key} 未成功（先执行该任务）")
        return problems

    @staticmethod
    def _watermark(con: sqlite3.Connection, task_key: str) -> str | None:
        row = con.execute(
            "SELECT watermark FROM sync_state WHERE task_key=?", (task_key,)
        ).fetchone()
        return row[0] if row else None

    def _mark(self, con_task_key: str, status: str, error: str,
              row_count: int, watermark: str | None) -> str:
        """回写 ``sync_state`` 行；返回 ``log_ref``（``failed`` 信封用）。"""
        with self._db.transact() as con:
            con.execute(
                "UPDATE sync_state SET watermark=?, last_success_at=?, "
                "last_row_count=?, last_status=?, last_error=? WHERE task_key=?",
                (watermark, _now_iso(), row_count, status, error, con_task_key),
            )
            con.commit()
        return f"data_cache/{con_task_key}"

    # ───────────────────────── 内部：抓取闭包（网关唯一出口） ─────────────────────────

    def _via_gateway(self, task_key: str, call, *,
                     timeout_ms: int, cancel) -> Any:
        """经网关执行一次抓取（审计只记字节数，内容不进网关）。

        三件事在此叠加（T-L0-017.2）：

        ① **唯一出口不变**——每次尝试都是一次独立 ``gateway.execute``，审计条数
           如实增长（重试不合并、复核不隐藏，02 §6）；
        ② **重试退避**——仅对抓取器标为 ``retryable`` 的错误按 ``RetryPolicy``
           指数退避重试；重试前重建会话（连接被源端强断是实测到的常见成因）。
           网关自身的拦截（离线 / 无发包实现）**不**重试——那是策略判定；
        ③ **截断复核**——行数恰为整页这一可疑形态再抓一次（同样经网关留审计），
           两次不一致即判失败，不让半截结果集静默入库。

        :param call: 无参闭包 → ``FetchResult``；抛 ``FetchError`` 系异常
        :returns: ``(fields, rows)``；网关 ``unavailable``/``failed`` 时抛
            对应 ``Fetch*`` 异常（由 ``run_task`` 翻译为信封）
        """
        return self._with_retry(
            lambda: self._fetch_checked(task_key, call, timeout_ms=timeout_ms,
                                        cancel=cancel)
        )

    def _with_retry(self, operation) -> Any:
        """按 ``RetryPolicy`` 重试；用尽即抛原异常（信封口径不变）。"""
        attempt = 0
        while True:
            try:
                return operation()
            except FetchError as exc:      # 含 FetchUnavailableError
                attempt += 1
                if (not getattr(exc, "retryable", False)
                        or attempt >= self._retry.attempts):
                    raise
                self._sleep(self._retry.delay_for(attempt))
                self._reconnect_fetcher()

    def _reconnect_fetcher(self) -> None:
        """重试前重建会话（抓取器未提供 ``reconnect`` 时静默跳过）。

        重建失败**不**顶替原始错误——那会让失败原因变成"重建失败"，
        掩盖真正的首因；下一次尝试会自己暴露新的失败。
        """
        reconnect = getattr(self._fetcher, "reconnect", None)
        if callable(reconnect):
            try:
                reconnect()
            except Exception:  # noqa: BLE001
                pass

    def _fetch_checked(self, task_key: str, call, *,
                       timeout_ms: int, cancel) -> Any:
        """一次逻辑抓取 = 一次经网关取数 +（可疑形态时）一次经网关复核。"""
        first = self._execute_once(task_key, call, timeout_ms=timeout_ms,
                                   cancel=cancel)
        if not truncation_suspect(len(first[1]), source_page_size()):
            return first
        again = self._execute_once(task_key, call, timeout_ms=timeout_ms,
                                   cancel=cancel)
        if result_shape(again) != result_shape(first):
            raise FetchError(
                f"结果集疑似被静默截断：两次取回不一致（{len(first[1])} 行 vs "
                f"{len(again[1])} 行）",
                retryable=True,
            )
        return again

    def _execute_once(self, task_key: str, call, *,
                      timeout_ms: int, cancel) -> Any:
        """经网关取一次数（唯一出口；审计只记字节数，内容不进网关）。"""
        holder: dict[str, Any] = {}

        def _sender(kind, host, timeout):
            _ = (kind, host, timeout)
            from st_agent.l0.net.errors import EgressError, EgressUnavailableError
            try:
                fields, rows = call()
            except FetchUnavailableError as exc:
                holder["error"] = exc
                raise EgressUnavailableError(str(exc) or "数据源不可达") from exc
            except FetchError as exc:
                holder["error"] = exc
                raise EgressError(f"抓取失败：{exc}") from exc
            except Exception as exc:
                # 逃出抓取器自己处理面的裸异常：按网络故障处理（可重试）
                holder["error"] = FetchError(f"抓取失败：{exc}", retryable=True)
                raise EgressError(f"抓取失败：{exc}") from exc
            blob_chars = sum(len(str(cell)) for row in rows for cell in row)
            blob_chars += sum(len(str(field)) for field in fields)
            holder["result"] = (fields, rows)
            return (0, blob_chars, ())

        purpose = f"BaoStock 同步（任务 {task_key}）"
        self._fetch_attempts += 1
        env = self._gateway.execute(
            "data_fetch", BAOSTOCK_HOST,
            initiator=self._initiator, purpose=purpose,
            timeout_ms=timeout_ms, cancel=cancel, sender=_sender,
        )
        if env.status == "ok":
            return holder["result"]
        captured = holder.get("error")
        if env.status == "unavailable":
            if isinstance(captured, FetchUnavailableError):
                raise captured                      # 保留 error_code / retryable
            # 网关自身拦截（离线模式 / 无发包实现）：策略判定，不重试
            raise FetchUnavailableError(env.reason or "数据源不可达")
        if env.status == "failed":
            if isinstance(captured, FetchError):
                detail = (f"{env.reason}（error_code={captured.error_code}"
                          f"，见 {env.log_ref}）" if captured.error_code
                          else f"{env.reason}（见 {env.log_ref}）")
                raise FetchError(
                    detail,
                    error_code=captured.error_code,
                    retryable=captured.retryable,
                )
            raise FetchError(f"{env.reason}（见 {env.log_ref}）")
        raise FetchError(env.reason or f"网关拒绝（{env.status}）")

    # ───────────────────────── 内部：分发抓取与入库 ─────────────────────────

    def _fetch_and_load(self, task_key: str, watermark: str | None, *,
                        day: str | None, codes: list[str] | None,
                        watchlist: list[str] | None,
                        timeout_ms: int, start: str | None,
                        batch_size: int | None, quarters: int | None,
                        cancel) -> tuple[int, str | None, int | None]:
        """分发各任务的抓取 + 入库；返回 ``(入库行数, 新水位, 覆盖对账期望数)``。"""
        today = _now().date().isoformat()
        if task_key == "bs_calendar":
            return self._load_calendar(timeout_ms, cancel)
        if task_key == "bs_security_basic":
            return self._load_security_basic(timeout_ms, cancel)
        if task_key == "bs_all_stock":
            return self._load_all_stock(day or today, timeout_ms, cancel)
        if task_key == "bs_k_daily":
            return self._load_k_daily(watermark, codes, timeout_ms, cancel,
                                      start=start, batch_size=batch_size)
        if task_key == "bs_k_period":
            return self._load_k_period(watermark, codes, timeout_ms, cancel,
                                       start=start, batch_size=batch_size)
        if task_key == "bs_k_minute":
            return self._load_k_minute(watchlist, codes, timeout_ms, cancel)
        if task_key == "bs_adjust_factor":
            return self._load_adjust_factor(codes, timeout_ms, cancel,
                                            start=start, batch_size=batch_size)
        if task_key == "bs_dividend":
            return self._load_dividend(watermark, codes, timeout_ms, cancel,
                                       start=start, batch_size=batch_size)
        if task_key == "bs_fin_quarter":
            return self._load_financial(codes, timeout_ms, cancel,
                                        quarters=quarters)
        if task_key == "bs_perf_express":
            return self._load_express(watermark, codes, timeout_ms, cancel,
                                      start=start, batch_size=batch_size)
        if task_key == "bs_forecast":
            return self._load_forecast(watermark, codes, timeout_ms, cancel,
                                       start=start, batch_size=batch_size)
        if task_key == "bs_industry":
            return self._load_industry(timeout_ms, cancel)
        if task_key in ("bs_sz50", "bs_hs300", "bs_zz500"):
            return self._load_constituents(task_key, timeout_ms, cancel)
        if task_key == "bs_macro_deposit":
            return self._load_macro("deposit", watermark, timeout_ms, cancel)
        if task_key == "bs_macro_loan":
            return self._load_macro("loan", watermark, timeout_ms, cancel)
        if task_key == "bs_macro_reserve":
            return self._load_macro("reserve", watermark, timeout_ms, cancel)
        if task_key == "bs_money_month":
            return self._load_macro("money_month", watermark, timeout_ms, cancel)
        if task_key == "bs_money_year":
            return self._load_macro("money_year", watermark, timeout_ms, cancel)
        raise MarketValidationError(f"任务 {task_key!r} 无执行分发（注册表与引擎不同步）")

    def security_codes(self) -> list[str]:
        """库内主档全量证券代码（``code`` 升序）——长跑脚本据此切分片。

        与 ``run_task(codes=None)`` 的缺省范围**同源**（同一 SQL），故脚本按本方法
        切片跑完后，缺省全量跑与脚本分片跑覆盖同一集合。
        """
        return self._codes(None)

    def _codes(self, codes: list[str] | None) -> list[str]:
        """逐码任务的证券范围（缺省 = 库内主档全量 code 排序）。"""
        if codes is not None:
            return list(codes)
        if not self._db.exists():
            return []
        with self._db.connect() as con:
            rows = con.execute("SELECT code FROM security ORDER BY code").fetchall()
        return [r[0] for r in rows]

    # —— 窗口推导（显式起点覆盖） ——

    @staticmethod
    def _window(task_key: str, watermark: str | None,
                start: str | None) -> tuple[str, str]:
        """任务窗口：给出 ``start`` 时按 ``[start, 今日]``，否则走 05 的水位推导。

        **不改** ``window_for`` 的规则（05 记的水位语义原样保留）——显式起点只是
        多一个入口，供分片长跑绕开「全库水位已到今日 ⇒ 后续分片窗口为空」。
        """
        if start is None:
            return window_for(task_key, watermark)
        return (start, _now().date().isoformat())

    # —— 元信息类 ——

    def _load_calendar(self, timeout_ms: int, cancel) -> tuple[int, str | None, None]:
        fields, rows = self._via_gateway(
            "bs_calendar", lambda: self._fetcher.calendar("1990-01-01", _now().date().isoformat()),
            timeout_ms=timeout_ms, cancel=cancel,
        )
        mapped = map_calendar(fields, rows)
        with self._db.transact() as con:
            con.executemany(
                "INSERT INTO trade_calendar(calendar_date, is_trading_day) VALUES (?, ?) "
                "ON CONFLICT(calendar_date) DO UPDATE SET is_trading_day=excluded.is_trading_day",
                mapped,
            )
            con.commit()
        return (len(mapped), None, None)

    def _load_security_basic(self, timeout_ms: int, cancel) -> tuple[int, str | None, None]:
        fields, rows = self._via_gateway(
            "bs_security_basic", lambda: self._fetcher.security_basic(),
            timeout_ms=timeout_ms, cancel=cancel,
        )
        mapped = map_security_basic(fields, rows)
        with self._db.transact() as con:
            con.executemany(
                "INSERT INTO security(code, code_name, ipo_date, out_date, type, status) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(code) DO UPDATE SET "
                "code_name=excluded.code_name, ipo_date=excluded.ipo_date, "
                "out_date=excluded.out_date, type=excluded.type, status=excluded.status",
                mapped,
            )
            con.commit()
        return (len(mapped), None, None)

    def _load_all_stock(self, day: str, timeout_ms: int, cancel) -> tuple[int, str | None, None]:
        fields, rows = self._via_gateway(
            "bs_all_stock", lambda: self._fetcher.all_stock(day),
            timeout_ms=timeout_ms, cancel=cancel,
        )
        mapped = map_all_stock(fields, rows)
        with self._db.transact() as con:
            con.executemany(
                "INSERT INTO security(code, code_name) VALUES (?, ?) "
                "ON CONFLICT(code) DO UPDATE SET code_name=excluded.code_name",
                mapped,
            )
            con.commit()
        return (len(mapped), day, None)

    # —— 行情类 ——

    def _load_k_daily(self, watermark: str | None, codes: list[str] | None,
                      timeout_ms: int, cancel, *, start: str | None = None,
                      batch_size: int | None = None,
                      ) -> tuple[int, str | None, None]:
        window_start, end = self._window("bs_k_daily", watermark, start)
        total = 0
        writer = _BatchWriter(self._write_k_daily, batch_size)
        for code in self._codes(codes):
            fields, rows = self._via_gateway(
                "bs_k_daily", lambda code=code: self._fetcher.k_daily(
                    code, window_start, end),
                timeout_ms=timeout_ms, cancel=cancel,
            )
            mapped = map_k_daily(fields, rows)
            writer.add(mapped)
            total += len(mapped)
        writer.flush()
        with self._db.connect() as con:
            row = con.execute("SELECT max(trade_date) FROM k_line_daily").fetchone()
        new_watermark = row[0] if row and row[0] else watermark
        return (total, new_watermark, None)

    def _write_k_daily(self, mapped: list[tuple]) -> None:
        with self._db.transact() as con:
            con.executemany(
                "INSERT INTO k_line_daily(code, trade_date, open, high, low, close,"
                " preclose, volume, amount, turn, trade_status, pct_chg, pe_ttm,"
                " pb_mrq, ps_ttm, pcf_ncf_ttm, is_st) VALUES ("
                + ", ".join(["?"] * 17) + ") ON CONFLICT(code, trade_date) DO UPDATE SET "
                "open=excluded.open, high=excluded.high, low=excluded.low, "
                "close=excluded.close, preclose=excluded.preclose, volume=excluded.volume, "
                "amount=excluded.amount, turn=excluded.turn, "
                "trade_status=excluded.trade_status, pct_chg=excluded.pct_chg, "
                "pe_ttm=excluded.pe_ttm, pb_mrq=excluded.pb_mrq, ps_ttm=excluded.ps_ttm, "
                "pcf_ncf_ttm=excluded.pcf_ncf_ttm, is_st=excluded.is_st",
                mapped,
            )
            con.commit()

    def _load_k_period(self, watermark: str | None, codes: list[str] | None,
                       timeout_ms: int, cancel, *, start: str | None = None,
                       batch_size: int | None = None,
                       ) -> tuple[int, str | None, None]:
        window_start = start if start is not None else _load_k_period_start(watermark)
        end = _now().date().isoformat()
        total = 0
        maxima: dict[str, str] = {}
        writer = _BatchWriter(self._write_k_period, batch_size)
        for frequency in ("w", "m"):
            for code in self._codes(codes):
                fields, rows = self._via_gateway(
                    "bs_k_period",
                    lambda code=code, frequency=frequency: self._fetcher.k_period(
                        code, frequency, window_start, end),
                    timeout_ms=timeout_ms, cancel=cancel,
                )
                mapped = map_k_period(fields, rows, frequency)
                writer.add(mapped)
                total += len(mapped)
            writer.flush()          # 取本 frequency 的 max 之前先落库
            with self._db.connect() as con:
                row = con.execute(
                    "SELECT max(trade_date) FROM k_line_period WHERE frequency=?",
                    (frequency,),
                ).fetchone()
                if row and row[0]:
                    maxima[frequency] = row[0]
        new_watermark = ",".join(f"{f}:{maxima[f]}" for f in sorted(maxima)) or watermark
        return (total, new_watermark, None)

    def _write_k_period(self, mapped: list[tuple]) -> None:
        with self._db.transact() as con:
            con.executemany(
                "INSERT INTO k_line_period(code, frequency, trade_date, open, high,"
                " low, close, volume, amount, turn, pct_chg) VALUES ("
                + ", ".join(["?"] * 11) + ") ON CONFLICT(code, frequency, trade_date)"
                " DO UPDATE SET open=excluded.open, high=excluded.high, "
                "low=excluded.low, close=excluded.close, volume=excluded.volume, "
                "amount=excluded.amount, turn=excluded.turn, pct_chg=excluded.pct_chg",
                mapped,
            )
            con.commit()

    def _load_k_minute(self, watchlist: list[str] | None, codes: list[str] | None,
                       timeout_ms: int, cancel) -> tuple[int, str | None, None]:
        pool = watchlist if watchlist is not None else codes
        if not pool:
            raise MarketValidationError(
                "分钟线任务需关注池（watchlist 非空；无关注池请保持禁用）"
            )
        with self._db.connect() as con:
            row = con.execute("SELECT max(bar_start) FROM k_line_minute").fetchone()
        start = (row[0][:10] if row and row[0] else "1990-01-01")
        end = _now().date().isoformat()
        total = 0
        for frequency in ("5", "15", "30", "60"):
            for code in pool:
                fields, rows = self._via_gateway(
                    "bs_k_minute",
                    lambda code=code, frequency=frequency: self._fetcher.k_minute(
                        code, frequency, start, end),
                    timeout_ms=timeout_ms, cancel=cancel,
                )
                mapped = map_k_minute(fields, rows, frequency)
                with self._db.transact() as con:
                    con.executemany(
                        "INSERT INTO k_line_minute(code, frequency, bar_start, open, high,"
                        " low, close, volume, amount) VALUES ("
                        + ", ".join(["?"] * 9) + ") ON CONFLICT(code, frequency, bar_start)"
                        " DO UPDATE SET open=excluded.open, high=excluded.high, "
                        "low=excluded.low, close=excluded.close, volume=excluded.volume, "
                        "amount=excluded.amount",
                        mapped,
                    )
                    con.commit()
                total += len(mapped)
        with self._db.connect() as con:
            row = con.execute("SELECT max(bar_start) FROM k_line_minute").fetchone()
        return (total, (row[0] if row and row[0] else None), None)

    def _load_adjust_factor(self, codes: list[str] | None,
                            timeout_ms: int, cancel, *, start: str | None = None,
                            batch_size: int | None = None,
                            ) -> tuple[int, str | None, None]:
        end = _now().date().isoformat()
        window_start = start or "1990-01-01"   # 本任务无水位（05），缺省＝基线起点
        total = 0
        writer = _BatchWriter(self._write_adjust_factor, batch_size)
        for code in self._codes(codes):
            fields, rows = self._via_gateway(
                "bs_adjust_factor",
                lambda code=code: self._fetcher.adjust_factor(
                    code, window_start, end),
                timeout_ms=timeout_ms, cancel=cancel,
            )
            mapped = map_adjust_factor(fields, rows)
            writer.add(mapped)
            total += len(mapped)
        writer.flush()
        with self._db.connect() as con:
            row = con.execute("SELECT max(ex_date) FROM adjust_factor").fetchone()
        return (total, (row[0] if row and row[0] else None), None)

    def _write_adjust_factor(self, mapped: list[tuple]) -> None:
        with self._db.transact() as con:
            con.executemany(
                "INSERT INTO adjust_factor(code, ex_date, fore_adjust_factor,"
                " back_adjust_factor, adjust_factor) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(code, ex_date) DO UPDATE SET "
                "fore_adjust_factor=excluded.fore_adjust_factor, "
                "back_adjust_factor=excluded.back_adjust_factor, "
                "adjust_factor=excluded.adjust_factor",
                mapped,
            )
            con.commit()

    def _load_dividend(self, watermark: str | None, codes: list[str] | None,
                       timeout_ms: int, cancel, *, start: str | None = None,
                       batch_size: int | None = None,
                       ) -> tuple[int, str | None, None]:
        years = _dividend_years(watermark, start)
        total = 0
        writer = _BatchWriter(self._write_dividend, batch_size)
        for code in self._codes(codes):
            for year in years:
                fields, rows = self._via_gateway(
                    "bs_dividend",
                    lambda code=code, year=year: self._fetcher.dividend(code, year),
                    timeout_ms=timeout_ms, cancel=cancel,
                )
                mapped = map_dividend(fields, rows)
                writer.add(mapped)
                total += len(mapped)
        writer.flush()
        return (total, "+".join(years), None)

    def _write_dividend(self, mapped: list[tuple]) -> None:
        """写一批分红行：有操作日期的走 UPSERT，无操作日期的按公告日判重后插入。"""
        dated = [r for r in mapped if r[6] is not None]
        dateless = [r for r in mapped if r[6] is None]
        with self._db.transact() as con:
            con.executemany(
                "INSERT INTO dividend(code, divid_pre_notice_date,"
                " divid_agm_pum_date, divid_plan_announce_date, divid_plan_date,"
                " divid_regist_date, divid_operate_date, divid_pay_date,"
                " divid_stock_market_date, divid_cash_ps_before_tax,"
                " divid_cash_ps_after_tax, divid_stocks_ps, divid_cash_stock,"
                " divid_reserve_to_stock_ps) VALUES ("
                + ", ".join(["?"] * 14) + ") "
                "ON CONFLICT(code, divid_operate_date) DO UPDATE SET "
                "divid_pre_notice_date=excluded.divid_pre_notice_date, "
                "divid_agm_pum_date=excluded.divid_agm_pum_date, "
                "divid_plan_announce_date=excluded.divid_plan_announce_date, "
                "divid_plan_date=excluded.divid_plan_date, "
                "divid_regist_date=excluded.divid_regist_date, "
                "divid_pay_date=excluded.divid_pay_date, "
                "divid_stock_market_date=excluded.divid_stock_market_date, "
                "divid_cash_ps_before_tax=excluded.divid_cash_ps_before_tax, "
                "divid_cash_ps_after_tax=excluded.divid_cash_ps_after_tax, "
                "divid_stocks_ps=excluded.divid_stocks_ps, "
                "divid_cash_stock=excluded.divid_cash_stock, "
                "divid_reserve_to_stock_ps=excluded.divid_reserve_to_stock_ps",
                dated,
            )
            for record in dateless:
                hit = con.execute(
                    "SELECT dividend_id FROM dividend WHERE code=? "
                    "AND divid_plan_announce_date IS ?",
                    (record[0], record[3]),
                ).fetchone()
                if hit is None:
                    con.execute(
                        "INSERT INTO dividend(code, divid_pre_notice_date,"
                        " divid_agm_pum_date, divid_plan_announce_date, divid_plan_date,"
                        " divid_regist_date, divid_operate_date, divid_pay_date,"
                        " divid_stock_market_date, divid_cash_ps_before_tax,"
                        " divid_cash_ps_after_tax, divid_stocks_ps, divid_cash_stock,"
                        " divid_reserve_to_stock_ps) VALUES ("
                        + ", ".join(["?"] * 14) + ")",
                        record,
                    )
            con.commit()

    def _load_financial(self, codes: list[str] | None,
                        timeout_ms: int, cancel, *,
                        quarters: int | None = None) -> tuple[int, str | None, None]:
        plan = last_n_quarters(quarters) if quarters else last_n_quarters(8)
        merged: dict[tuple[str, str], dict[str, Any]] = {}
        for year, quarter in plan:
            for which in ("profit", "operation", "growth", "balance", "cash", "dupont"):
                for code in self._codes(codes):
                    fields, rows = self._via_gateway(
                        "bs_fin_quarter",
                        lambda code=code, year=year, quarter=quarter, which=which:
                        self._fetcher.financial(code, year, quarter, which),
                        timeout_ms=timeout_ms, cancel=cancel,
                    )
                    for record in map_financial(which, fields, rows):
                        key = (record["code"], record["stat_date"])
                        slot = merged.setdefault(key, {})
                        for column, value in record.items():
                            if column == "pub_date":
                                prev = slot.get("pub_date")
                                if value is not None and (prev is None or value > prev):
                                    slot["pub_date"] = value
                            elif value is not None:
                                slot[column] = value
        ordered = [c for c in FIN_QUARTER_COLUMNS if c not in ("code", "stat_date")]
        payload = [
            tuple([code, stat] + [slot.get(c) for c in ordered])
            for (code, stat), slot in sorted(merged.items())
        ]
        set_clause = ", ".join(f"{c}=excluded.{c}" for c in ordered)
        with self._db.transact() as con:
            con.executemany(
                f"INSERT INTO financial_quarter(code, stat_date, {', '.join(ordered)}) "
                f"VALUES ({', '.join(['?'] * (2 + len(ordered)))}) "
                f"ON CONFLICT(code, stat_date) DO UPDATE SET {set_clause}, "
                "updated_at=datetime('now')",
                payload,
            )
            con.commit()
            row = con.execute("SELECT max(stat_date) FROM financial_quarter").fetchone()
        return (len(payload), (row[0] if row and row[0] else None), None)

    # —— 公司报告类 ——

    def _load_express(self, watermark: str | None, codes: list[str] | None,
                      timeout_ms: int, cancel, *, start: str | None = None,
                      batch_size: int | None = None,
                      ) -> tuple[int, str | None, None]:
        window_start, end = self._window("bs_perf_express", watermark, start)
        total = 0
        writer = _BatchWriter(self._write_express, batch_size)
        for code in self._codes(codes):
            fields, rows = self._via_gateway(
                "bs_perf_express",
                lambda code=code: self._fetcher.performance_express(
                    code, window_start, end),
                timeout_ms=timeout_ms, cancel=cancel,
            )
            mapped = map_performance_express(fields, rows)
            writer.add(mapped)
            total += len(mapped)
        writer.flush()
        return (total, window_start, None)

    def _write_express(self, mapped: list[tuple]) -> None:
        with self._db.transact() as con:
            con.executemany(
                "INSERT INTO performance_express(code, stat_date, pub_date, update_date,"
                " total_asset, net_asset, eps_chg_pct, roe_wa, eps_diluted, gr_yoy, op_yoy)"
                " VALUES (" + ", ".join(["?"] * 11) + ") "
                "ON CONFLICT(code, stat_date) DO UPDATE SET "
                "pub_date=excluded.pub_date, update_date=excluded.update_date, "
                "total_asset=excluded.total_asset, net_asset=excluded.net_asset, "
                "eps_chg_pct=excluded.eps_chg_pct, roe_wa=excluded.roe_wa, "
                "eps_diluted=excluded.eps_diluted, gr_yoy=excluded.gr_yoy, "
                "op_yoy=excluded.op_yoy",
                mapped,
            )
            con.commit()

    def _load_forecast(self, watermark: str | None, codes: list[str] | None,
                       timeout_ms: int, cancel, *, start: str | None = None,
                       batch_size: int | None = None,
                       ) -> tuple[int, str | None, None]:
        window_start, end = self._window("bs_forecast", watermark, start)
        total = 0
        writer = _BatchWriter(self._write_forecast, batch_size)
        for code in self._codes(codes):
            fields, rows = self._via_gateway(
                "bs_forecast",
                lambda code=code: self._fetcher.forecast(code, window_start, end),
                timeout_ms=timeout_ms, cancel=cancel,
            )
            mapped = map_forecast(fields, rows)
            writer.add(mapped)
            total += len(mapped)
        writer.flush()
        return (total, window_start, None)

    def _write_forecast(self, mapped: list[tuple]) -> None:
        with self._db.transact() as con:
            con.executemany(
                "INSERT INTO profit_forecast(code, stat_date, pub_date, forecast_type,"
                " abstract, chg_pct_up, chg_pct_dwn) VALUES ("
                + ", ".join(["?"] * 7) + ") ON CONFLICT(code, stat_date) DO UPDATE SET "
                "pub_date=excluded.pub_date, forecast_type=excluded.forecast_type, "
                "abstract=excluded.abstract, chg_pct_up=excluded.chg_pct_up, "
                "chg_pct_dwn=excluded.chg_pct_dwn",
                mapped,
            )
            con.commit()

    # —— 板块类 ——

    def _load_industry(self, timeout_ms: int, cancel) -> tuple[int, str | None, None]:
        fields, rows = self._via_gateway(
            "bs_industry", lambda: self._fetcher.industry(),
            timeout_ms=timeout_ms, cancel=cancel,
        )
        mapped = map_industry(fields, rows)
        with self._db.transact() as con:
            con.executemany(
                "INSERT OR IGNORE INTO stock_industry(code, update_date, code_name,"
                " industry, industry_classification) VALUES (?, ?, ?, ?, ?)",
                mapped,
            )
            con.commit()
            row = con.execute("SELECT max(update_date) FROM stock_industry").fetchone()
        return (len(mapped), (row[0] if row and row[0] else None), None)

    def _load_constituents(self, task_key: str,
                           timeout_ms: int, cancel) -> tuple[int, str | None, None]:
        index_key = {"bs_sz50": "sz50", "bs_hs300": "hs300", "bs_zz500": "zz500"}[task_key]
        fields, rows = self._via_gateway(
            task_key, lambda: self._fetcher.constituents(index_key),
            timeout_ms=timeout_ms, cancel=cancel,
        )
        mapped = map_constituent(fields, rows, index_key)
        with self._db.transact() as con:
            con.executemany(
                "INSERT OR IGNORE INTO index_constituent(index_key, code, update_date,"
                " code_name) VALUES (?, ?, ?, ?)",
                mapped,
            )
            con.commit()
            row = con.execute(
                "SELECT max(update_date) FROM index_constituent WHERE index_key=?",
                (index_key,),
            ).fetchone()
        return (len(mapped), (row[0] if row and row[0] else None), None)

    # —— 宏观类 ——

    def _load_macro(self, which: str, watermark: str | None,
                    timeout_ms: int, cancel) -> tuple[int, str | None, None]:
        fetcher_map = {
            "deposit": (self._fetcher.macro_deposit, map_macro_deposit,
                        "INSERT OR REPLACE INTO macro_deposit_rate(pub_date, demand,"
                        " fixed_3m, fixed_6m, fixed_1y, fixed_2y, fixed_3y, fixed_5y,"
                        " installment_1y, installment_3y, installment_5y) VALUES ("
                        + ", ".join(["?"] * 11) + ")"),
            "loan": (self._fetcher.macro_loan, map_macro_loan,
                     "INSERT OR REPLACE INTO macro_loan_rate(pub_date, loan_6m, loan_6m_1y,"
                     " loan_1y_3y, loan_3y_5y, loan_above_5y, mortgage_below_5y,"
                     " mortgage_above_5y) VALUES (" + ", ".join(["?"] * 8) + ")"),
            "reserve": (self._fetcher.macro_reserve, map_macro_reserve,
                        "INSERT OR REPLACE INTO macro_reserve_ratio(pub_date, effective_date,"
                        " big_pre, big_after, medium_pre, medium_after) VALUES ("
                        + ", ".join(["?"] * 6) + ")"),
            "money_month": (self._fetcher.macro_money_month, map_macro_money_month,
                            "INSERT OR REPLACE INTO macro_money_supply_month(stat_year,"
                            " stat_month, m0, m0_yoy, m0_chain, m1, m1_yoy, m1_chain,"
                            " m2, m2_yoy, m2_chain) VALUES (" + ", ".join(["?"] * 11) + ")"),
            "money_year": (self._fetcher.macro_money_year, map_macro_money_year,
                           "INSERT OR REPLACE INTO macro_money_supply_year(stat_year,"
                           " m0, m0_yoy, m1, m1_yoy, m2, m2_yoy) VALUES ("
                           + ", ".join(["?"] * 7) + ")"),
        }
        task_key = {"deposit": "bs_macro_deposit", "loan": "bs_macro_loan",
                    "reserve": "bs_macro_reserve", "money_month": "bs_money_month",
                    "money_year": "bs_money_year"}[which]
        fetch, mapper, sql = fetcher_map[which]
        start, end = window_for(task_key, watermark)
        if which in ("deposit", "loan"):
            fields, rows = self._via_gateway(
                task_key, lambda: fetch(start, end), timeout_ms=timeout_ms, cancel=cancel)
        else:
            fields, rows = self._via_gateway(
                task_key, lambda: fetch(), timeout_ms=timeout_ms, cancel=cancel)
        mapped = mapper(fields, rows)
        with self._db.transact() as con:
            con.executemany(sql, mapped)
            con.commit()
        return (len(mapped), None, None)

    # ───────────────────────── 内部：完整性校验（05 五项） ─────────────────────────

    def _integrity_checks(self, task_key: str,
                          expected: int | None) -> list[str]:
        """跑 05 完整性校验中与本任务相关的项；返回失败项名（空 = 全过）。"""
        failed: list[str] = []
        if not self._db.exists():
            return [f"{task_key}: 库缺失，校验无从谈起"]
        with self._db.connect() as con:
            if task_key in ("bs_k_daily", "bs_all_stock", "bs_calendar"):
                failed.extend(self._check_coverage(con, expected))
                failed.extend(self._check_calendar_consistency(con))
            if task_key == "bs_fin_quarter":
                failed.extend(self._check_quarter_ends(con))
            if task_key == "bs_k_daily":
                failed.extend(self._check_halted_shape(con))
            failed.extend(self._check_foreign_keys(con))
        return failed

    @staticmethod
    def _check_coverage(con: sqlite3.Connection, expected: int | None) -> list[str]:
        """校验 1：某交易日入库证券数 vs 源全量证券数（`expected` 缺失时跳过）。"""
        if expected is None:
            return []
        row = con.execute("SELECT max(trade_date) FROM k_line_daily").fetchone()
        if not row or not row[0]:
            return []
        count = con.execute(
            "SELECT count(DISTINCT code) FROM k_line_daily WHERE trade_date=?",
            (row[0],),
        ).fetchone()[0]
        if count != expected:
            return [f"行情覆盖对账：{row[0]} 入库 {count} 家，源全量 {expected} 家"]
        return []

    @staticmethod
    def _check_calendar_consistency(con: sqlite3.Connection) -> list[str]:
        """校验 2：有行情的日期必须是交易日。"""
        rows = con.execute(
            "SELECT DISTINCT k.trade_date FROM k_line_daily k LEFT JOIN trade_calendar c "
            "ON c.calendar_date = k.trade_date "
            "WHERE c.is_trading_day IS NULL OR c.is_trading_day = 0 LIMIT 5"
        ).fetchall()
        if rows:
            sample = ", ".join(r[0] for r in rows)
            return [f"交易日历一致性：行情日期非交易日（{sample}）"]
        return []

    @staticmethod
    def _check_foreign_keys(con: sqlite3.Connection) -> list[str]:
        """校验 3：外键孤儿行必须为 0。"""
        rows = con.execute("PRAGMA foreign_key_check").fetchall()
        if rows:
            return [f"外键完整性：孤儿行 {len(rows)} 条"]
        return []

    @staticmethod
    def _check_quarter_ends(con: sqlite3.Connection) -> list[str]:
        """校验 4：`stat_date` 只能是季度末日。"""
        rows = con.execute(
            "SELECT DISTINCT stat_date FROM financial_quarter "
            "WHERE substr(stat_date, 6) NOT IN ('03-31','06-30','09-30','12-31') LIMIT 5"
        ).fetchall()
        if rows:
            sample = ", ".join(r[0] for r in rows)
            return [f"财务键域合法性：非季度末 stat_date（{sample}）"]
        return []

    @staticmethod
    def _check_halted_shape(con: sqlite3.Connection) -> list[str]:
        """校验 5：停牌行（`trade_status=0`）`volume` 应为 0。"""
        count = con.execute(
            "SELECT count(*) FROM k_line_daily WHERE trade_status = 0 AND volume > 0"
        ).fetchone()[0]
        if count:
            return [f"停牌行形态：{count} 行停牌但成交量非 0"]
        return []


def _load_k_period_start(watermark: str | None) -> str:
    """周月线拉取起点（水位串 ``w:<d>,m:<d>`` 取较早者；缺失 → 基线起点）。"""
    if not watermark:
        return "1990-12-19"
    days = [part.split(":")[-1] for part in watermark.split(",") if ":" in part]
    days = [d for d in days if len(d) == 10]
    if not days:
        return "1990-12-19"
    start = min(days)
    try:
        from st_agent.l0.market.tasks import next_day
        return next_day(start)
    except ValueError:
        return "1990-12-19"


def _dividend_years(watermark: str | None, start: str | None) -> list[str]:
    """分红拉取年份（降序）。

    给出显式起点时按「起点年 → 今年」；否则走水位年份（``2024+2025`` 形），
    水位缺失＝今年与前一年（与既有口径一致）。
    """
    if start is not None:
        first = int(start[:4])
        return [str(y) for y in range(_now().year, first - 1, -1)]
    years = sorted({y.strip() for y in (watermark or "").split("+")
                    if y.strip().isdigit()}, reverse=True)
    if years:
        return years
    return [str(_now().year), str(_now().year - 1)]
