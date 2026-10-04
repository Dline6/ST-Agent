"""`T-INT-003` 测试：L4 三个鸭子端口的真实现（[`l4/ports.py`](../../src/st_agent/l4/ports.py)）。

三条边界逐条对应一组用例：**编排件不触网（出网只在本模块）** / **失败显式化**
（不可用即抛或如实标未判定，不静默、不编造）/ **产出文案仍交下游 §6 门**。

LLM 与取数面全为替身——真实端点由 `tests/live/test_llm_deliberation_live.py` 兜（GWT-9）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l4.deliberation import SynthesizedView
from st_agent.l4.divergence import EvidenceReview
from st_agent.l4.errors import PortUnavailableError
from st_agent.l4.ports import (
    LlmEvidenceReviewer,
    LlmOpinionSynthesizer,
    MarketDimensionCatalog,
)

ENDPOINT = "cloud-main"


# ───────────────────────────── 替身 ─────────────────────────────

class _Event:
    def __init__(self, kind: str, text: str = "", envelope: Any = None) -> None:
        self.kind = kind
        self.text = text
        self.error_envelope = envelope


class FakeLlm:
    """按预置应答产事件的假 `LlmClient`（记录每次调用的端点与归属标注）。"""

    def __init__(self, text: str = "", *, error: ResultEnvelope | None = None) -> None:
        self._text = text
        self._error = error
        self.calls: list[tuple[str, str, str]] = []

    def invoke(self, endpoint_id: str, prompt: str, *, initiator: str = "", purpose: str = "", **_kw: Any):
        self.calls.append((endpoint_id, initiator, purpose))
        if self._error is not None:
            yield _Event("error", envelope=self._error)
            return
        if self._text:
            yield _Event("chunk", text=self._text)
        yield _Event("done")


class FakeMarket:
    """假取数源（`MarketQuerySource` 鸭子型）。"""

    def __init__(self, names: tuple[str, ...] = (), *, envelope: ResultEnvelope | None = None) -> None:
        self._names = names
        self._envelope = envelope
        self.sqls: list[str] = []

    def query(self, sql: str, params: tuple = ()) -> ResultEnvelope:
        self.sqls.append(sql)
        if self._envelope is not None:
            return self._envelope
        return ResultEnvelope.ok(
            {"columns": ["name"], "rows": [{"name": n} for n in self._names]}
        )


class FakeSkills:
    """假 `SkillRegistry`：只回答 `get_latest(base)`（未登记的 base 回 `None`）。"""

    def __init__(self, known: tuple[str, ...] = ()) -> None:
        self._known = set(known)

    def get_latest(self, base: str) -> Any:
        return SimpleNamespace(skill_id=f"{base}_v1.0") if base in self._known else None


def _lens(name: str = "风险视角") -> SimpleNamespace:
    return SimpleNamespace(
        name=name, description="识别风险",
        judging_criteria=SimpleNamespace(natural="按风险信号为准"),
    )


# ───────────────────── 方向合成器（编排件不触网，出网只在此） ─────────────────────

def test_synthesizer_returns_a_structured_view() -> None:
    llm = FakeLlm('{"stance": "negative", "key_reasons": ["数据面偏弱"]}')
    synthesizer = LlmOpinionSynthesizer(llm, ENDPOINT)
    view = synthesizer.synthesize(_lens(), [{"a": 1}], (), 1.0)
    assert isinstance(view, SynthesizedView)
    assert view.stance == "negative" and view.key_reasons == ("数据面偏弱",)
    assert llm.calls and llm.calls[0][0] == ENDPOINT
    assert llm.calls[0][1] and llm.calls[0][2], "用量归属必填（01 §5）"


def test_synthesizer_tolerates_a_fenced_json_block() -> None:
    llm = FakeLlm('```json\n{"stance": "neutral", "key_reasons": ["数据中性"]}\n```')
    view = LlmOpinionSynthesizer(llm, ENDPOINT).synthesize(_lens(), [], (), 0.0)
    assert view.stance == "neutral"


@pytest.mark.parametrize(
    "payload",
    [
        '{"stance": "insufficient-data", "key_reasons": ["x"]}',  # 越权：那是编排的判定
        '{"stance": "bullish", "key_reasons": ["x"]}',            # 未知方向
        '{"stance": "positive", "key_reasons": []}',              # 理由为空
        '{"stance": "positive"}',                                 # 缺理由字段
        "不是 JSON",
        "[]",
    ],
)
def test_synthesizer_rejects_output_it_cannot_trust(payload: str) -> None:
    """端点产出不合口径即抛——由编排的失败隔离转 `insufficient-data`，**不编造**观点。"""
    with pytest.raises(PortUnavailableError):
        LlmOpinionSynthesizer(FakeLlm(payload), ENDPOINT).synthesize(_lens(), [], (), 0.0)


def test_synthesizer_surfaces_endpoint_errors() -> None:
    error = ResultEnvelope.unavailable(
        "端点未配置", last_updated_at=datetime(2026, 10, 4, tzinfo=timezone.utc),
    )
    with pytest.raises(PortUnavailableError) as excinfo:
        LlmOpinionSynthesizer(FakeLlm(error=error), ENDPOINT).synthesize(_lens(), [], (), 0.0)
    assert "端点未配置" in str(excinfo.value)


def test_synthesizer_rejects_an_empty_response() -> None:
    with pytest.raises(PortUnavailableError):
        LlmOpinionSynthesizer(FakeLlm(""), ENDPOINT).synthesize(_lens(), [], (), 0.0)


# ───────────────────── 盲点维度目录（读本地库，不出网） ─────────────────────

def test_catalog_reports_only_dimensions_present_in_the_local_cache() -> None:
    """维度全集来自官方 Pack 旁的声明，本件读库**判存在性**——本地没有的数据面不报。"""
    market = FakeMarket(("k_line_daily", "security"))
    catalog = MarketDimensionCatalog(market, skills=FakeSkills((
        "sk_st_list_sync", "sk_fundamental_screening",
    )))
    dims = {d.key: d for d in catalog.dimensions_for("sh.600000")}
    # kline / security 的承载表在缓存里 → 报；financial / company_report 等不在 → 不报
    assert set(dims) == {"kline", "security"}
    assert dims["kline"].label == "行情"
    assert dims["security"].skill_ids == ("sk_fundamental_screening_v1.0",)
    assert market.sqls and "sqlite_master" in market.sqls[0]


def test_catalog_leaves_unresolvable_producers_empty() -> None:
    """声明的产出 Skill 解析不出 → `skill_ids` 为空（对照件据此记「覆盖不可判」，不臆造 id）。"""
    market = FakeMarket(("k_line_daily",))
    catalog = MarketDimensionCatalog(market, skills=FakeSkills(()))  # 注册表里一个都没有
    (dim,) = catalog.dimensions_for("x")
    assert dim.key == "kline" and dim.skill_ids == ()


def test_catalog_without_a_skill_registry_reports_no_producers() -> None:
    catalog = MarketDimensionCatalog(FakeMarket(("announcement",)), skills=None)
    (dim,) = catalog.dimensions_for("x")
    assert dim.key == "announcement" and dim.skill_ids == ()


def test_catalog_fails_loudly_without_a_market_source() -> None:
    with pytest.raises(PortUnavailableError):
        MarketDimensionCatalog(None).dimensions_for("x")


def test_catalog_fails_loudly_on_a_bad_query_result() -> None:
    not_ok = FakeMarket(envelope=ResultEnvelope.empty("库未建"))
    with pytest.raises(PortUnavailableError):
        MarketDimensionCatalog(not_ok, skills=FakeSkills()).dimensions_for("x")

    no_rows = FakeMarket(envelope=ResultEnvelope.ok({"columns": []}))
    with pytest.raises(PortUnavailableError):
        MarketDimensionCatalog(no_rows, skills=FakeSkills()).dimensions_for("x")


# ───────────────────── 证据重研判（失败不抛，但必须可见） ─────────────────────

def test_reviewer_consumes_a_well_formed_judgement() -> None:
    llm = FakeLlm(
        '{"validity": "valid", "freshness": "stale", "weight": 0.25, "note": "证据成立但偏旧"}'
    )
    review = LlmEvidenceReviewer(llm, ENDPOINT).review("run_" + "0" * 20, lens_ids=("a", "b"), as_of=None)
    assert isinstance(review, EvidenceReview)
    assert (review.validity, review.freshness, review.weight) == ("valid", "stale", 0.25)
    assert review.ref == "run_" + "0" * 20, "被评证据的 ref 恒钉回本件"


@pytest.mark.parametrize(
    ("payload", "expected_weight"),
    [
        ('{"validity": "maybe", "freshness": "warm", "weight": 7, "note": "x"}', None),
        ('{"validity": "invalid", "freshness": "fresh", "weight": null, "note": "x"}', None),
        ('{"validity": "invalid", "freshness": "fresh", "weight": true, "note": "x"}', None),
    ],
)
def test_reviewer_leaves_out_of_range_values_undecided(payload: str, expected_weight: Any) -> None:
    """取值越界一律留 `unknown` / `None`——**不臆断**，也不因越界而毁掉整张分歧图。"""
    review = LlmEvidenceReviewer(FakeLlm(payload), ENDPOINT).review("ref_x", lens_ids=(), as_of=None)
    assert review.validity in ("valid", "invalid", "unknown")
    assert review.freshness in ("fresh", "stale", "unknown")
    assert review.weight == expected_weight
    if '"validity": "maybe"' in payload:
        assert review.validity == "unknown" and review.freshness == "unknown"


def test_reviewer_degrades_visibly_when_the_endpoint_is_down() -> None:
    """端点不可用**不抛**（对照件逐条同步调用且无异常兜底），但降级必须是**可见**的。"""
    error = ResultEnvelope.unavailable(
        "端点未配置", last_updated_at=datetime(2026, 10, 4, tzinfo=timezone.utc),
    )
    review = LlmEvidenceReviewer(FakeLlm(error=error), ENDPOINT).review(
        "ref_x", lens_ids=("a",), as_of=None,
    )
    assert (review.validity, review.freshness, review.weight) == ("unknown", "unknown", None)
    assert "未判定" in review.note and "端点未配置" in review.note
