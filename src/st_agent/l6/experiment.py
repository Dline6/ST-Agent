"""L6 A/B 实验（[08 §4](../../../docs/技术架构-v2/08-L6-反思演进.md)）。

实验结构 ＝ ``{hypothesis, scope, sample, result, decision}``（[08 §4](../../../docs/技术架构-v2/08-L6-反思演进.md)
原文即此五字段），**仅适用于低风险范围**（推送策略 / 视角组合），启停结算**全程留痕可查**，
效果判断**保守**，启用受 [08 §5](../../../docs/技术架构-v2/08-L6-反思演进.md) 授权档位约束。

五条口径：

- **启用门经注入的授权面、缺省不启用**——[08 §5](../../../docs/技术架构-v2/08-L6-反思演进.md) 的授权档位
  （`manual` / `collaborative` / `autonomous`）本体归 [T-L6-003](../项目管理/tasks/T-L6-003-演进授权与变更流回滚与出厂重置.md)，
  本面只消费**注入的授权面**。**未注入即不启用**（返回 `enabled=False` ＋原因、**不落盘**）——
  否则等于在授权档位落地前造出一条自行开跑的路径（[08 §7](../../docs/技术架构-v2/08-L6-反思演进.md)
  红线的延伸）。
- **范围是闭集**——不在 :data:`LOW_RISK_SCOPES` 内的范围**显式拒**（:class:`ExperimentError`），
  **不静默放行**；清单只登 [08 §4](../../docs/技术架构-v2/08-L6-反思演进.md) 原文点名的两类。
- **保守判定、不做统计显著性强断言**——:func:`judge` 是**纯函数**：样本数低于下限、或用户反馈
  未明确偏向任一侧 ⇒ `inconclusive`；只有用户反馈**明确偏向**且样本达下限才给 `adopt` / `revert`。
  判据里**没有**显著性检验，也不产出 p 值 / 置信区间之类断言（本地单用户小样本下那些说法没有依据）。
- **留痕即事实、重启安全**——实验落在 ``reflection/experiments/<experiment_id>.json``（**不新造分区**），
  读面（:meth:`ExperimentConsole.all` / :meth:`ExperimentConsole.running`）在**本层**即成立
  「用户可查看实验日志」；重复结算同一结果**幂等**（不追加第二份）。
- **本面不发布事件、不生效任何配置**——「每次变更经 L5 显式告知」与「A/B 实验停止」的出厂重置
  归 [T-L6-003](../项目管理/tasks/T-L6-003-演进授权与变更流回滚与出厂重置.md)；本面只交启停 / 结算 /
  判定的**面**供其调用。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from st_agent.contracts.identifiers import digest_id
from st_agent.contracts.neutrality import NeutralityGuard
from st_agent.l6.errors import ExperimentError
from st_agent.l6.experiment_store import ExperimentStore

__all__ = [
    "AUTHORIZER_ABSENT_REASON",
    "DEFAULT_MIN_SAMPLE",
    "LOW_RISK_SCOPES",
    "NOT_PERMITTED_REASON",
    "Experiment",
    "ExperimentConsole",
    "ExperimentStart",
    "judge",
]

LOW_RISK_SCOPES: tuple[str, ...] = ("delivery-strategy", "lens-composition")
"""低风险范围闭集（[08 §4](../../docs/技术架构-v2/08-L6-反思演进.md) 原文「仅适用于推送策略 / 视角组合等低风险参数」）。

