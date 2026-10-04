"""`T-INT-003` 测试：`analyze` 去向的 L4 装配件（[`l4/analyze.py`](../../src/st_agent/l4/analyze.py)）。

本件是编排 → 对照 → 视图三段的**唯一衔接点**，职责只有：解析 `topic` / `mode`、按序调用
三段、把失败信封**原样透出**、把三条产物并列承载。故用例集中在**衔接语义**上——
各段自身的行为由 `T-L4-002` / `T-L4-003` / `T-L4-004.1` 的套件覆盖。
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from st_agent.contracts import LensOpinion, ResultEnvelope, Trace
from st_agent.contracts.identifiers import TraceId
from st_agent.l4.analyze import DEFAULT_MODE, MODES, AnalyzeOutcome, AnalyzeService
from st_agent.l4.crosscheck import CrossExaminer
from st_agent.l4.deliberation import DeliberationResult
from st_agent.l4.divergence_view import DivergenceViewer


def _opinion(lens_id: str, stance: str, refs: tuple[str, ...] = ()) -> LensOpinion:
    return LensOpinion(
        lens_id=lens_id, stance=stance, key_reasons=("中性理由",),
        evidence_refs=refs, confidence="medium", skills_triggered=(),
        trace_id=TraceId.generate().value,
    )


def _result(
    topic: str = "sh.600000", *, opinions: tuple[LensOpinion, ...] = (), mode: str = "deep",
) -> DeliberationResult:
    return DeliberationResult(
        topic=topic, mode=mode,
        envelope=ResultEnvelope.ok([o.lens_id for o in opinions]),
        opinions=opinions,
        traces=tuple(Trace(trace_id=TraceId.generate()) for _ in opinions),
        lens_ids=tuple(o.lens_id for o in opinions),
    )


class _StubDeliberation:
    """按预置结果应答的编排替身（记录被调用的 topic / mode）。"""

    def __init__(self, result: DeliberationResult) -> None:
        self._result = result
        self.calls: list[tuple[str, str]] = []

    def deliberate(self, topic: str, *, mode: str = "deep", **_kw: Any) -> DeliberationResult:
        self.calls.append((topic, mode))
        return self._result


def _service(result: DeliberationResult, *, viewer: Any = None, examiner: Any = None):
    examiner = examiner if examiner is not None else CrossExaminer()
    viewer = viewer if viewer is not None else DivergenceViewer()
    stub = _StubDeliberation(result)
    return AnalyzeService(deliberation=stub, examiner=examiner, viewer=viewer), stub


# ───────────────────────────── topic / mode 解析 ─────────────────────────────

def test_unknown_mode_is_rejected_before_running_anything() -> None:
    service, stub = _service(_result())
    outcome = service.analyze(SimpleNamespace(values={"topic": "x", "mode": "wide"}))
    assert outcome.envelope.status == "validation_failed"
    assert stub.calls == [], "模式非法时不得启动编排"


@pytest.mark.parametrize("mode", MODES)
def test_mode_passes_through_to_the_orchestration(mode: str) -> None:
    service, stub = _service(_result(mode=mode))
    service.analyze(SimpleNamespace(values={"topic": "x", "mode": mode}))
    assert stub.calls == [("x", mode)]


def test_missing_mode_falls_back_to_the_declared_default() -> None:
    """澄清问项被跳过时确认卡不给 `mode`——编排取 06 §2.1 的默认模式（深度的完整面）。"""
    service, stub = _service(_result())
    outcome = service.analyze(SimpleNamespace(values={"topic": "x"}))
    assert stub.calls == [("x", DEFAULT_MODE)]
    assert outcome.mode == DEFAULT_MODE


def test_values_override_the_confirmation_card() -> None:
    """派发时的 `values` 覆盖确认卡取值（总线 `dispatch(values=…)` 的同名口径）。"""
    service, stub = _service(_result(mode="quick"))
    outcome = service.analyze(
        SimpleNamespace(values={"topic": "卡上主题", "mode": "deep"}),
        values={"topic": "调用方主题", "mode": "quick"},
    )
    assert stub.calls == [("调用方主题", "quick")]
    assert outcome.topic == "调用方主题"


# ───────────────────────────── 失败信封原样透出 ─────────────────────────────

def test_orchestration_failure_envelope_passes_through_unchanged() -> None:
    """编排的失败信封原样承载——不重包、不改 status、不吞 reason，且不产视图。"""
    failed = DeliberationResult(
        topic="x", mode="deep",
        envelope=ResultEnvelope.unavailable(
            "未注入 Skill 执行面", last_updated_at=datetime(2026, 10, 4, tzinfo=timezone.utc),
        ),
    )
    service, _ = _service(failed)
    outcome = service.analyze(SimpleNamespace(values={"topic": "x"}))
    assert outcome.envelope is failed.envelope
    assert outcome.view is None and outcome.map is None
    assert outcome.result is failed


def test_malformed_orchestration_payload_is_dependency_failed() -> None:
    class _Broken:
        def deliberate(self, *_a: Any, **_kw: Any) -> Any:
            return SimpleNamespace(topic="x")

    service = AnalyzeService(
        deliberation=_Broken(), examiner=CrossExaminer(), viewer=DivergenceViewer(),
    )
    outcome = service.analyze(SimpleNamespace(values={"topic": "x"}))
    assert outcome.envelope.status == "dependency_failed"


def test_injected_port_defect_does_not_escape_as_an_exception() -> None:
    """对照件消费的注入端口若抛穿，转 `dependency_failed`——派发不得以异常收场（05 §4）。"""

    class _ExplodingExaminer:
        def cross_examine(self, _result: Any) -> Any:
            raise RuntimeError("维度目录端口炸了")

    service, _ = _service(_result(opinions=(_opinion("lens_a", "neutral"),)),
                          examiner=_ExplodingExaminer())
    outcome = service.analyze(SimpleNamespace(values={"topic": "x"}))
    assert outcome.envelope.status == "dependency_failed"
    assert "端口" in outcome.envelope.reason
    assert outcome.view is None


# ───────────────────────────── 成功路径的三产物 ─────────────────────────────

def test_full_chain_carries_view_map_traces_and_a_factual_payload() -> None:
    opinions = (
        _opinion("lens_a", "positive", ("run_00000000000000000001",)),
        _opinion("lens_b", "negative", ("run_00000000000000000002",)),
    )
    result = _result(opinions=opinions)
    service, _ = _service(result)
    outcome = service.analyze(SimpleNamespace(values={"topic": "sh.600000"}))

    assert outcome.envelope.status == "ok"
    assert outcome.map is not None and outcome.view is not None
    assert len(outcome.view.rows) == 2
    # 信封载荷只有描述性事实（无合并结论；06 架构级红线、铁律 4）
    payload = outcome.envelope.data
    assert set(payload) == {"topic", "mode", "lens_ids", "disagreements", "blind_spots"}
    assert payload["lens_ids"] == ["lens_a", "lens_b"]


def test_without_a_viewer_the_run_still_reports_the_map() -> None:
    """视图件缺位不阻断对照产物——描述件届时走 `unavailable`，而非返回残缺卡。"""
    service = AnalyzeService(
        deliberation=_StubDeliberation(_result(opinions=(_opinion("lens_a", "neutral"),))),
        examiner=CrossExaminer(), viewer=None,
    )
    outcome = service.analyze(SimpleNamespace(values={"topic": "x"}))
    assert outcome.envelope.status == "ok"
    assert outcome.map is not None and outcome.view is None


# ───────────────────────────── 追问接口的取数面 ─────────────────────────────

def test_trace_for_addresses_a_lens_chain_by_id() -> None:
    result = _result(opinions=(_opinion("lens_a", "neutral"),))
    outcome = AnalyzeOutcome(
        envelope=ResultEnvelope.ok({}), traces=result.traces,
    )
    assert outcome.trace_for(result.traces[0].trace_id.value) is result.traces[0]
    assert outcome.trace_for("tr_" + "0" * 20) is None
