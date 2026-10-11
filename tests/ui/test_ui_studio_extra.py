"""Studio 附加面（`/studio` 的模板 / 属性 / 版本 / 试跑）的回环面（[`T-UI-015.3`]）。

本组钉六件事（真组合根上的装配由 `tests/integration/test_ui_studio_extra_wiring.py` 钉）：

1. **五条屏各有载体**（[11-sitemap §2 能力] story-06：参数 / 属性 / 调试控制台 / 版本历史 /
   模板库；主画布与导入导出已交付）——**零新造型**：全是已实现型；
2. **试跑是进程内活会话**——`start` 登记，后续按 `trial_id` 取回**同一个对象**；会话不在本
   进程 ⇒ `unavailable` ＋ 说明（不冒充「没有这次试跑」）；
3. **试跑不放宽权限口径 / 不登记输出复用**——由 L1 `TrialRunner` 保证，本层不绕过；
4. **版本动作真落值**——激活 / 回滚经 `WorkflowStore`；未知动作即拒；
5. **fork 交既有画布会话**——未接入画布面 ⇒ `unavailable`（不退化成「只落个盘」）；
6. **路由形态与页面登记**——六读三写；`timeline_view` / `report_card` 的差异段在「未给 /
   一致」时是 `empty` ＋ 原因，不摆空表。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from st_agent.contracts.identifiers import TraceId
from st_agent.contracts.registry_types import SemVer
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.trace import Trace
from st_agent.l1.studio.errors import TemplateError
from st_agent.l1.workflow.errors import TrialStateError, WorkflowNotFoundError
from st_agent.l1.workflow.ids import base_of
from st_agent.l1.workflow.models import checked_dag
from st_agent.ui.app import PAGE_PATHS
from st_agent.ui.server import _GET_ROUTES, _POST_ROUTES, serve

_UI = Path(__file__).resolve().parents[2] / "src" / "st_agent" / "ui"


def _dag(
    flow_id: str = "wf_demo_v1.0",
    *,
    name: str = "演示流程",
    nodes: tuple[tuple[str, str], ...] = (("n1", "sk_demo_v1.0"),),
    edges: tuple[tuple[str, str, str], ...] = (),
    version: tuple[int, int] = (1, 0),
):
    return checked_dag(
        flow_id=flow_id,
        name=name,
        description="做一件中性的事",
        version=SemVer(major=version[0], minor=version[1]),
        nodes=tuple({"node_id": node, "skill_id": skill} for node, skill in nodes),
        edges=tuple(
            {"edge_id": edge, "from_node": src, "to_node": dst} for edge, src, dst in edges
        ),
    )


class _FakeStep:
    def __init__(self, order: int = 0, node_id: str = "n1") -> None:
        self.order = order
        self.node_id = node_id
        self.skill_id = "sk_demo_v1.0"
        self.value_sources = {"format": "literal"}
        self.envelope = ResultEnvelope.ok({})
        self.duration_ms = 7
        self.params = {"format": "brief"}

    @property
    def status(self) -> str:
        return self.envelope.status


class _FakeRecord:
    def __init__(self, *, status: str = "running", steps: int = 1) -> None:
        self.trial_id = "trial_1"
        self.flow_id = "wf_demo_v1.0"
        self.status = status
        self.steps = tuple(_FakeStep(order=index) for index in range(steps))
        self.started_at = datetime.now().astimezone()
        self.finished_at = None
        self.approved_permissions: tuple[str, ...] = ()
        self.injections: dict = {}


class _FakeTrial:
    """试跑会话替身：只记下被调用的驱动动作（真件的步进语义归 `tests/l1/`）。"""

    def __init__(self) -> None:
        self.trial_id = "trial_1"
        self.calls: list[str] = []
        self.trace = Trace(trace_id=TraceId.generate())

    def _drive(self, action: str) -> None:
        self.calls.append(action)

    def step(self) -> None:
        self._drive("step")

    def run_to_end(self) -> None:
        self._drive("run_to_end")

    def pause(self) -> None:
        self._drive("pause")

    def resume(self) -> None:
        self._drive("resume")

    def abort(self) -> None:
        self._drive("abort")

    def record(self) -> _FakeRecord:
        return _FakeRecord()


class _FakeSessions:
    def __init__(self, *trials: _FakeTrial) -> None:
        self._items = {trial.trial_id: trial for trial in trials}

    def put(self, trial: _FakeTrial) -> None:
        self._items[trial.trial_id] = trial

    def has(self, trial_id: str) -> bool:
        return trial_id in self._items

    def get(self, trial_id: str) -> _FakeTrial:
        try:
            return self._items[trial_id]
        except KeyError as exc:
            raise KeyError(trial_id) from exc


class _FakeWorkflows:
    def __init__(self, *dags, active: str = "", pending: tuple = ()) -> None:
        self._dags = list(dags)
        self._active = active
        self._pending = pending
        self.actions: list[tuple[str, str, str]] = []

    def list_all(self):
        return tuple(self._dags)

    def get(self, flow_id: str):
        for dag in self._dags:
            if dag.flow_id == flow_id:
                return dag
        raise WorkflowNotFoundError(f"工作流 {flow_id!r} 不存在")

    def list_versions(self, base: str):
        return tuple(dag for dag in self._dags if base_of(dag.flow_id) == base)

    def active(self, base: str):
        for dag in self._dags:
            if dag.flow_id == self._active:
                return dag
        return None

    def pending_checks(self):
        return tuple(self._pending)

    def activate(self, base: str, flow_id: str):
        self.actions.append(("activate", base, flow_id))
        self._active = flow_id
        return self.get(flow_id)

    def rollback(self, base: str, flow_id: str):
        self.actions.append(("rollback", base, flow_id))

        class _Change:
            change_id = "chg_1"
            changelog = "回滚"

        return _Change()

    def diff(self, a: str, b: str):
        class _Diff:
            flow_id_a, flow_id_b = a, b
            added_nodes = ("n2",)
            removed_nodes: tuple[str, ...] = ()
            changed_nodes: tuple[str, ...] = ()
            added_edges: tuple[str, ...] = ()
            removed_edges: tuple[str, ...] = ()
            name_changed = False
            description_changed = False
            schedule_changed = False

            def is_empty(self) -> bool:
                return False

        return _Diff()


class _FakeSummary:
    def __init__(self, template_id: str = "daily_st_brief") -> None:
        self.template_id = template_id
        self.name = "每日 ST 简报"
        self.description = "同步名单并聚合简报"
        self.node_count = 2
        self.skill_ids = ("sk_st_list_sync_v1.0",)
        self.missing_skill_ids: tuple[str, ...] = ()


class _FakeTemplates:
    def __init__(self, *, missing: bool = False) -> None:
        self.missing = missing

    def list(self):
        summary = _FakeSummary()
        if self.missing:
            summary.missing_skill_ids = ("sk_absent_v1.0",)
        return (summary,)

    def get(self, template_id: str):
        if template_id != "daily_st_brief":
            raise TemplateError(f"模板不存在：{template_id!r}")
        return _FakeSummary(template_id)

    def load(self, template_id: str):
        return _dag("wf_daily_st_brief_v0.0", name="每日 ST 简报", version=(0, 0))


class _FakeHistory:
    def __init__(self, *records) -> None:
        self._records = list(records)

    def list_all(self):
        return tuple(self._records)

    def list_for_flow(self, flow_id: str):
        return tuple(item for item in self._records if item.flow_id == flow_id)

    def compare(self, a: str, b: str):
        class _Diff:
            trial_id_a, trial_id_b = a, b
            added_nodes = ("n2",)
            removed_nodes: tuple[str, ...] = ()
            changed_nodes: tuple[str, ...] = ()

            def is_empty(self) -> bool:
                return False

        return _Diff()


class _FakeTrials:
    def __init__(self, *, history=None, trial=None) -> None:
        self.history = history or _FakeHistory()
        self._trial = trial or _FakeTrial()
        self.started: list[str] = []

    def start(self, dag, values=None):
        if values is None:
            self.started.append(dag.flow_id)
        else:
            self.started.append(f"{dag.flow_id}:{sorted(values)}")
        return self._trial


class _FakeHome:
    """`StudioHome` 的最小替身（真实判据在组合根算一次，见 `StudioFacade.home`）。"""

    is_empty = True
    one_line_entry = "用一句话描述你想要的工作流"


class _FakeStudio:
    def __init__(self, *, workflows=None, templates=None, trials=None, sessions=None,
                 open_template=None, skills=None, home=None) -> None:
        self.workflows = workflows or _FakeWorkflows(_dag())
        self.templates = templates or _FakeTemplates()
        self.trials = trials or _FakeTrials()
        self.sessions = sessions or _FakeSessions()
        self.open_template = open_template
        self.skills = skills
        self.home = home or _FakeHome()


class _FakeView:
    proposal_id = "tpl:daily_st_brief"
    base = "daily_st_brief"
    flow_id = "wf_daily_st_brief_v0.0"
    node_count = 2
    violations: tuple[str, ...] = ()


def _get(http_get, auth, path, **faces):
    with serve(dev=False, **faces) as running:
        return http_get(running, path, headers=auth(running)).json()


def _post(http_post, auth, path, body, **faces):
    with serve(dev=False, **faces) as running:
        return http_post(running, path, body, headers=auth(running)).json()


# ───────────────────────── 工作流清单与属性 ─────────────────────────


def test_the_workflow_list_is_a_table_with_the_active_marker(http_get, auth) -> None:
    workflows = _FakeWorkflows(
        _dag("wf_demo_v1.0"), _dag("wf_demo_v2.0", version=(2, 0)), active="wf_demo_v2.0"
    )
    payload = _get(http_get, auth, "/api/studio/workflows", studio=_FakeStudio(workflows=workflows))
    assert payload["status"] == "ok"
    rows = payload["data"]["slots"]["rows"]
    assert [row["flow_id"] for row in rows] == ["wf_demo_v1.0", "wf_demo_v2.0"]
    assert rows[1]["active"] == "生效"
    assert rows[0]["active"] == "—"


def test_no_workflow_reads_as_empty_with_the_empty_state_hint(http_get, auth) -> None:
    face = _FakeStudio(workflows=_FakeWorkflows())
    payload = _get(http_get, auth, "/api/studio/workflows", studio=face)
    assert payload["status"] == "empty"
    # 空态原因**指向本页真正有模板的那一段**（走查实测：这句原写「下方」，而模板库在上方）
    assert "模板库" in payload["reason"]


def test_the_property_panel_carries_six_sections(http_get, auth) -> None:
    workflows = _FakeWorkflows(
        _dag("wf_demo_v1.0", nodes=(("n1", "sk_a_v1.0"), ("n2", "sk_b_v1.0")),
             edges=(("e1", "n1", "n2"),), version=(1, 0)),
        active="wf_demo_v1.0",
    )
    face = _FakeStudio(workflows=workflows)
    payload = _get(http_get, auth, "/api/studio/workflow?flow=wf_demo_v1.0", studio=face)
    assert payload["status"] == "ok"
    assert payload["data"]["component_type"] == "report_card"
    sections = {item["title"]: item["lines"] for item in payload["data"]["slots"]["sections"]}
    assert set(sections) == {"基本信息", "调度", "节点", "连线", "引用方待检查"}
    assert "n1 → sk_a_v1.0" in sections["节点"]
    assert sections["调度"] == ["手动"]


def test_a_missing_flow_identifier_is_validation_failed(http_get, auth) -> None:
    face = _FakeStudio()
    for path in ("/api/studio/workflow", "/api/studio/versions"):
        payload = _get(http_get, auth, path, studio=face)
        assert payload["status"] == "validation_failed", path


# ───────────────────────── 版本历史与动作 ─────────────────────────


def test_versions_are_a_timeline_and_the_diff_says_when_it_was_not_asked_for(http_get, auth) -> None:
    workflows = _FakeWorkflows(_dag("wf_demo_v1.0"), active="wf_demo_v1.0")
    face = _FakeStudio(workflows=workflows)
    payload = _get(http_get, auth, "/api/studio/versions?flow=wf_demo_v1.0", studio=face)
    assert payload["versions"]["status"] == "ok"
    assert payload["versions"]["data"]["component_type"] == "timeline_view"
    assert payload["versions"]["data"]["slots"]["entries"][0]["at"] == "v1.0"
    assert "当前生效" in payload["versions"]["data"]["slots"]["entries"][0]["content"]
    # 未给两版 ⇒ 差异段如实说明，不摆空表
    assert payload["diff"]["status"] == "empty"
    assert payload["diff"]["reason"]


def test_the_diff_segment_lists_the_three_kinds_when_two_versions_are_given(http_get, auth) -> None:
    face = _FakeStudio(workflows=_FakeWorkflows(_dag("wf_demo_v1.0")))
    payload = _get(
        http_get, auth, "/api/studio/versions?flow=wf_demo_v1.0&a=wf_demo_v1.0&b=wf_demo_v2.0",
        studio=face,
    )
    assert payload["diff"]["status"] == "ok"
    lines = payload["diff"]["data"]["slots"]["sections"][0]["lines"]
    assert any(line.startswith("新增节点：n2") for line in lines)


@pytest.mark.parametrize(
    ("action", "expect"),
    [("activate", ("activate", "wf_demo", "wf_demo_v1.0")),
     ("rollback", ("rollback", "wf_demo", "wf_demo_v1.0"))],
)
def test_version_actions_go_through_the_real_keys(
    http_get, http_post, auth, action: str, expect: tuple
) -> None:
    workflows = _FakeWorkflows(_dag("wf_demo_v1.0"))
    face = _FakeStudio(workflows=workflows)
    payload = _post(
        http_post, auth, "/api/studio/workflow/version",
        {"flow_id": "wf_demo_v1.0", "action": action}, studio=face,
    )
    assert payload["status"] == "ok", payload.get("reason")
    assert workflows.actions == [expect]


def test_an_unknown_version_action_is_refused(http_get, http_post, auth) -> None:
    face = _FakeStudio(workflows=_FakeWorkflows(_dag("wf_demo_v1.0")))
    payload = _post(
        http_post, auth, "/api/studio/workflow/version",
        {"flow_id": "wf_demo_v1.0", "action": "explode"}, studio=face,
    )
    assert payload["status"] == "validation_failed"


# ───────────────────────── 模板库 ─────────────────────────


def test_templates_are_two_segments_and_list_missing_skills(http_get, auth) -> None:
    face = _FakeStudio(templates=_FakeTemplates(missing=True))
    payload = _get(http_get, auth, "/api/studio/templates", studio=face)
    assert payload["home"]["status"] == "ok"
    sections = {item["title"]: item["lines"]
                for item in payload["home"]["data"]["slots"]["sections"]}
    assert sections["一句话入口"] == ["用一句话描述你想要的工作流"]
    assert payload["templates"]["status"] == "ok"
    row = payload["templates"]["data"]["slots"]["rows"][0]
    assert row["missing"] == "sk_absent_v1.0"


def test_the_template_preview_carries_the_provisional_identity(http_get, auth) -> None:
    face = _FakeStudio()
    payload = _get(http_get, auth, "/api/studio/template?id=daily_st_brief", studio=face)
    assert payload["status"] == "ok"
    sections = {item["title"]: item["lines"] for item in payload["data"]["slots"]["sections"]}
    assert any("v0.0" in line for line in sections["基本信息"])
    assert sections["缺失 Skill"] == ["（无缺失）"]


def test_an_unknown_template_is_validation_failed(http_get, auth) -> None:
    face = _FakeStudio()
    payload = _get(http_get, auth, "/api/studio/template?id=nope", studio=face)
    assert payload["status"] == "validation_failed"


def test_fork_opens_the_existing_canvas_session(http_get, http_post, auth) -> None:
    """fork **交既有画布会话**（经 `open_template`），回的会话键可直接拼成 `?proposal_id=`。"""
    seen: list[str] = []

    def open_template(template_id: str):
        seen.append(template_id)
        return _FakeView()

    face = _FakeStudio(open_template=open_template)
    payload = _post(
        http_post, auth, "/api/studio/template/fork",
        {"template_id": "daily_st_brief"}, studio=face,
    )
    assert payload["status"] == "ok"
    assert payload["data"]["session_key"] == "tpl:daily_st_brief"
    assert payload["data"]["node_count"] == 2
    assert seen == ["daily_st_brief"]


def test_fork_without_the_canvas_face_is_unavailable(http_get, http_post, auth) -> None:
    """未接入画布面 ⇒ `unavailable` ＋ 点名——**不**退化成「只落个盘」。"""
    face = _FakeStudio()
    payload = _post(
        http_post, auth, "/api/studio/template/fork",
        {"template_id": "daily_st_brief"}, studio=face,
    )
    assert payload["status"] == "unavailable"
    assert "画布" in payload["reason"]


# ───────────────────────── 调试控制台 ─────────────────────────


def test_start_drives_the_trial_and_returns_three_segments(http_get, http_post, auth) -> None:
    """`start` 真起一次试跑：小结 / 逐节点快照 / 完整链三块同出，并回平键 `trial_id`。"""
    trials = _FakeTrials()
    sessions = _FakeSessions()
    face = _FakeStudio(workflows=_FakeWorkflows(_dag("wf_demo_v1.0")), trials=trials,
                       sessions=sessions)
    payload = _post(
        http_post, auth, "/api/studio/trial",
        {"action": "start", "flow_id": "wf_demo_v1.0"}, studio=face,
    )
    assert payload["trial_id"] == "trial_1"
    assert payload["summary"]["status"] == "ok"
    assert payload["steps"]["status"] == "ok"
    steps = payload["steps"]["data"]["slots"]["rows"]
    assert steps[0]["node_id"] == "n1"
    assert steps[0]["sources"] == "format=字面量"
    assert sessions.has("trial_1")
    assert trials.started == ["wf_demo_v1.0"]


@pytest.mark.parametrize("action", ["step", "run_to_end", "pause", "resume", "abort"])
def test_the_six_drivers_reach_the_same_trial(http_get, http_post, auth, action: str) -> None:
    trial = _FakeTrial()
    face = _FakeStudio(sessions=_FakeSessions(trial))
    payload = _post(
        http_post, auth, "/api/studio/trial",
        {"action": action, "trial_id": "trial_1"}, studio=face,
    )
    assert payload["summary"]["status"] == "ok", payload.get("reason")
    assert trial.calls == [action]


def test_an_unknown_driver_action_is_refused(http_get, http_post, auth) -> None:
    face = _FakeStudio(sessions=_FakeSessions(_FakeTrial()))
    payload = _post(
        http_post, auth, "/api/studio/trial",
        {"action": "rewind", "trial_id": "trial_1"}, studio=face,
    )
    assert payload["summary"]["status"] == "validation_failed"


def test_a_trial_not_in_this_process_reads_as_unavailable(http_get, http_post, auth) -> None:
    """会话表是**进程内**的：旧 id 无可续跑的活对象 ⇒ `unavailable` ＋ 说明。"""
    face = _FakeStudio(sessions=_FakeSessions())
    payload = _post(
        http_post, auth, "/api/studio/trial",
        {"action": "step", "trial_id": "trial_gone"}, studio=face,
    )
    assert payload["summary"]["status"] == "unavailable"
    assert "会话表" in payload["summary"]["reason"]


def test_a_trial_state_error_is_translated_to_validation_failed(http_get, http_post, auth) -> None:
    """`TrialStateError`（如已中止的会话再推进）⇒ `validation_failed`，不是 `failed`。"""

    class _Boom(_FakeTrial):
        def step(self) -> None:
            raise TrialStateError("试跑已中止，无法继续推进")

    face = _FakeStudio(sessions=_FakeSessions(_Boom()))
    payload = _post(
        http_post, auth, "/api/studio/trial",
        {"action": "step", "trial_id": "trial_1"}, studio=face,
    )
    assert payload["summary"]["status"] == "validation_failed"
    assert payload["summary"]["reason"]


def test_trial_history_is_a_table_and_the_compare_segment_explains_itself(http_get, auth) -> None:
    history = _FakeHistory(_FakeRecord(status="completed"))
    face = _FakeStudio(trials=_FakeTrials(history=history))
    payload = _get(http_get, auth, "/api/studio/trials?flow=wf_demo_v1.0", studio=face)
    assert payload["trials"]["status"] == "ok"
    row = payload["trials"]["data"]["slots"]["rows"][0]
    assert row["status"] == "已完成"
    assert payload["diff"]["status"] == "empty"


def test_no_trial_record_reads_as_empty(http_get, auth) -> None:
    face = _FakeStudio(trials=_FakeTrials(history=_FakeHistory()))
    payload = _get(http_get, auth, "/api/studio/trials?flow=wf_demo_v1.0", studio=face)
    assert payload["trials"]["status"] == "empty"
    assert payload["trials"]["reason"]


# ───────────────────────── 装配缺口与登记 ─────────────────────────


def test_the_face_not_wired_reads_as_unavailable_with_the_face_named(http_get, auth) -> None:
    payload = _get(http_get, auth, "/api/studio/workflows")
    assert payload["status"] == "unavailable"
    assert "未接入" in payload["reason"] and "Studio" in payload["reason"]


def test_routes_and_page_registration() -> None:
    for path in ("/api/studio/workflows", "/api/studio/workflow", "/api/studio/versions",
                 "/api/studio/templates", "/api/studio/template", "/api/studio/trials"):
        assert path in _GET_ROUTES, path
    for path in ("/api/studio/workflow/version", "/api/studio/template/fork",
                 "/api/studio/trial"):
        assert path in _POST_ROUTES, path
    assert "/studio" in PAGE_PATHS
    pages = (_UI / "web" / "js" / "pages.js").read_text(encoding="utf-8")
    assert "renderStudioPage" in pages


def test_new_endpoints_need_the_token(http_get) -> None:
    face = _FakeStudio()
    with serve(dev=False, studio=face) as running:
        for path in ("/api/studio/workflows", "/api/studio/workflow?flow=wf_demo_v1.0",
                     "/api/studio/versions?flow=wf_demo_v1.0", "/api/studio/templates",
                     "/api/studio/template?id=daily_st_brief", "/api/studio/trials"):
            assert http_get(running, path).status == 401, path
