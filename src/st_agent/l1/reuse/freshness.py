"""新鲜度判定（03 §1.3；01 §8 时间口径；数据库设计 §05 陈旧判据）。

口径**唯一**：有 L0 同步状态可查时，一律经 ``MarketFreshnessOracle`` 取
L0 的判定（``BaoStockSync.freshness_verdict`` → ``StalenessVerdict``，
T-L0-005 交付；逐表水位与陈旧判据见 §05），L1 **不另建第二套口径**；
无 L0 可查（无库 / 未接线）时退化为按 ``as_of`` 超龄判，并在
``detail`` 里写明这是兜底口径——不静默冒充 L0 判定。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.time_events import StalenessVerdict
from st_agent.l1.reuse.errors import ReuseValidationError

__all__ = [
    "DEFAULT_MAX_AGE",
    "FreshnessOracle",
    "MarketFreshnessOracle",
    "age_based_verdict",
    "unavailable_envelope",
]

DEFAULT_MAX_AGE = timedelta(hours=24)
"""兜底超龄阈值（A9）：对齐 §05「日线 T 日数据在 T 日 BaoStock 更新后可得」，
以「一日」为通用预期新鲜度；逐域精细判据仍以 L0 判定为准。
"""


@runtime_checkable
class FreshnessOracle(Protocol):
    """新鲜度判定来源（按数据域给出 §05 口径的判定）。"""

    def verdict(self, domain: str) -> StalenessVerdict:
        """给出某数据域当前的新鲜度判定。"""
        ...


class MarketFreshnessOracle:
    """L0 同步状态口径的适配器（鸭子类型，L1 不 import L0）。

    被适配对象须提供 ``freshness_verdict(domain) -> StalenessVerdict``
    （``st_agent.l0.market.BaoStockSync`` 即此形态）。不合口径的对象在
    构造期即拒绝——否则「复用 L0 口径」会退化成各自发明判据。
    """

    def __init__(self, sync_source: object) -> None:
        if not callable(getattr(sync_source, "freshness_verdict", None)):
            raise ReuseValidationError(
                "新鲜度来源须提供 freshness_verdict(domain)（L0 §05 口径，"
                f"如 BaoStockSync）；收到 {type(sync_source).__name__}"
            )
        self._source = sync_source

    def verdict(self, domain: str) -> StalenessVerdict:
        """委托 L0 判定（未知域等非法入参由 L0 侧抛错，原样透出）。"""
        return self._source.freshness_verdict(domain)


def _require_tz(value: datetime, field: str) -> datetime:
    if value.tzinfo is None:
        raise ReuseValidationError(f"{field} 必须带时区语义（01 §8）")
    return value


def _age_text(max_age: timedelta) -> str:
    seconds = int(max_age.total_seconds())
    if seconds and seconds % 3600 == 0:
        return f"{seconds // 3600} 小时"
    if seconds and seconds % 60 == 0:
        return f"{seconds // 60} 分钟"
    return f"{seconds} 秒"


def age_based_verdict(
    as_of: datetime | None,
    *,
    fallback_at: datetime,
    now: datetime | None = None,
    max_age: timedelta = DEFAULT_MAX_AGE,
) -> StalenessVerdict:
    """按 ``as_of`` 超龄判新鲜度（无 L0 可查时的兜底口径，A9）。

    ``as_of`` 缺失（执行器未给锚点）时**保守判陈旧**并在 ``detail`` 说明
    原因——「禁止用旧数据冒充新数据」优先于「少报陈旧」（§8）。
    """
    stamp = _require_tz(fallback_at, "fallback_at")
    if now is None:
        now = datetime.now().astimezone()
    _require_tz(now, "now")
    if as_of is None:
        return StalenessVerdict(
            stale=True,
            last_updated_at=stamp,
            detail=f"引用输出未带 as_of 锚点（01 §8），无法判定新鲜度；"
                   f"兜底口径按陈旧处理，登记于 {stamp.isoformat()}",
        )
    _require_tz(as_of, "as_of")
    if (now - as_of) > max_age:
        return StalenessVerdict(
            stale=True,
            last_updated_at=as_of,
            detail=f"引用输出基于 {as_of.isoformat()}，已超出预期新鲜度 "
                   f"{_age_text(max_age)}（§05 兜底口径，未接 L0 同步状态）",
        )
    return StalenessVerdict(
        stale=False,
        last_updated_at=as_of,
        detail=f"引用输出基于 {as_of.isoformat()}，在预期新鲜度 "
               f"{_age_text(max_age)} 内（§05 兜底口径，未接 L0 同步状态）",
    )


def unavailable_envelope(verdict: StalenessVerdict, reason: str) -> ResultEnvelope:
    """从新鲜度判定构造 ``unavailable`` 信封（GWT-8）。

    「最后更新时间 T」**只**来自 ``verdict.last_updated_at``（其源头是 L0
    §05 口径）——执行器不得自行编造时间，这是「数据不可用不编造结果」的
    落点。
    """
    return ResultEnvelope.unavailable(reason, last_updated_at=verdict.last_updated_at)
