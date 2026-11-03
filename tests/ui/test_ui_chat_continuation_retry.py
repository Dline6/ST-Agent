"""T-AGT-031.3 前端/载荷测试：``llm_failed`` 的「从已有证据继续」入口。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from st_agent.composition.facades.chat import DialogFacade
from st_agent.ui.surfaces.chat import _chat_reply_text

ROOT = Path(__file__).resolve().parents[2]


def _record(**overrides):
    base = dict(
        agent_run_id="agr_old", task="查证 sh.600000", termination="llm_failed",
        reason="端点鉴权失败", plan=(), pending=None, questions=(), directions=(),
        ask_note="", steps=(
            SimpleNamespace(
                tool_name="sk_data_aggregate", skill_id="sk_data_aggregate_v1.0",
                status="ok", skill_run_id="run_1", log_ref="skill-run/run_1.json",
                truncated=False,
            ),
        ),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_llm_failed_payload_is_retry_kind():
    investigation = SimpleNamespace(
        report=SimpleNamespace(agent_run_id="agr_old", record=_record()), stale=False,
    )
    payload = DialogFacade._continuation_payload(investigation)
    assert payload is not None
    assert payload["kind"] == "retry"
    assert payload["agent_run_id"] == "agr_old"
    assert payload["reason"] == "端点鉴权失败"
    assert [item["skill_run_id"] for item in payload["evidence"]] == ["run_1"]


def test_retry_reply_text_is_neutral():
    text = _chat_reply_text({"agent_continuation": {"kind": "retry", "questions": []}})
    assert text == "查证调用失败（可从已有证据继续）"


def test_chat_js_exposes_continue_from_evidence_button():
    source = (ROOT / "src/st_agent/ui/web/js/pages/chat.js").read_text(encoding="utf-8")
    assert "从已有证据继续" in source
    assert "decision: 'retry'" in source
