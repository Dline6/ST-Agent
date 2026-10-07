"""反思中心的表现层入口（[`T-UI-004.2`]）。

本组锁四件事：

1. **面未注入 ⇒ fail-closed**——各端点回 `unavailable` + 点名（不 500、不伪造报告）；
2. **信封形态**——读端点出的是 01 §12 的描述（`report_card` / `trace_timeline` /
   `proposal_card` / `table`），且**两处空态分开判定**（无触达 / 数据不足由 L6 的正文原话承载）；
3. **写面只转发**——反馈经**交互层**的采集面（`feedback_id` 由它铸），提案处置经变更流；
   面回的信封**原样出站**（六态不被吞）；
4. **出站两闸**——必填槽与渲染前中性化门在同一处（`_gated_description`）。

替身只回**已定形态**的数据：本组钉的是 ui 侧的信封与描述形态，真链路的装配由
`tests/integration/` 的 M4 夹具覆盖。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.ui.app import build_ui
from st_agent.ui.server import serve

CST = timezone(__import__("datetime").timedelta(hours=8))

WEEK = "2026-W41"

REPORT = {
    "week": WEEK,
    "period_start": "2026-10-05",
    "period_end": "2026-10-11",
    "generated_at": "2026-10-11T20:00:00+08:00",
    "empty": False,
    "insufficient": False,
    "body": "本周报告正文",
    "sections": [
        {
            "name": "week_digest",
            "label": "本周为你做了什么",
            "lines": [{"text": "本周触达 3 次，采纳 1 次", "generated": True}],
            "empty_note": "",
        },
        {
            "name": "errors_understood",
            "label": "错误与理解",
            "lines": [{"text": "我觉得这条不对，因为估值偏高", "generated": False}],
            "empty_note": "",
        },
    ],
    "proposals": [
        {
            "config_id": "fatigue.ignore-threshold",
            "current": 4,
            "suggested": 3,
            "reason": "该类推送被连续忽略达阈值",
            "trace_ref": "",
        }
    ],
    "trace": {
        "delivery_ids": ["dlv_" + "1" * 20],
        "signal_ids": [],
        "feedback_ids": ["fb_" + "a" * 20],
        "memory_node_ids": [],
        "evidence_refs": [{"kind": "announcement_id", "ref": "ann_" + "2" * 20}],
        "trace_ids": [],
    },
}

PENDING = [
    {
        "pending_id": "pnd_1",
        "status": "pending",
        "deferrals": 0,
        "proposal": {
            "config_id": "fatigue.ignore-threshold",
            "current": 4,
            "suggested": 3,
            "reason": "该类推送被连续忽略达阈值",
            "trace_ref": "",
            "source": "weekly-report",
        },
    },
    {
        "pending_id": "pnd_2",
        "status": "accepted",          # 已处置：不进待办（留痕仍在 L6 侧）
        "deferrals": 0,
        "proposal": {"config_id": "x", "current": 1, "suggested": 2, "reason": "r"},
    },
]

SKILL_PROPOSAL = [
    {
        "proposal_id": "prp_1",
        "key": "repeat-question",
        "count": 6,
        "reason": "同类问题重复出现超过阈值",
        "sample": "我总想问这个",
        "released_week": WEEK,
        "draft": {"name": "专门处理这类问题", "description": "回答某类问题", "nodes": [1, 2, 3]},
    }
]


class FakeReflection:
    """表现层适配面替身（只回已定形态的数据，并记下收到的调用）。"""

    def __init__(
        self,
        *,
        weeks=(WEEK,),
        report=REPORT,
        feedback=None,
        pending=PENDING,
        proposals=SKILL_PROPOSAL,
        decide=None,
        experiments=(),
        trainings=(),
    ) -> None:
        self._weeks = tuple(weeks)
        self._report = report
        self._feedback = (
            feedback
            if feedback is not None
            else ResultEnvelope.ok({"feedback_id": "fb_" + "a" * 20})
        )
        self._pending = list(pending)
        self._proposals = list(proposals)
        self._decide = decide if decide is not None else {"action": "accept", "applied": True}
        self._experiments = list(experiments)
        self._trainings = list(trainings)
        self.calls: list[tuple] = []

    def report_weeks(self):
        return self._weeks

    def report(self, week):
        return self._report

    def record_feedback(self, *, target, action, reason=None, context=None):
        self.calls.append(("feedback", target, action, reason))
        return self._feedback

    def pending(self):
        return list(self._pending)

    def proposals(self):
        return list(self._proposals)

    def decide(self, *, action, proposal=None, pending_id=""):
        self.calls.append(("decide", action, pending_id, proposal))
        if isinstance(self._decide, Exception):      # 替身按需抛出「输入类失败」
            raise self._decide
        return self._decide

    def experiments(self):
        return list(self._experiments)

    def trainings(self):
        return list(self._trainings)


@pytest.fixture
def reflection_server():
    """起一个注入了替身适配面的真服务。"""

    def _start(facade):
        return serve(dev=False, reflection=facade)

    return _start


# ── 面未注入：fail-closed ──────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "path",
    [
        "/api/reflection/reports",
        "/api/reflection/reports/trace",
        "/api/reflection/feedback-capture",
        "/api/reflection/proposals",
        "/api/reflection/experiments",
        "/api/reflection/training",
    ],
)
def test_read_endpoints_are_unavailable_without_the_face(ui_server, http_get, auth, path):
    payload = http_get(ui_server, path, headers=auth(ui_server)).json()
    assert payload["status"] == "unavailable"
    assert "未接入" in payload["reason"]


@pytest.mark.parametrize(
    "path,body",
    [
        ("/api/reflection/feedback", {"target": {"kind": "delivery", "ref": "dlv_x"}, "action": "adopted"}),
        ("/api/reflection/proposals/decide", {"action": "accept", "pending_id": "pnd_1"}),
    ],
)
def test_write_endpoints_are_unavailable_without_the_face(ui_server, http_post, auth, path, body):
    payload = http_post(ui_server, path, body, headers=auth(ui_server)).json()
    assert payload["status"] == "unavailable"
    assert "未接入" in payload["reason"]


# ── 周报：四段式与两处空态 ────────────────────────────────────────────────────
def test_report_renders_a_four_section_card(reflection_server, http_get, auth):
    with reflection_server(FakeReflection()) as running:
        payload = http_get(running, "/api/reflection/reports", headers=auth(running)).json()
    assert payload["status"] == "ok"
    assert payload["data"]["component_type"] == "report_card"
    sections = payload["data"]["slots"]["sections"]
    assert [section["title"] for section in sections] == ["本周为你做了什么", "错误与理解"]
    assert sections[1]["lines"] == ["我觉得这条不对，因为估值偏高"]   # 用户原话按 data 原样呈现


@pytest.mark.parametrize(
    "flags, note",
    [
        ({"empty": True, "insufficient": False}, "本周无触达，无反馈可分析"),
        ({"empty": False, "insufficient": True}, "数据还不够，下周见"),
    ],
)
def test_two_empty_states_are_kept_apart(reflection_server, http_get, auth, flags, note):
    """两态由 L6 的正文原话承载——表现层不按「反馈数为 0」自行反推（[08 §4]）。"""
    report = {**REPORT, **flags, "body": note}
    with reflection_server(FakeReflection(report=report)) as running:
        payload = http_get(running, "/api/reflection/reports", headers=auth(running)).json()
    assert payload["status"] == "empty"
    assert payload["reason"] == note


def test_no_stored_week_is_empty_not_a_blank_report(reflection_server, http_get, auth):
    with reflection_server(FakeReflection(weeks=(), report=None)) as running:
        payload = http_get(running, "/api/reflection/reports", headers=auth(running)).json()
    assert payload["status"] == "empty"
    assert "尚无任何一期" in payload["reason"]


def test_trace_lists_one_step_per_anchor_class(reflection_server, http_get, auth):
    with reflection_server(FakeReflection()) as running:
        payload = http_get(
            running, "/api/reflection/reports/trace", headers=auth(running)
        ).json()
    assert payload["status"] == "ok"
    assert payload["data"]["component_type"] == "trace_timeline"
    steps = payload["data"]["slots"]["steps"]
    assert [step["step_type"] for step in steps] == [
        "投递留痕", "信号留痕", "反馈留痕", "记忆读取", "证据引用", "推理链引用",
    ]
    by_type = {step["step_type"]: step for step in steps}
    assert by_type["投递留痕"]["degraded"] is False
    assert by_type["信号留痕"]["degraded"] is True                  # 空类如实标记，不硬凑
    assert by_type["证据引用"]["ref"].startswith("announcement_id:")   # {kind, ref} 压成文本


# ── 反馈采集：只转发 ──────────────────────────────────────────────────────────
def test_feedback_is_forwarded_and_the_envelope_passes_through(reflection_server, http_get, http_post, auth):
    facade = FakeReflection()
    with reflection_server(facade) as running:
        capture = http_get(
            running, "/api/reflection/feedback-capture", headers=auth(running)
        ).json()
        assert capture["data"]["component_type"] == "feedback_capture"
        slots = capture["data"]["slots"]
        assert slots["actions"] == ["adopted", "ignored", "rejected", "queried", "liked"]
        assert slots["reason_required"] == ["rejected"]
        body = {"target": slots["target"], "action": "rejected", "reason": "估值偏高"}
        payload = http_post(running, "/api/reflection/feedback", body, headers=auth(running)).json()
    assert payload["status"] == "ok"
    assert facade.calls == [("feedback", slots["target"], "rejected", "估值偏高")]


def test_a_rejecting_collector_envelope_is_not_swallowed(reflection_server, http_get, http_post, auth):
    """采集面自己回 `validation_failed`（如否定类缺原因）时**原样出站**——六态不被吞。"""
    facade = FakeReflection(feedback=ResultEnvelope.validation_failed("否定类反馈必须说明原因"))
    with reflection_server(facade) as running:
        payload = http_post(
            running,
            "/api/reflection/feedback",
            {"target": {"kind": "delivery", "ref": "dlv_x"}, "action": "rejected"},
            headers=auth(running),
        ).json()
    assert payload["status"] == "validation_failed"
    assert "必须说明原因" in payload["reason"]


def test_missing_body_keys_are_validation_failed(reflection_server, http_post, auth):
    with reflection_server(FakeReflection()) as running:
        payload = http_post(
            running, "/api/reflection/feedback", {"action": "adopted"}, headers=auth(running)
        ).json()
    assert payload["status"] == "validation_failed"
    assert "target" in payload["reason"]


# ── 提案卡与处置 ──────────────────────────────────────────────────────────────
def test_proposal_card_carries_queue_candidates_and_skill_proposals(reflection_server, http_get, auth):
    with reflection_server(FakeReflection()) as running:
        payload = http_get(running, "/api/reflection/proposals", headers=auth(running)).json()
    assert payload["status"] == "ok"
    assert payload["data"]["component_type"] == "proposal_card"
    items = payload["data"]["slots"]["proposals"]
    kinds = [item["kind"] for item in items]
    assert kinds == ["change", "change", "skill"]      # 待批准 1 条 + 周报候选 1 条 + 主动提案 1 条
    queued, candidate, skill = items
    assert queued["pending_id"] == "pnd_1"
    assert queued["actions"] == ["accept", "reject", "defer"]
    assert candidate["pending_id"] == "" and candidate["actions"] == ["accept"]
    assert skill["actions"] == []                      # 主动提案是信息面（接受 → Studio 草稿，未接线）
    assert skill["draft"]["nodes"] == 3


def test_decide_forwards_action_and_pending_id(reflection_server, http_post, auth):
    facade = FakeReflection(decide={"action": "reject", "applied": False, "note": "留痕"})
    with reflection_server(facade) as running:
        payload = http_post(
            running,
            "/api/reflection/proposals/decide",
            {"action": "reject", "pending_id": "pnd_1"},
            headers=auth(running),
        ).json()
    assert payload["status"] == "ok"
    assert payload["data"]["note"] == "留痕"
    assert facade.calls == [("decide", "reject", "pnd_1", None)]


def test_input_shaped_failure_from_the_face_maps_to_validation_failed(reflection_server, http_post, auth):
    """适配面的**输入类**失败（`ValueError`）⇒ `validation_failed` + 原话原因（[01 §5]）。

    缺失项的判定归组合根的适配面（`ReflectionFacade.decide`），表现层只按异常类别分流。
    """
    facade = FakeReflection(decide=ValueError("需给出待批准项 id 或提案内容"))
    with reflection_server(facade) as running:
        payload = http_post(
            running, "/api/reflection/proposals/decide", {"action": "accept"}, headers=auth(running)
        ).json()
    assert payload["status"] == "validation_failed"
    assert "待批准项" in payload["reason"]


def test_unexpected_failure_from_the_face_maps_to_failed_with_a_log_ref(reflection_server, http_post, auth):
    """其余异常 ⇒ `failed` + 可查 `log_ref`（[00 §6]：不静默、不当成输入错）。"""
    facade = FakeReflection(decide=RuntimeError("落盘损坏"))
    with reflection_server(facade) as running:
        payload = http_post(
            running,
            "/api/reflection/proposals/decide",
            {"action": "accept", "pending_id": "pnd_1"},
            headers=auth(running),
        ).json()
    assert payload["status"] == "failed"
    assert payload["log_ref"] and payload["log_ref"].startswith("ui/")


# ── A/B 实验日志与训练留痕 ────────────────────────────────────────────────────
def test_experiments_and_trainings_render_tables(reflection_server, http_get, auth):
    experiments = [{
        "experiment_id": "exp_1", "hypothesis": "新文案更易被采纳", "scope": "delivery-strategy",
        "sample": {"baseline": 3, "new": 4}, "result": {"baseline": 1, "new": 3},
        "decision": "adopt", "status": "decided",
    }]
    trainings = [{
        "session": {
            "training_id": "trn_1", "correction": "我这条不对，因为估值偏高",
            "restatement": "你关注的是估值水平", "pattern": {"topic": "估值"},
            "confirmed": True, "created_at": "2026-10-11T20:00:00+08:00",
        }
    }]
    with reflection_server(FakeReflection(experiments=experiments, trainings=trainings)) as running:
        exp = http_get(running, "/api/reflection/experiments", headers=auth(running)).json()
        trn = http_get(running, "/api/reflection/training", headers=auth(running)).json()
    assert exp["data"]["component_type"] == "table"
    assert exp["data"]["slots"]["rows"][0][0] == "新文案更易被采纳"
    assert exp["data"]["slots"]["columns"][-1] == "状态"
    # 训练留痕里含**用户原话**（第一人称）：整槽按 data ⇒ 不过中性化门、原样呈现（D-053）
    assert trn["status"] == "ok"
    assert trn["data"]["slots"]["rows"][0][1] == "我这条不对，因为估值偏高"


# ── 出站两闸：必填槽与渲染前中性化门 ──────────────────────────────────────────
def test_gated_description_enforces_required_slots():
    app = build_ui(host="127.0.0.1", port=1)
    with pytest.raises(ValueError, match="必填槽"):
        app._gated_description(
            "report_card", slots={}, text_kinds={}, title="反思报告"
        )


def test_gated_description_blocks_unneutral_generated_text():
    """`generated` 槽命中中性化规则 ⇒ 阻断（01 §6 执行点 2 在出站路径上，不可绕）。"""
    app = build_ui(host="127.0.0.1", port=1)
    with pytest.raises(ValueError):
        app._gated_description(
            "table",
            slots={"columns": ["我认为该买"], "rows": []},
            text_kinds={"columns": "generated", "rows": "data"},
            title="实验日志",
        )


def test_gated_description_passes_user_data_through():
    """`data` 槽含第一人称 ⇒ 放行（用户原话不是生成文案，[D-053]）。"""
    app = build_ui(host="127.0.0.1", port=1)
    description = app._gated_description(
        "table",
        slots={"columns": ["修正原话"], "rows": [["我觉得这条不对"]]},
        text_kinds={"columns": "generated", "rows": "data"},
        title="训练留痕",
    )
    payload = json.loads(json.dumps(description.model_dump(mode="json")))
    assert payload["slots"]["rows"] == [["我觉得这条不对"]]


def test_descriptions_carry_a_tz_aware_snapshot_time(reflection_server, http_get, auth):
    """`as_of` 带时区语义（01 §8）；解析不出带时区的时刻就不带该字段（不臆造）。"""
    with reflection_server(FakeReflection()) as running:
        payload = http_get(running, "/api/reflection/reports", headers=auth(running)).json()
    assert payload["data"]["as_of"] == "2026-10-11T20:00:00+08:00"
    assert datetime.fromisoformat(payload["data"]["as_of"]).tzinfo is not None
