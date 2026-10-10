"""推理区端点的形状与 fail-closed（[`T-UI-016.1`] / [`.2`] / [`.3`]）。

与 `tests/integration/test_ui_deliberation_wiring.py`（**真组合根**）分工：本组用一个
**真 `LensRoster`**（阵容读面 / 中性化校验走真件）+ **替身编排面**钉形状——

1. 三枚只读面（阵容 / 视角定义 / 决策留痕）未接线 ⇒ `unavailable` + 点名；
2. `analyze` **分多段**回（`reply` 信封 + `progress` / `divergence` / `lenses` 三段描述），
   且**校验在编排之前**（缺主题即拒，替身的 `run_analysis` 一次都没被调到）；
3. 阵容面板走 `setting_panel` 且面键为 `deliberation-lens`；**内置只给停用**（不摆必然失败的删除）；
4. 自定义视角命名 / 描述经真中性化校验拒（`validation_failed`，不落盘）；
5. 视角定义三种缺口互不冒充；决策提交形态归一。
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from st_agent.contracts import LensOpinion, Trace, TraceId, TraceStep, digest_of
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.storage import Store
from st_agent.l1.skills import SkillRegistry
from st_agent.l1.skills.pack import ensure_official_pack
from st_agent.l4.analyze import AnalyzeOutcome
from st_agent.l4.deliberation import DeliberationResult
from st_agent.l4.divergence_view import DivergenceView, LensRow
from st_agent.l4.roster import LensRoster
from st_agent.ui.server import _GET_ROUTES, _POST_ROUTES, serve

PASS = "correct horse battery staple"
_NOW = datetime.fromisoformat("2026-10-10T10:00:00+08:00")

_READ_PATHS = (
    "/api/deliberation/lenses",
    "/api/deliberation/lens?id=x",
    "/api/deliberation/decisions",
)
_GET_ONLY_PATHS = ("/api/deliberation/lens?id=x", "/api/deliberation/decisions")

_ANALYZE_PATH = "/api/deliberation/analyze"
_LENSES_PATH = "/api/deliberation/lenses"
_LENS_UPDATE_PATH = "/api/deliberation/lenses/update"
_DECISION_PATH = "/api/deliberation/decision"


def _trace(step_type: str = "skill_run") -> Trace:
    """一条带一步的真 `Trace`（描述件按 `steps` 出槽）。"""
    trace = Trace(trace_id=TraceId.generate())
    return trace.append_step(TraceStep(
        step_type=step_type,
        ref=trace.trace_id.value,
        input_digest=digest_of({"topic": "sh.600000"}),
        output_digest=digest_of({"ok": True}),
        duration_ms=3,
        timestamp=_NOW,
    ))


def _outcome(*, stance: str = "neutral") -> AnalyzeOutcome:
    """一次两视角编排的产出（真模型手搭：矩阵两行、两链、两观点）。"""
    traces = (_trace(), _trace("memory_read"))
    opinions = tuple(
        LensOpinion(
            lens_id=f"lens_view_{index}",
            stance=stance if index == 0 else "insufficient-data",
            key_reasons=(f"视角 {index} 的中性理由",),
            evidence_refs=("run_0000000001",),
            confidence="medium" if index == 0 else "low",
            skills_triggered=("run_0000000001",),
            trace_id=trace.trace_id.value,
        )
        for index, trace in enumerate(traces)
    )
    envelope = ResultEnvelope.ok([o.lens_id for o in opinions], as_of=_NOW)
    result = DeliberationResult(
        topic="是否关注中际旭创", mode="deep", envelope=envelope,
        opinions=opinions, traces=traces, lens_ids=tuple(o.lens_id for o in opinions),
    )
    view = DivergenceView(
        topic="是否关注中际旭创", map_id="map_0001", as_of=_NOW,
        rows=tuple(
            LensRow(
                lens_id=o.lens_id, name=f"视角{index}", stance=o.stance,
                confidence=o.confidence, trace_id=o.trace_id,
            )
            for index, o in enumerate(opinions)
        ),
    )
    return AnalyzeOutcome(
        envelope=envelope, topic=result.topic, mode=result.mode,
        result=result, view=view, traces=traces,
    )


class _FakeFace:
    """编排面替身：真 `LensRoster`（命名校验走真件）+ 手搭的编排产出。"""

    def __init__(self, roster: LensRoster, *, outcome: AnalyzeOutcome | None = None) -> None:
        self.roster = roster
        # `analyze` 非 None ＝「编排面已接线」（门面在组合根装配，缺省即 fail-closed）。
        self.analyze = True
        self.outcome = outcome or _outcome()
        self.calls: list[tuple[str, str]] = []
        self.recorded: list[dict] = []

    def run_analysis(self, *, topic: str, mode: str) -> AnalyzeOutcome:
        self.calls.append((topic, mode))
        return self.outcome

    def decisions(self) -> list[dict]:
        return list(self.recorded)

    def record_decision(self, **fields) -> ResultEnvelope:
        self.recorded.append(dict(fields))
        return ResultEnvelope.ok({"memory_node_id": "mn_decision_0001"})


@pytest.fixture()
def roster(tmp_path) -> LensRoster:
    store = Store.create(tmp_path / "root", PASS)
    registry = SkillRegistry(store)
    ensure_official_pack(registry)
    built = LensRoster(store, skills=registry)
    built.seed_builtin()
    return built


@pytest.fixture()
def face(roster: LensRoster) -> _FakeFace:
    return _FakeFace(roster)


# ───────────────────────── 1 · 未接线时 fail-closed ─────────────────────────

@pytest.mark.parametrize("path", _READ_PATHS)
def test_read_faces_are_unavailable_when_not_wired(ui_server, http_get, auth, path: str) -> None:
    """三枚只读面未注入 ⇒ `unavailable` + 点名（不 500、不装空表冒充「面在但无数据」）。"""
    payload = http_get(ui_server, path, headers=auth(ui_server)).json()
    assert payload["status"] == "unavailable"
    assert "多视角推理面" in payload["reason"]


def test_analyze_is_unavailable_when_not_wired(ui_server, http_post, auth) -> None:
    """编排面未注入 ⇒ `unavailable` + 点名，且**四段键齐**（读取方不必判键在不在）。"""
    payload = http_post(
        ui_server, _ANALYZE_PATH, {"topic": "x", "mode": "deep"}, headers=auth(ui_server)
    ).json()
    assert payload["reply"]["status"] == "unavailable"
    assert set(payload) == {"reply", "progress", "divergence", "lenses"}
    assert payload["progress"] is None and payload["divergence"] is None
    assert payload["lenses"] == []


# ───────────────────────── 2 · 触发：校验在编排之前 ─────────────────────────

@pytest.mark.parametrize("body", [{}, {"topic": "   "}, {"mode": "deep"}])
def test_analyze_rejects_a_missing_topic_before_running(
    http_post, auth, roster, face, body
) -> None:
    """GWT-1：缺主题 ⇒ `validation_failed`，且**编排一次都没跑**。"""
    with serve(dev=False, deliberation=face) as running:
        payload = http_post(running, _ANALYZE_PATH, body, headers=auth(running)).json()
    assert payload["reply"]["status"] == "validation_failed"
    assert "topic" in payload["reply"]["reason"]
    assert face.calls == [], "校验须在编排之前——缺主题时不得触发 run_analysis"


def test_analyze_returns_four_segments_on_success(http_post, auth, roster, face) -> None:
    """GWT-2：一次编排回「信封 + 逐视角进度 + 分歧图 + 逐视角段」，各自可独立渲染。"""
    with serve(dev=False, deliberation=face) as running:
        payload = http_post(
            running, _ANALYZE_PATH, {"topic": "是否关注中际旭创", "mode": "deep"},
            headers=auth(running),
        ).json()

    assert face.calls == [("是否关注中际旭创", "deep")]
    assert payload["reply"]["status"] == "ok"

    progress = payload["progress"]
    assert progress["status"] == "ok"
    assert progress["data"]["component_type"] == "report_card"
    lines = json.dumps(progress["data"]["slots"]["sections"], ensure_ascii=False)
    assert "数据不足" in lines, "非 ok 视角须显式呈现立场与原因（不合并、不静默）"

    divergence = payload["divergence"]
    assert divergence["data"]["component_type"] == "divergence_map"
    slots = divergence["data"]["slots"]
    assert len(slots["matrix"]) == 2, "全部参与视角照常成行（含 insufficient-data 者）"
    assert "network" in slots, "两视图均实现——证据网络图同样出槽"
    assert slots["stance_labels"], "立场词表由服务端下发（前端不自带词表）"

    lenses = payload["lenses"]
    assert [entry["lens_id"] for entry in lenses] == ["lens_view_0", "lens_view_1"]
    assert lenses[0]["opinion"]["data"]["component_type"] == "report_card"
    assert lenses[0]["trace"]["data"]["component_type"] == "trace_timeline"
    assert lenses[0]["trace"]["data"]["slots"]["steps"], "视角详情须带上该视角的完整链"


def test_lens_trace_anchors_match_the_matrix(http_post, auth, roster, face) -> None:
    """矩阵行的锚点 ⊆ 逐视角段的锚点——追问按同一键取值（[06 §5]）。"""
    with serve(dev=False, deliberation=face) as running:
        payload = http_post(
            running, _ANALYZE_PATH, {"topic": "t", "mode": "deep"}, headers=auth(running)
        ).json()
    matrix_ids = {row["trace_id"] for row in payload["divergence"]["data"]["slots"]["matrix"]}
    lens_ids = {entry["trace_id"] for entry in payload["lenses"]}
    assert matrix_ids and matrix_ids <= lens_ids


# ───────────────────────── 3 · 阵容面 ─────────────────────────

def test_lens_panel_uses_the_deliberation_surface_key(roster, face, http_get, auth) -> None:
    """GWT-5：阵容面板走 `setting_panel`，面键＝ `deliberation-lens`，内置**不给删除动作**。"""
    with serve(dev=False, deliberation=face) as running:
        payload = http_get(running, _LENSES_PATH, headers=auth(running)).json()
    assert payload["status"] == "ok"
    description = payload["data"]
    assert description["component_type"] == "setting_panel"
    assert description["slots"]["surface"] == "deliberation-lens"
    assert set(description["slots"]["labels"]) >= {"enable", "disable", "remove"}
    entries = description["slots"]["entries"]
    assert len(entries) == 7, "官方预置 7 视角（06 §1）"
    for entry in entries:
        assert entry["actions"] == ["disable"], "内置视角只能停用不可删——不摆必然失败的按钮"
        assert entry["params"]["lens_id"]


def test_custom_lens_can_be_created_and_then_removed(
    face, http_post, http_get, auth
) -> None:
    """GWT-4：合法命名的自定义视角落盘进阵容，且**自定义可删**（内置不可）。"""
    with serve(dev=False, deliberation=face) as running:
        created = http_post(
            running, _LENSES_PATH,
            {"name": "供应链视角", "description": "评估上游供给与交付节奏",
             "skill_bundle": ["sk_data_aggregate_v1.0"],
             "judging_criteria": {"natural": "以上游供给与交付节奏为准"}},
            headers=auth(running),
        ).json()
        assert created["status"] == "ok", created
        lens_id = created["data"]["lens_id"]

        listed = http_get(running, _LENSES_PATH, headers=auth(running)).json()
        entries = listed["data"]["slots"]["entries"]
        custom = [entry for entry in entries if entry["params"]["lens_id"] == lens_id]
        assert custom and custom[0]["actions"] == ["disable", "remove"]

        removed = http_post(
            running, _LENS_UPDATE_PATH, {"action": "remove", "lens_id": lens_id},
            headers=auth(running),
        ).json()
        assert removed["status"] == "ok"


@pytest.mark.parametrize(
    "body",
    [
        {"name": "老张", "description": "关注上涨机会", "judging_criteria": {"natural": "x"}},
        {"name": "激进派", "description": "关注上涨机会", "judging_criteria": {"natural": "x"}},
        {"name": "机会观察", "description": "我认为应该关注上涨", "judging_criteria": {"natural": "x"}},
        {"name": "机会观察", "description": "关注上涨机会", "judging_criteria": {}},
        {"name": "机会观察", "description": "关注上涨机会", "skill_bundle": "sk_data_aggregate_v1.0",
         "judging_criteria": {"natural": "x"}},
    ],
)
def test_lens_create_rejects_illegal_input(face, http_post, auth, body) -> None:
    """GWT-4：拟人化命名 / 描述、缺评判准则、`skill_bundle` 形态错 ⇒ 拒且**不落盘**。"""
    with serve(dev=False, deliberation=face) as running:
        payload = http_post(running, _LENSES_PATH, body, headers=auth(running)).json()
    assert payload["status"] == "validation_failed"
    assert len(face.roster.list_all()) == 7, "被拒的创建不得落盘"


def test_removing_a_builtin_lens_is_rejected(face, roster, http_post, http_get, auth) -> None:
    """GWT-5：内置视角删除显式拒（`validation_failed`），阵容不变。"""
    builtin_id = roster.list_all()[0].lens_id
    with serve(dev=False, deliberation=face) as running:
        payload = http_post(
            running, _LENS_UPDATE_PATH, {"action": "remove", "lens_id": builtin_id},
            headers=auth(running),
        ).json()
        after = http_get(running, _LENSES_PATH, headers=auth(running)).json()
    assert payload["status"] == "validation_failed"
    assert "不可删除" in payload["reason"]
    assert after["data"]["slots"]["entries"], "内置仍在阵容里"


def test_toggling_a_lens_is_recorded(face, roster, http_post, http_get, auth) -> None:
    """启用 / 停用可切换，且停用后该视角仍在阵容里（停用不落删除）。"""
    builtin_id = roster.list_all()[0].lens_id
    with serve(dev=False, deliberation=face) as running:
        payload = http_post(
            running, _LENS_UPDATE_PATH, {"action": "disable", "lens_id": builtin_id},
            headers=auth(running),
        ).json()
        listed = http_get(running, _LENSES_PATH, headers=auth(running)).json()
        entries = listed["data"]["slots"]["entries"]
    assert payload["status"] == "ok" and payload["data"]["enabled"] is False
    row = [entry for entry in entries if entry["params"]["lens_id"] == builtin_id][0]
    assert row["current"] == "停用" and row["actions"] == ["enable"]


# ───────────────────────── 4 · 视角定义读面 ─────────────────────────

def test_lens_definition_reports_three_distinct_gaps(face, roster, http_get, auth) -> None:
    """缺 id / 格式非法 / 不在阵容——三种缺口互不冒充（同 `T-UI-014.2` 的口径）。

    「不在阵容」用**格式合法却不存在**的 `lens_id`：`LensId.of` 的形态校验是产生方与消费方
    共用的单一真相源，`lens_ghost` 这类会先撞形态校验（那是**输入非法**，不是**寻址失败**）。
    """
    absent = "lens_ffffffffffffffffffff"
    with serve(dev=False, deliberation=face) as running:
        missing = http_get(running, "/api/deliberation/lens?id=", headers=auth(running)).json()
        malformed = http_get(
            running, "/api/deliberation/lens?id=lens_ghost", headers=auth(running)
        ).json()
        unknown = http_get(
            running, f"/api/deliberation/lens?id={absent}", headers=auth(running)
        ).json()
        known = http_get(
            running, f"/api/deliberation/lens?id={roster.list_all()[0].lens_id}",
            headers=auth(running),
        ).json()
    assert missing["status"] == "validation_failed"
    assert malformed["status"] == "validation_failed"
    assert "id" in missing["reason"] and absent not in malformed["reason"], "两种入错互不冒充"
    assert unknown["status"] == "unavailable" and absent in unknown["reason"]
    assert known["status"] == "ok"
    assert known["data"]["component_type"] == "report_card"


# ───────────────────────── 5 · 决策记录 ─────────────────────────

def test_decisions_is_empty_before_any_record(face, http_get, auth) -> None:
    """无留痕 ⇒ `empty` + 原因（不静默留空）。"""
    with serve(dev=False, deliberation=face) as running:
        payload = http_get(running, "/api/deliberation/decisions", headers=auth(running)).json()
    assert payload["status"] == "empty"


def test_decision_requires_a_decision_text(face, http_post, http_get, auth) -> None:
    """缺决策文本 ⇒ `validation_failed`；给了即转发（未采纳视角由表单侧补齐，见页面）。"""
    with serve(dev=False, deliberation=face) as running:
        rejected = http_post(
            running, _DECISION_PATH, {"reasoning": "因为"}, headers=auth(running)
        ).json()
        accepted = http_post(
            running, _DECISION_PATH,
            {"decision": "不关注", "reasoning": "风险偏高",
             "adopted_lens_ids": ["lens_view_0"], "ignored_lens_ids": ["lens_view_1"]},
            headers=auth(running),
        ).json()
        listed = http_get(running, "/api/deliberation/decisions", headers=auth(running)).json()
    assert rejected["status"] == "validation_failed"
    assert accepted["status"] == "ok"
    assert face.recorded[0]["ignored_lens_ids"] == ("lens_view_1",)
    assert listed["status"] == "ok" and listed["data"]["component_type"] == "report_card"


# ───────────────────────── 6 · 守卫与路由表 ─────────────────────────

@pytest.mark.parametrize("path", (*_READ_PATHS, _ANALYZE_PATH, _LENS_UPDATE_PATH, _DECISION_PATH))
def test_new_endpoints_still_require_the_token(ui_server, http_get, http_post, path: str) -> None:
    """推理区端点属 `/api/*` 数据面，恒要令牌（守卫不放宽）。"""
    if path in _POST_ROUTES:
        response = http_post(ui_server, path, {})
    else:
        response = http_get(ui_server, path)
    assert response.status == 401


def test_the_read_faces_stay_out_of_the_write_table() -> None:
    """只读面不得进写表；四条写面必须在（[01 §12] 动作不进描述的另一面）。

    `/api/deliberation/lenses` 是**一读一写同路径**（`GET` 列阵容、`POST` 建视角），故它两侧都在
    ——与其余只读面（`GET` 独占）分开断言。
    """
    for path in _GET_ONLY_PATHS:
        assert path.split("?")[0] in _GET_ROUTES
        assert path.split("?")[0] not in _POST_ROUTES
    assert _LENSES_PATH in _GET_ROUTES and _LENSES_PATH in _POST_ROUTES
    for path in (_ANALYZE_PATH, _LENS_UPDATE_PATH, _DECISION_PATH):
        assert path in _POST_ROUTES
