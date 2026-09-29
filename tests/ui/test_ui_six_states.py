"""六态序列化：各态附**确定的**渲染元数据，且与渲染语义表逐项一致。

这条一致性是「前端不复制语义表」得以成立的前提（`T-UI-001.2` 假设 `A3` 的姐妹口径）：
服务端下发的 `render` 必须就是渲染语义表本身，否则前端会照着错的东西渲染。
"""

from __future__ import annotations

import json

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l3.dispatch.bus import RENDER_SEMANTICS, render_semantics
from st_agent.ui.dev.samples import SAMPLE_STATUSES, sample_envelope
from st_agent.ui.envelope import envelope_payload


def test_sample_statuses_cover_exactly_the_six_states() -> None:
    assert set(SAMPLE_STATUSES) == set(RENDER_SEMANTICS)


@pytest.mark.parametrize("status", SAMPLE_STATUSES)
def test_payload_render_matches_render_semantics(status: str) -> None:
    envelope = sample_envelope(status)
    assert envelope is not None
    assert envelope.status == status

    payload = envelope_payload(envelope)
    expected = render_semantics(envelope)

    assert payload["render"]["presentation"] == expected.presentation
    assert payload["render"]["must_show"] == list(expected.must_show)
    assert payload["render"]["notice"] is None


def test_every_state_names_what_must_be_shown() -> None:
    """非 ok 的五个态各自点名了必展示项（`ok` 为正常态，无必展示项）。"""
    for status in SAMPLE_STATUSES:
        expected = render_semantics(sample_envelope(status))
        if status == "ok":
            assert expected.must_show == ()
        else:
            assert expected.must_show, f"{status} 未点名任何必展示项"


def test_unavailable_is_the_only_state_with_last_updated_at() -> None:
    payloads = {status: envelope_payload(sample_envelope(status)) for status in SAMPLE_STATUSES}
    assert payloads["unavailable"]["last_updated_at"] is not None
    assert payloads["ok"]["last_updated_at"] is None
    assert payloads["failed"]["last_updated_at"] is None


def test_llm_degraded_carries_a_notice_on_unavailable() -> None:
    """显式降级告知（而非静默失败）：仅 `unavailable` + `llm_degraded` 带告知。"""
    envelope = sample_envelope("unavailable")
    degraded = envelope_payload(envelope, llm_degraded=True)
    assert degraded["render"]["notice"] == render_semantics(envelope, llm_degraded=True).notice
    assert degraded["render"]["notice"]

    assert envelope_payload(sample_envelope("ok"), llm_degraded=True)["render"]["notice"] is None


@pytest.mark.parametrize("status", SAMPLE_STATUSES)
def test_payload_is_json_serialisable(status: str) -> None:
    json.dumps(envelope_payload(sample_envelope(status)), ensure_ascii=False)


def test_payload_shape_matches_the_envelope_contract() -> None:
    payload = envelope_payload(ResultEnvelope.ok({"a": 1}))
    assert set(payload) == {
        "status",
        "data",
        "reason",
        "evidence_refs",
        "as_of",
        "last_updated_at",
        "log_ref",
        "render",
    }
