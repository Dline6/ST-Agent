"""L6 出厂重置（[08 §6](../../../docs/技术架构-v2/08-L6-反思演进.md) 后半）。

[08 §6](../../../docs/技术架构-v2/08-L6-反思演进.md) 原文：「『回滚到出厂设置』（三次确认）：
演进状态清空——Skill 参数恢复默认、A/B 实验停止、授权档复位；**Memory 原始数据保留**；
变更历史归档可查。」

四条口径：

- **三次确认**——不足三次**即拒**：不留痕、不执行任何动作、不留「做了一半」的状态。
- **「Skill 参数恢复默认」＝逆序回放演进变更**——条目的 `default` 槽存的是**当前取值**
  （[01 §7](../../../docs/技术架构-v2/01-平台共享契约.md) 落值语义），故「出厂默认」不能由门面泛化给出；
  以**演进变更历史**为据、经**变更流回放** `old_value`（每条各自留痕）是本条的**唯一**可行口径，
  且**不波及**与演进无关的条目（`retention.*` / `mcp-hub/` 等）。
- **Memory 保留**——重置**不触碰** `memory` 分区、**不调用**任何删除面（L2 的删除面是
  **用户显式删除**的通道，本流程**不得借道**；[08 §7](../../../docs/技术架构-v2/08-L6-反思演进.md) 红线）。
- **变更历史归档而非抹除**——重置前的全部演进变更**仍可查**；重置本身另留一条痕。
  `reset_id` 走**本层确定性摘要**（`digest_id("rst", …)`，同 [T-L6-001.1](../../项目管理/tasks/T-L6-001.1-反思数据池.md)
  的池子口径）——L6 **不铸**任何契约 ID（`new_id` 全层零出现，由 `tests/l6/test_pool.py` 的守卫钉住）。

本模块**不做**档位判定（[`authorization.py`](authorization.py)）与变更流本身（[`change.py`](change.py)）。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from st_agent.contracts.identifiers import digest_id
from st_agent.l6.authorization import DEFAULT_TIER
from st_agent.l6.change_store import EvolutionChangeStore
from st_agent.l6.errors import FactoryResetError

__all__ = [
    "REPLAY_NOTE",
    "RESET_CONFIRMATIONS",
    "FactoryReset",
    "ResetOutcome",
]

RESET_CONFIRMATIONS = 3
"""「回滚到出厂设置」的确认次数（[08 §6](../../../docs/技术架构-v2/08-L6-反思演进.md) 明文「三次确认」）。"""

REPLAY_NOTE = "回放经变更流（08 §6：重置本身也走 §5 的唯一通道）"


class ResetOutcome(BaseModel):
    """一次出厂重置的结论（**未执行也显式**——原因随行，不静默）。"""

    model_config = ConfigDict(frozen=True)

    performed: bool
    reason: str = ""
    reset_id: str = ""
    replayed: tuple[str, ...] = ()
    """被回放的演进变更 `change_id`（按回放顺序；**逆序**＝由新到旧）。"""
    replay_noops: tuple[str, ...] = ()
    """取值已等于回滚值、未产生新留痕的变更（如实列出，不谎称已回放）。"""
    experiments_stopped: tuple[str, ...] = ()
    tier_reset: bool = False
    memory_note: str = "Memory 原始数据不在本流程的触及范围内（08 §6：原始数据保留）"


def _system_now() -> datetime:
    """用户本地时区当前时刻（[01 §8](../../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    return datetime.now().astimezone()


class FactoryReset:
    """出厂重置面（三次确认 → 演进状态清空 → 留痕）。

    :param change_flow: 变更流面（[`EvolutionChangeFlow`](change.py) 的 `history` / `rollback`）；
        缺省 ``None`` ⇒ **拒执行**并点名（不假装重置）
    :param authorization: 演进授权面（[`EvolutionAuthorization`](authorization.py) 的 `set_tier`）；
        缺省 ``None`` ⇒ 档位复位**跳过并如实说明**（不谎称已复位）
    :param experiments: A/B 实验面（[`ExperimentConsole`](experiment.py) 的 `running` / `abandon`）；
        缺省 ``None`` ⇒ 实验停止**跳过并如实说明**
    :param store: ``Store`` 句柄（重置留痕落 `reflection/reset/`）；缺省 ``None`` ＝纯内存态
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    def __init__(
        self,
        *,
        change_flow: Any = None,
        authorization: Any = None,
        experiments: Any = None,
        store: Any = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._flow = change_flow
        self._authorization = authorization
        self._experiments = experiments
        self._store = EvolutionChangeStore(store, now=now)
        self._now = _system_now if now is None else now

    # ───────────────────────── 执行 ─────────────────────────

    def request(self, confirmations: int, *, now: datetime | None = None) -> ResetOutcome:
        """按确认次数执行（或拒绝）出厂重置。

        :raises FactoryResetError: 未接入变更流（**拒执行**，不假装重置）
        """
        if isinstance(confirmations, bool) or not isinstance(confirmations, int):
            raise FactoryResetError(f"确认次数须为整数，收到 {confirmations!r}")
        if confirmations < RESET_CONFIRMATIONS:
            return ResetOutcome(
                performed=False,
                reason=f"确认次数不足（{confirmations}/{RESET_CONFIRMATIONS}），"
                       "未执行任何动作、未留痕（08 §6）",
            )
        if self._flow is None:
            raise FactoryResetError(
                "未接入变更流，不予重置（08 §6 的「Skill 参数恢复默认」经 §5 变更流回放）"
            )
        moment = self._now() if now is None else now
        replayed, noops = self._replay(moment)
        stopped = self._stop_experiments()
        tier_reset = self._reset_tier()
        reset_id = digest_id("rst", moment.isoformat(), *(replayed + noops + stopped))
        stored = ResetOutcome(
            performed=True,
            reason="出厂重置已执行：演进状态清空、Memory 原始数据保留（08 §6）",
            reset_id=reset_id, replayed=tuple(replayed), replay_noops=tuple(noops),
            experiments_stopped=tuple(stopped), tier_reset=tier_reset,
        )
        self._store.put_reset(reset_id, {
            **stored.model_dump(mode="json"),
            "requested_at": moment.isoformat(),
            "confirmations": confirmations,
        })
        return stored

    # ───────────────────────── 读面 ─────────────────────────

    def stored(self, reset_id: str) -> ResetOutcome | None:
        """取一条重置留痕（不存在 → ``None``；损坏 → 抛）。"""
        raw = self._store.get_reset(reset_id)
        if raw is None:
            return None
        try:
            return ResetOutcome(**raw)
        except Exception as exc:                       # noqa: BLE001 - 统一转本面失败形态
            raise FactoryResetError(f"重置留痕形态损坏（{reset_id}）：{exc}") from exc

    def history(self) -> tuple[ResetOutcome, ...]:
        """全部重置留痕（按落盘顺序；无 → 空集）。"""
        out: list[ResetOutcome] = []
        for raw in self._store.all_resets():
            try:
                out.append(ResetOutcome(**raw))
            except Exception as exc:                   # noqa: BLE001
                raise FactoryResetError(f"重置留痕形态损坏：{exc}") from exc
        return tuple(sorted(out, key=lambda r: (r.reset_id,)))

    # ───────────────────────── 内部 ─────────────────────────

    def _replay(self, moment: datetime) -> tuple[list[str], list[str]]:
        """**逆序**回放全部演进变更（由新到旧；已经回滚过的不重复回放）。"""
        replayed: list[str] = []
        noops: list[str] = []
        pending = [c for c in self._flow.history() if c.rolled_back_at is None]
        for change in reversed(pending):
            run = self._flow.rollback(change.change_id)
            if run.applied:
                replayed.append(change.change_id)
            else:
                noops.append(change.change_id)
        return replayed, noops

    def _stop_experiments(self) -> list[str]:
        if self._experiments is None:
            return []
        try:
            running = tuple(self._experiments.running())
        except Exception as exc:                       # noqa: BLE001
            raise FactoryResetError(f"实验读面不可用（{exc}）") from exc
        stopped: list[str] = []
        for experiment in running:
            self._experiments.abandon(experiment.experiment_id)
            stopped.append(experiment.experiment_id)
        return stopped

    def _reset_tier(self) -> bool:
        if self._authorization is None:
            return False
        change = self._authorization.set_tier(DEFAULT_TIER)
        return change is not None
