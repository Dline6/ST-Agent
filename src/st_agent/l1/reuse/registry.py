"""输出复用登记与查询（T-L1-001.4；03 §1.2 步骤 6 + §1.3）。

- **登记**：``ok`` / ``empty`` 输出登记为可引用结果（索引形态，A1/A2），
  后续经 ``skill_run_id`` 引用；失败分支不登记
- **复用**：``reuse`` 取回原信封 + 新鲜度判定（``ReuseView``）；判定只给
  结论，是否重算由上层自决（§1.3）
- **新鲜度**：接 L0 §05 口径（``MarketFreshnessOracle``）时以 L0 判定为基，
  并补判「引用快照早于该域当前数据面截止」；未接 L0 时按 ``as_of`` 超龄兜底

布局（只经 ``Store`` 读写，不直连文件系统）：
- 可复用登记索引 → ``execution_log`` 分区 ``skill-output/<skill_run_id>.json``
- 载荷来源 → ``execution_log`` 分区 ``skill-run/<skill_run_id>.json``（不复制）
"""

from __future__ import annotations

from datetime import datetime

from pydantic import ValidationError

from st_agent.contracts.identifiers import SkillRunId
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.time_events import StalenessVerdict
from st_agent.l1.reuse.errors import (
    OutputNotRegisteredError,
    ReuseValidationError,
)
from st_agent.l1.reuse.freshness import (
    FreshnessOracle,
    age_based_verdict,
)
from st_agent.l1.reuse.models import (
    OUTPUT_PREFIX,
    REUSABLE_STATUSES,
    ReusableOutput,
    ReuseView,
)
from st_agent.l1.runner.models import RUN_PREFIX, SkillRun
from st_agent.l1.skills.errors import SkillValidationError
from st_agent.l1.skills.ids import check_skill_id

__all__ = [
    "OUTPUT_PREFIX",
    "REUSABLE_STATUSES",
    "OutputRegistry",
]


def _now() -> datetime:
    """用户本地时区当前时刻（01 §8）。"""
    return datetime.now().astimezone()


def _check_run_id(value: str) -> str:
    try:
        SkillRunId.of(value)
    except ValueError as exc:
        raise ReuseValidationError(f"非法 skill_run_id {value!r}：{exc}") from exc
    return value


def _check_skill(value: str) -> str:
    try:
        check_skill_id(value)
    except SkillValidationError as exc:
        raise ReuseValidationError(f"非法 skill_id {value!r}：{exc}") from exc
    return value


class OutputRegistry:
    """输出复用登记表门面（03 §1.3；持久化只经 ``Store``）。"""

    def __init__(self, store, *, freshness: FreshnessOracle | None = None) -> None:
        self._store = store
        self._freshness = freshness

    # ───────────────────────── 登记（§1.2 步骤 6） ─────────────────────────

    @staticmethod
    def index_path(skill_run_id: str) -> str:
        """登记索引在 ``execution_log`` 分区内的相对路径。"""
        return f"{OUTPUT_PREFIX}{skill_run_id}.json"

    def register(
        self,
        skill_run_id: str,
        skill_id: str,
        envelope: ResultEnvelope,
        *,
        registered_at: datetime | None = None,
    ) -> ReusableOutput:
        """把一次成功输出登记为可引用结果（非可复用分支 → 拒绝登记）。

        非 ``ok``/``empty`` 的信封在此 **fail-fast**——把失败登记成「可复用
        结果」等于让失败沿引用链继续传播（§1.2 禁止用错误数据继续）。
        """
        _check_run_id(skill_run_id)
        _check_skill(skill_id)
        if envelope.status not in REUSABLE_STATUSES:
            raise ReuseValidationError(
                f"status={envelope.status!r} 不得登记为可复用结果；"
                f"仅 {list(REUSABLE_STATUSES)} 可登记（A2）"
            )
        record = ReusableOutput(
            skill_run_id=skill_run_id,
            skill_id=skill_id,
            status=envelope.status,
            as_of=envelope.as_of,
            registered_at=registered_at if registered_at is not None else _now(),
        )
        self._store.put(
            "execution_log",
            self.index_path(skill_run_id),
            record.model_dump_json().encode("utf-8"),
        )
        return record

    # ───────────────────────── 查询（§1.3） ─────────────────────────

    def is_registered(self, skill_run_id: str) -> bool:
        """该 ``skill_run_id`` 是否已登记为可复用结果。"""
        return self.index_path(skill_run_id) in self._store.list_files("execution_log")

    def get(self, skill_run_id: str) -> ReusableOutput:
        """读回登记索引（未登记 → ``OutputNotRegisteredError``）。"""
        _check_run_id(skill_run_id)
        try:
            raw = self._store.get("execution_log", self.index_path(skill_run_id))
        except KeyError as exc:
            raise OutputNotRegisteredError(
                f"{skill_run_id!r} 未登记为可复用结果（03 §1.3）"
            ) from exc
        try:
            return ReusableOutput.model_validate_json(raw)
        except ValidationError as exc:
            raise ReuseValidationError(f"可复用登记记录非法：{exc}") from exc

    def load_envelope(self, skill_run_id: str) -> ResultEnvelope:
        """读回被引用输出的原信封（载荷单一来源于 ``SkillRun`` 记录，A1）。"""
        _check_run_id(skill_run_id)
        try:
            raw = self._store.get(
                "execution_log", f"{RUN_PREFIX}{skill_run_id}.json")
        except KeyError as exc:
            raise OutputNotRegisteredError(
                f"{skill_run_id!r} 无对应 SkillRun 记录，载荷来源缺失"
            ) from exc
        try:
            return SkillRun.model_validate_json(raw).envelope
        except ValidationError as exc:
            raise ReuseValidationError(f"SkillRun 记录非法：{exc}") from exc

    def freshness(
        self, skill_run_id: str, *, domain: str | None = None
    ) -> StalenessVerdict:
        """给出引用某输出的新鲜度判定（§1.3「freshness 查询」接口）。"""
        return self._verdict_for(self.get(skill_run_id), domain)

    def reuse(self, skill_run_id: str, *, domain: str | None = None) -> ReuseView:
        """复用查询：登记索引 + 原信封 + 新鲜度判定（是否重算由上层自决）。"""
        output = self.get(skill_run_id)
        return ReuseView(
            output=output,
            envelope=self.load_envelope(skill_run_id),
            verdict=self._verdict_for(output, domain),
        )

    # ───────────────────────── 内部：判定口径 ─────────────────────────

    def _verdict_for(
        self, output: ReusableOutput, domain: str | None
    ) -> StalenessVerdict:
        """判定口径（A8/A9）：有 L0 可查则以其为基并补判引用快照落伍。"""
        if domain is None or self._freshness is None:
            return age_based_verdict(output.as_of, fallback_at=output.registered_at)
        base = self._freshness.verdict(domain)
        if output.as_of is not None and base.last_updated_at > output.as_of:
            return StalenessVerdict(
                stale=True,
                last_updated_at=base.last_updated_at,
                detail=(
                    f"引用输出基于 {output.as_of.isoformat()}，{domain} 域数据面当前"
                    f"截止 {base.last_updated_at.isoformat()}——引用快照早于当前数据面"
                ),
            )
        return base