更细的**风险分级清单**（哪些参数可自主改、哪些必须协作）属 [08 §5](../../docs/技术架构-v2/08-L6-反思演进.md)，
归 [T-L6-003](../项目管理/tasks/T-L6-003-演进授权与变更流回滚与出厂重置.md)；本面只认这个闭集。
"""

AUTHORIZER_ABSENT_REASON = "未注入授权面，实验未启用（08 §4 末条 / §5 授权档位）"

NOT_PERMITTED_REASON = "授权面未放行该范围的实验（08 §5 授权档位）"

DEFAULT_MIN_SAMPLE = 5
"""保守判定的样本下限（低于它一律 `inconclusive`——本地单用户小样本下不给强断言）。"""

ExperimentStatus = Literal["running", "settled", "decided"]
ExperimentDecision = Literal["pending", "adopt", "revert", "inconclusive"]

class Experiment(BaseModel):
    """一次 A/B 实验（[08 §4](../../docs/技术架构-v2/08-L6-反思演进.md) 的五字段 + 时刻与状态）。"""

    model_config = ConfigDict(frozen=True)

    experiment_id: str
    hypothesis: str
    """实验假设（**生成性文案**，过 [01 §6](../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    scope: str
    """实验范围（限 :data:`LOW_RISK_SCOPES`）。"""
    sample: Mapping[str, Any] = Field(default_factory=dict)
    """样本口径（规模 / 覆盖对象等；结构由产出方给出，本面只承载、不解释）。"""
    result: Mapping[str, Any] | None = None
    """结算结果（**未结算为 `None`**）。本面只读其中两个约定键：
    ``size``（有效样本数）与 ``preference``（`new` / `baseline` / 其他），其余键原样留存。"""
    decision: ExperimentDecision = "pending"
    reason: str = ""
    """判定理由（**生成性文案**，过 [01 §6](../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    status: ExperimentStatus = "running"
    started_at: datetime
    settled_at: datetime | None = None
    decided_at: datetime | None = None
    trace_ref: str = ""
    """溯源锚点（[01 §4](../../docs/技术架构-v2/01-平台共享契约.md)）。"""

    @property
    def record(self) -> Mapping[str, Any]:
        """五字段视图（`hypothesis` / `scope` / `sample` / `result` / `decision`）——
        [08 §4](../../docs/技术架构-v2/08-L6-反思演进.md) 的实验结构逐项可读。"""
        return {
            "hypothesis": self.hypothesis, "scope": self.scope, "sample": self.sample,
            "result": self.result, "decision": self.decision,
        }


class ExperimentStart(BaseModel):
    """一次启用的结论（**未启用也显式**——原因随行，不静默）。"""

    model_config = ConfigDict(frozen=True)

    enabled: bool
    reason: str = ""
    experiment: Experiment | None = None


def judge(
    result: Mapping[str, Any] | None, *, min_sample: int = DEFAULT_MIN_SAMPLE
) -> tuple[ExperimentDecision, str]:
    """**保守判定**（纯函数）：样本是否够、用户反馈是否明确偏向。

    :return: ``(decision, reason)``——`inconclusive` 的两条来路（样本不足 / 反馈未偏向）
        各有自己的中性理由，不合并成一句。
    """
    if not isinstance(result, Mapping):
        return "inconclusive", "尚无结算结果，不做判定（08 §4：效果判断保守）"
    size = _as_int(result.get("size"))
    if size < min_sample:
        return "inconclusive", f"有效样本 {size} 低于下限 {min_sample}，不做判定（08 §4：本地小样本不加强断言）"
    preference = str(result.get("preference") or "").strip()
    if preference == "new":
        return "adopt", "用户反馈明确偏向新策略，采用（08 §4：用户反馈优先）"
    if preference == "baseline":
        return "revert", "用户反馈明确偏向原策略，回退（08 §4：用户反馈优先）"
    return "inconclusive", "用户反馈未明确偏向任一侧，不做判定（08 §4：效果判断保守）"


class ExperimentConsole:
    """A/B 实验面（启停 / 结算 / 判定 / 读面）。

    :param store: ``Store`` 句柄；缺省 ``None`` ＝**纯内存态**（可读不可持久）
    :param authorizer: **授权面**鸭子端口（``permits(scope) -> bool``，[08 §5] 授权档位的消费口）；
        缺省 ``None`` ⇒ **不启用**（`enabled=False` ＋原因、**不落盘**）
    :param min_sample: 保守判定的样本下限（缺省 :data:`DEFAULT_MIN_SAMPLE`）
    :param guard: 中性化守卫（缺省用模块级统一件；注入以便测试固定规则库）
    :param now: 取时函数（测试注入固定时钟；缺省本机当前时刻）
    """

    def __init__(
        self,
        *,
        store: Any = None,
        authorizer: Any = None,
        min_sample: int = DEFAULT_MIN_SAMPLE,
        guard: NeutralityGuard | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = ExperimentStore(store, now=now) if store is not None else None
        self._authorizer = authorizer
        if isinstance(min_sample, bool) or not isinstance(min_sample, int) or min_sample < 0:
            raise ExperimentError(f"样本下限须为非负整数，收到 {min_sample!r}")
        self._min_sample = min_sample
        self._guard = guard if guard is not None else NeutralityGuard()
        self._now = _system_now if now is None else now
        self._memory: dict[str, dict[str, Any]] = {}     # 纯内存态（无 Store 时）

    # ───────────────────────── 启用 ─────────────────────────

    def start(
        self, hypothesis: str, scope: str, *, sample: Mapping[str, Any] | None = None,
        trace_ref: str = "", now: datetime | None = None,
    ) -> ExperimentStart:
        """启用一次实验（**低风险范围 + 授权面放行**两道门）。

        :raises ExperimentError: 假设为空 · 范围不在 :data:`LOW_RISK_SCOPES` 内
            （**显式拒，不静默放行**）
        """
        text = (hypothesis or "").strip()
        if not text:
            raise ExperimentError("实验须给出假设（08 §4 的五字段之一）")
        if scope not in LOW_RISK_SCOPES:
            raise ExperimentError(
                f"范围 {scope!r} 不在低风险清单内（{list(LOW_RISK_SCOPES)}，08 §4）——"
                "实验仅适用于推送策略 / 视角组合等低风险参数"
            )
        moment = self._now() if now is None else now
        if self._authorizer is None:
            return ExperimentStart(enabled=False, reason=AUTHORIZER_ABSENT_REASON)
        try:
            permitted = bool(self._authorizer.permits(scope))
        except Exception as exc:
            raise ExperimentError(f"授权面不可用（{exc}）") from exc
        if not permitted:
            return ExperimentStart(enabled=False, reason=NOT_PERMITTED_REASON)
        self._require_neutral(text, "实验假设")
        experiment = Experiment(
            experiment_id=digest_id("exp", scope, text), hypothesis=text, scope=scope,
            sample=dict(sample or {}), started_at=moment, trace_ref=trace_ref,
        )
        self._persist(experiment)
        return ExperimentStart(enabled=True, experiment=experiment)

    # ───────────────────────── 结算与判定 ─────────────────────────

    def settle(
        self, experiment_id: str, *, result: Mapping[str, Any], now: datetime | None = None
    ) -> Experiment:
        """结算一次实验（写 `result`；**同结果重复结算幂等**，不追加第二份留痕）。"""
        current = self._require(experiment_id)
        if current.result is not None and dict(current.result) == dict(result):
            return current                        # 同结果重放：留痕一字不改（幂等）
        moment = self._now() if now is None else now
        updated = current.model_copy(update={
            "result": dict(result), "settled_at": moment,
            "status": "decided" if current.status == "decided" else "settled",
        })
        self._persist(updated)
        return updated

    def decide(
        self, experiment_id: str, *, decision: ExperimentDecision | None = None,
        now: datetime | None = None,
    ) -> Experiment:
        """判定一次实验（不给 `decision` 即按 :func:`judge` 的**保守**判据）。

        :raises ExperimentError: 尚未结算（无 `result` 不得判定）· `decision` 取值非法
        """
        current = self._require(experiment_id)
        moment = self._now() if now is None else now
        if current.result is None:
            raise ExperimentError(
                f"实验 {experiment_id!r} 尚未结算，不予判定（08 §4 五字段须齐备）"
            )
        if decision is None:
            chosen, reason = judge(current.result, min_sample=self._min_sample)
        else:
            if decision not in ("adopt", "revert", "inconclusive"):
                raise ExperimentError(f"未知判定 {decision!r}（adopt / revert / inconclusive）")
            chosen, reason = decision, _DECISION_REASONS[decision]
        self._require_neutral(reason, "实验判定理由")
        updated = current.model_copy(update={
            "decision": chosen, "reason": reason, "decided_at": moment, "status": "decided",
        })
        self._persist(updated)
        return updated

    # ───────────────────────── 读面 ─────────────────────────

    def get(self, experiment_id: str) -> Experiment | None:
        """取一条实验（不存在 → ``None``；落盘损坏 → :class:`ExperimentError`）。"""
        raw = self._store.get(experiment_id) if self._store is not None else self._memory.get(experiment_id)
        if raw is None:
            return None
        try:
            return Experiment(**raw)
        except ValidationError as exc:
            raise ExperimentError(f"实验记录形态损坏（{experiment_id}）：{exc}") from exc

    def all(self) -> tuple[Experiment, ...]:
        """全部实验（按（开始时刻, 标识）升序；无 → 空集，**不报错**）。"""
        raws = self._store.all() if self._store is not None else tuple(self._memory.values())
        out: list[Experiment] = []
        for raw in raws:
            try:
                out.append(Experiment(**raw))
            except ValidationError as exc:
                raise ExperimentError(f"实验记录形态损坏：{exc}") from exc
        return tuple(sorted(out, key=lambda e: (e.started_at, e.experiment_id)))

    def running(self) -> tuple[Experiment, ...]:
        """仍在跑的实验（`status == "running"`）。"""
        return tuple(e for e in self.all() if e.status == "running")

    # ───────────────────────── 内部 ─────────────────────────

    def _require(self, experiment_id: str) -> Experiment:
        current = self.get(experiment_id)
        if current is None:
            raise ExperimentError(f"无此实验：{experiment_id!r}")
        return current

    def _persist(self, experiment: Experiment) -> None:
        payload = experiment.model_dump(mode="json")
        if self._store is None:
            self._memory[experiment.experiment_id] = payload
            return
        self._store.put(experiment.experiment_id, payload)

    def _require_neutral(self, text: str, where: str) -> None:
        verdict = self._guard.check_output(text)
        if not verdict.passed:
            hits = " / ".join(f"{f.kind}:{f.matched!r}" for f in verdict.findings)
            raise ExperimentError(
                f"{where} 未过 01 §6 中性化校验（{hits}）：文案须中性（铁律 2）"
            )


_DECISION_REASONS: dict[str, str] = {
    "adopt": "决策方指定采用（08 §4：效果判断与决策留痕）",
    "revert": "决策方指定回退（08 §4：效果判断与决策留痕）",
    "inconclusive": "决策方判为不足以定论（08 §4：效果判断保守）",
}


def _system_now() -> datetime:
    """用户本地时区当前时刻（[01 §8](../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    return datetime.now().astimezone()


def _as_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))
