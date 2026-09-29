"""dev 面的六态示例信封。

用途只有一个：在浏览器里把**六个态各自的渲染**逐条走通——M1 骨架叶不接真实数据
（任务 `T-UI-001.2` 假设 `A5`）。信封一律用 [01 §5] 的**合法工厂**构造，故它们同时
也是契约形状的活样本；本模块不进发布构建。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from st_agent.contracts.result_envelope import EvidenceRef, ResultEnvelope

__all__ = ["SAMPLE_STATUSES", "sample_envelope"]

_NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone(timedelta(hours=8)))

SAMPLE_STATUSES: tuple[str, ...] = (
    "ok",
    "empty",
    "unavailable",
    "dependency_failed",
    "failed",
    "validation_failed",
)
"""六个状态（与 [01 §5] 的枚举同序）。"""


def _samples() -> dict[str, ResultEnvelope]:
    evidence = (EvidenceRef(kind="dataset_snapshot_id", ref="ds_sample0000000000000"),)
    return {
        "ok": ResultEnvelope.ok(
            {"summary": "示例载荷（骨架叶不接真实数据）"}, as_of=_NOW, evidence_refs=evidence
        ),
        "empty": ResultEnvelope.empty(
            "今日无满足条件的记录", as_of=_NOW, evidence_refs=evidence
        ),
        "unavailable": ResultEnvelope.unavailable(
            "数据源暂不可用", last_updated_at=_NOW - timedelta(hours=6), as_of=_NOW
        ),
        "dependency_failed": ResultEnvelope.dependency_failed(
            "上游依赖失败", log_ref="trace/sample-dependency"
        ),
        "failed": ResultEnvelope.failed("执行失败", log_ref="trace/sample-failure"),
        "validation_failed": ResultEnvelope.validation_failed("参数校验失败：取值超出声明范围"),
    }


def sample_envelope(status: str) -> ResultEnvelope | None:
    """按状态名取示例信封；未知状态名返回 ``None``（调用方回 400 / 404）。"""
    return _samples().get(status)
