"""Skill 库（`/skills` —— 能力区 story-02 四条屏）的回环面（[`T-UI-015.1`]）。

本组钉六件事（真组合根上的装配由 `tests/integration/test_ui_skills_wiring.py` 钉）：

1. **四种缺口互不冒充**（[01 §5]）——缺查询串 / 标识**形态**非法 ⇒ `validation_failed`；
   标识合法但**未注册** ⇒ `unavailable` ＋ 点名；能力池空 / 该 Skill 未执行过 ⇒ `empty`
   ＋ 原因；
2. **参数面板的取值面按类型选**（渲染件头注的三态）——`enum` / `boolean` 给候选下拉、
   其余给自由文本；**值域仍归后端**；
3. **参数落值经唯一写口**（[03 §1.1]）且**超范围即拒不保存**（story-02 边缘情况）；
4. **执行日志是 `skill-run/` 前缀的只读列表**——无法解析的记录**计数并披露**，不谎报为 0；
5. **两道闸在出站点生效**——必填槽（注册表镜像）＋ 渲染前中性门，不过闸即 `validation_failed`；
6. **路由形态与页面登记**——四条只读在 `_GET_ROUTES`、一条写在 `_POST_ROUTES`；`/skills`
   挂上 `render`（不再是六态探针页）；`setting_panel` 渲染件认得 `skill-params` 面键。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from st_agent.contracts.capability_types import Provenance, SkillDescriptor
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l1.skills.ids import check_skill_id
from st_agent.l1.skills.errors import SkillNotFoundError
from st_agent.ui.app import PAGE_PATHS
from st_agent.ui.server import _GET_ROUTES, _POST_ROUTES, serve

_UI = Path(__file__).resolve().parents[2] / "src" / "st_agent" / "ui"


def _descriptor(
    skill_id: str = "sk_demo_v1.0",
    *,
    name: str = "演示能力",
    parameters: tuple[dict, ...] = (),
    permissions: tuple[str, ...] = (),
    source: str = "official",
    dependencies: tuple[str, ...] = (),
) -> SkillDescriptor:
    """造一个描述体（**不经**注册表的中性化校验——本组要能注入不合格的名字）。"""
    return SkillDescriptor(
        skill_id=skill_id,
        name=name,
        description="做一件中性的事",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        output_schema={"type": "object", "properties": {"rows": {"type": "array"}}},
        parameters=tuple(parameters),
        dependencies=dependencies,
        source=source,
        provenance=Provenance(),
        permissions=permissions,
        offline_level="full",
        version_policy="follow-latest",
    )


class _FakeStore:
    """`execution_log` 分区的小替身（只认 `list_files` / `get`）。"""

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self._files = dict(files or {})

    def list_files(self, partition: str) -> tuple[str, ...]:
        assert partition == "execution_log"
        return tuple(self._files)

    def get(self, partition: str, name: str) -> bytes:
        assert partition == "execution_log"
        try:
            return self._files[name]
        except KeyError as exc:
            raise KeyError(name) from exc


class _FakeRegistry:
    """`SkillRegistry` 的鸭子面替身：只实现本叶消费的三个口。

    `get` 的**形态**校验走真件 `check_skill_id`——否则「标识非法」与「未注册」在替身里会
    混成同一个 `SkillNotFoundError`，而那正是本叶要分开的两件事。
    """

    def __init__(self, *descriptors: SkillDescriptor) -> None:
        self._by_id = {item.skill_id: item for item in descriptors}
        self.applied: list[tuple[str, dict]] = []

    def list_all(self) -> tuple[SkillDescriptor, ...]:
        return tuple(self._by_id.values())

    def get(self, skill_id: str) -> SkillDescriptor:
        check_skill_id(skill_id)
        try:
            return self._by_id[skill_id]
        except KeyError as exc:
            raise SkillNotFoundError(f"Skill {skill_id!r} 不存在") from exc

    def set_parameters(self, skill_id: str, values: dict) -> SkillDescriptor:
        from st_agent.l1.skills.registry import validate_param_values

        descriptor = self.get(skill_id)
        merged = validate_param_values(descriptor.parameters, values)
        self.applied.append((skill_id, dict(values)))
        updated = descriptor.model_copy(update={
            "parameters": tuple(
                spec.model_copy(update={"default": merged[spec.name]})
                for spec in descriptor.parameters
            )
        })
        self._by_id[skill_id] = updated
        return updated


class _FakeSkills:
    def __init__(self, registry: _FakeRegistry, store: _FakeStore | None = None) -> None:
        self.registry = registry
        self.store = store


def _run_record(
    skill_id: str = "sk_demo_v1.0",
    *,
    run_id: str = "run_1",
    status: str = "ok",
    duration_ms: int = 12,
    started_at: str = "2026-10-11T10:00:00+08:00",
    params: dict | None = None,
) -> bytes:
    return json.dumps({
        "skill_run_id": run_id,
        "skill_id": skill_id,
        "trace_id": "tr_1",
        "params": params if params is not None else {"exchange": "all"},
        "inputs": {},
        "upstream": [],
        "envelope": {"status": status},
        "duration_ms": duration_ms,
        "started_at": started_at,
    }, ensure_ascii=False).encode("utf-8")


def _get(http_get, auth, path, **faces):
    with serve(dev=False, **faces) as running:
        return http_get(running, path, headers=auth(running)).json()


def _post(http_post, auth, path, body, **faces):
    with serve(dev=False, **faces) as running:
        return http_post(running, path, body, headers=auth(running)).json()


# ───────────────────────── 清单 ─────────────────────────


def test_catalog_is_a_table_and_says_empty_when_the_pool_is_empty(http_get, auth) -> None:
    """空池是 `empty` ＋ 原因（[01 §5]：不返回裸空数组冒充「读到了，只是没有」）。"""
    face = _FakeSkills(_FakeRegistry())
    payload = _get(http_get, auth, "/api/skills/catalog", skills=face)
    assert payload["status"] == "empty"
    assert payload["reason"]


def test_catalog_lists_every_registered_version(http_get, auth) -> None:
    """清单逐条并列（`table`），含**全部已注册版本**——如实不折叠。"""
    face = _FakeSkills(_FakeRegistry(
        _descriptor("sk_demo_v1.0"),
        _descriptor("sk_demo_v2.0", dependencies=("sk_other_v1.0",)),
    ))
    payload = _get(http_get, auth, "/api/skills/catalog", skills=face)
    assert payload["status"] == "ok"
    assert payload["data"]["component_type"] == "table"
    rows = payload["data"]["slots"]["rows"]
    assert [row["skill_id"] for row in rows] == ["sk_demo_v1.0", "sk_demo_v2.0"]
    assert rows[0]["source"] == "官方预置"
    assert rows[1]["dependencies"] == "sk_other_v1.0"


# ───────────────────────── 详情 ─────────────────────────


def test_detail_carries_all_five_sections_and_shows_the_empty_permission_set(
    http_get, auth
) -> None:
    """官方 Pack 的 `permissions=()` 是**如实的一段**（「无权限声明」），不隐藏该段。"""
    face = _FakeSkills(_FakeRegistry(_descriptor()))
    payload = _get(http_get, auth, "/api/skills/detail?skill=sk_demo_v1.0", skills=face)
    assert payload["status"] == "ok"
    assert payload["data"]["component_type"] == "report_card"
    sections = {item["title"]: item["lines"] for item in payload["data"]["slots"]["sections"]}
    assert set(sections) == {"基本信息", "可配参数", "依赖", "权限声明", "输入 / 输出契约"}
    assert sections["权限声明"] == ["（无权限声明）"]
    assert sections["依赖"] == ["（无依赖）"]
    assert sections["输入 / 输出契约"][0].startswith("输入：query")


# ───────────────────────── 参数面板 ─────────────────────────


def _param(name, type_, default, **kw):
    return {"name": name, "type": type_, "default": default, "description": f"{name} 的说明", **kw}


def test_param_entries_pick_the_value_surface_by_type(http_get, auth) -> None:
    """三态取值面：候选下拉（enum / boolean）与自由文本（number / string），见渲染件头注。"""
    face = _FakeSkills(_FakeRegistry(_descriptor(parameters=(
        _param("exchange", "enum", "all", choices=("all", "sh")),
        _param("top_n", "integer", 20, min_value=1, max_value=100),
        _param("verbose", "boolean", False),
        _param("keywords", "string", ""),
    ))))
    payload = _get(http_get, auth, "/api/skills/params?skill=sk_demo_v1.0", skills=face)
    assert payload["status"] == "ok"
    assert payload["data"]["component_type"] == "setting_panel"
    assert payload["data"]["slots"]["surface"] == "skill-params"
    entries = {item["identifier"]: item for item in payload["data"]["slots"]["entries"]}
    assert [option["value"] for option in entries["exchange"]["options"]] == ["all", "sh"]
    assert entries["exchange"]["current"] == "all"
    assert [option["value"] for option in entries["verbose"]["options"]] == ["true", "false"]
    assert entries["verbose"]["current"] == "false"
    assert "options" not in entries["top_n"] or not entries["top_n"].get("options")
    assert entries["top_n"]["input"] == "text"
    assert entries["keywords"]["input"] == "text"
    assert entries["top_n"]["params"] == {"skill_id": "sk_demo_v1.0", "name": "top_n"}


def test_params_of_a_skill_without_parameters_is_empty(http_get, auth) -> None:
    """无参数不是「读不到」——`empty` ＋ 原因（[03 §1.2]：`parameters` 可为空）。"""
    face = _FakeSkills(_FakeRegistry(_descriptor()))
    payload = _get(http_get, auth, "/api/skills/params?skill=sk_demo_v1.0", skills=face)
    assert payload["status"] == "empty"
    assert payload["reason"]


def test_params_set_lands_via_the_only_writer(http_get, http_post, auth) -> None:
    """落值经 `set_parameters`，回 `applied`——**不**回 `changed`（描述体写入不留 ChangeRecord）。"""
    registry = _FakeRegistry(_descriptor(parameters=(
        _param("exchange", "enum", "all", choices=("all", "sh")),
    )))
    payload = _post(
        http_post, auth, "/api/skills/params",
        {"skill_id": "sk_demo_v1.0", "name": "exchange", "value": "sh"},
        skills=_FakeSkills(registry),
    )
    assert payload["status"] == "ok"
    assert payload["data"] == {"applied": True}
    assert registry.applied == [("sk_demo_v1.0", {"exchange": "sh"})]


def test_params_set_coerces_the_form_string_to_the_declared_type(http_get, http_post, auth) -> None:
    """表单值恒为字符串：`"30"` 要还原成整数，否则 `type=integer` 的参数会被判非法。"""
    registry = _FakeRegistry(_descriptor(parameters=(
        _param("top_n", "integer", 20, min_value=1, max_value=100),
    )))
    payload = _post(
        http_post, auth, "/api/skills/params",
        {"skill_id": "sk_demo_v1.0", "name": "top_n", "value": "30"},
        skills=_FakeSkills(registry),
    )
    assert payload["status"] == "ok", payload.get("reason")
    assert registry.applied == [("sk_demo_v1.0", {"top_n": 30})]


@pytest.mark.parametrize(
    "body",
    [
        {"skill_id": "sk_demo_v1.0", "name": "top_n", "value": "500"},   # 超上限
        {"skill_id": "sk_demo_v1.0", "name": "top_n", "value": "abc"},   # 非数
        {"skill_id": "sk_demo_v1.0", "name": "nope", "value": "1"},      # 未声明的参数名
        {"skill_id": "sk_demo_v1.0", "name": "top_n"},                   # 缺取值
        {"skill_id": "sk_demo_v1.0"},                                     # 缺参数名
    ],
)
def test_a_bad_parameter_value_is_refused_and_not_saved(http_get, http_post, auth, body) -> None:
    """超范围 / 类型不符 / 未声明 ⇒ `validation_failed`（story-02 边缘情况：不允许保存）。"""
    registry = _FakeRegistry(_descriptor(parameters=(
        _param("top_n", "integer", 20, min_value=1, max_value=100),
    )))
    payload = _post(http_post, auth, "/api/skills/params", body, skills=_FakeSkills(registry))
    assert payload["status"] == "validation_failed"
    assert payload["reason"]
    assert registry.applied == []


# ───────────────────────── 执行日志 ─────────────────────────


def test_logs_read_the_skill_run_prefix_as_a_table(http_get, auth) -> None:
    """逐条留痕进 `table`；别的 Skill 的记录与别的分区前缀都不混进来。"""
    store = _FakeStore({
        "skill-run/run_1.json": _run_record("sk_demo_v1.0", run_id="run_1"),
        "skill-run/run_2.json": _run_record("sk_other_v1.0", run_id="run_2"),
        "workflow-trial/tr_1.json": _run_record("sk_demo_v1.0", run_id="run_3"),
    })
    face = _FakeSkills(_FakeRegistry(_descriptor()), store)
    payload = _get(http_get, auth, "/api/skills/logs?skill=sk_demo_v1.0", skills=face)
    assert payload["status"] == "ok"
    rows = payload["data"]["slots"]["rows"]
    assert [row["skill_run_id"] for row in rows] == ["run_1"]
    assert rows[0]["status"] == "完成"
    assert rows[0]["duration_ms"] == 12
    assert rows[0]["upstream"] == "（无）"


def test_logs_come_back_newest_first(http_get, auth) -> None:
    """新在前（时间线口径与其余留痕面一致）。"""
    store = _FakeStore({
        "skill-run/run_a.json": _run_record(
            run_id="run_a", started_at="2026-10-10T09:00:00+08:00"),
        "skill-run/run_b.json": _run_record(
            run_id="run_b", started_at="2026-10-11T09:00:00+08:00"),
    })
    face = _FakeSkills(_FakeRegistry(_descriptor()), store)
    payload = _get(http_get, auth, "/api/skills/logs?skill=sk_demo_v1.0", skills=face)
    assert [row["skill_run_id"] for row in payload["data"]["slots"]["rows"]] == ["run_b", "run_a"]


def test_a_skill_that_never_ran_reads_as_empty_not_ok(http_get, auth) -> None:
    """未执行过 ⇒ `empty` ＋ 原因——不是 `ok` 空表，也不是 `unavailable`。"""
    face = _FakeSkills(_FakeRegistry(_descriptor()), _FakeStore())
    payload = _get(http_get, auth, "/api/skills/logs?skill=sk_demo_v1.0", skills=face)
    assert payload["status"] == "empty"
    assert payload["reason"]


def test_unreadable_records_are_counted_not_silently_dropped(http_get, auth) -> None:
    """坏记录**计数并披露**（[01 §5]：不谎报为 0）。"""
    store = _FakeStore({
        "skill-run/run_1.json": _run_record("sk_demo_v1.0", run_id="run_1"),
        "skill-run/run_bad.json": b"{ not json",
    })
    face = _FakeSkills(_FakeRegistry(_descriptor()), store)
    payload = _get(http_get, auth, "/api/skills/logs?skill=sk_demo_v1.0", skills=face)
    assert payload["status"] == "ok"
    assert "无法解析" in payload["data"]["title"]


def test_a_bad_limit_is_refused_not_clamped(http_get, auth) -> None:
    """`limit` 非法即拒（不夹取）；正整数则照用。"""
    face = _FakeSkills(_FakeRegistry(_descriptor()), _FakeStore())
    for bad in ("abc", "0", "-3"):
        payload = _get(http_get, auth, f"/api/skills/logs?skill=sk_demo_v1.0&limit={bad}", skills=face)
        assert payload["status"] == "validation_failed", bad


# ───────────────────────── 缺口分流与装配 ─────────────────────────


@pytest.mark.parametrize("path", ["/api/skills/detail", "/api/skills/params", "/api/skills/logs"])
def test_a_missing_identifier_is_validation_failed(http_get, auth, path) -> None:
    """缺查询串 ⇒ `validation_failed`（请求不完整，不是「没有这个能力」）。"""
    payload = _get(http_get, auth, path, skills=_FakeSkills(_FakeRegistry(_descriptor())))
    assert payload["status"] == "validation_failed"


@pytest.mark.parametrize("path", ["/api/skills/detail", "/api/skills/params", "/api/skills/logs"])
def test_a_malformed_identifier_is_validation_failed(http_get, auth, path) -> None:
    """标识**形态**非法 ⇒ `validation_failed`（`check_skill_id` 抛的是 `ValueError`）。"""
    payload = _get(http_get, auth, f"{path}?skill=nope", skills=_FakeSkills(_FakeRegistry()))
    assert payload["status"] == "validation_failed"


@pytest.mark.parametrize("path", ["/api/skills/detail", "/api/skills/params", "/api/skills/logs"])
def test_an_unknown_but_well_formed_identifier_is_unavailable(http_get, auth, path) -> None:
    """形态合法但**未注册** ⇒ `unavailable` ＋ 点名——与「参数非法」互不冒充。"""
    payload = _get(http_get, auth, f"{path}?skill=sk_absent_v1.0", skills=_FakeSkills(_FakeRegistry()))
    assert payload["status"] == "unavailable"
    assert payload["reason"]


def test_the_face_not_wired_reads_as_unavailable_with_the_task_named(http_get, auth) -> None:
    """端口未注入 ⇒ `unavailable` ＋ 点名（装配归生产入口），不 500、不装空表。"""
    payload = _get(http_get, auth, "/api/skills/catalog")
    assert payload["status"] == "unavailable"
    assert "未接入" in payload["reason"] and "Skill" in payload["reason"]


def test_a_non_neutral_skill_name_is_blocked_at_the_render_gate(http_get, auth) -> None:
    """两道闸在**出站点**生效：`title` 恒受检（[01 §12]），故含第一人称的名字回
    `validation_failed` 而**不出**可渲染的描述。

    注：注册期还有一道 `check_name`（[01 §6]）——本组绕开它注入不合格的名字，验的正是
    **第二道**（渲染前门）独立成立，而不是「反正注册时就拦了」。
    """
    face = _FakeSkills(_FakeRegistry(_descriptor(name="我帮你选股")))
    payload = _get(http_get, auth, "/api/skills/detail?skill=sk_demo_v1.0", skills=face)
    assert payload["status"] == "validation_failed"
    assert "中性" in payload["reason"]


# ───────────────────────── 路由与页面登记 ─────────────────────────


def test_routes_and_page_registration() -> None:
    """四条只读进 `_GET_ROUTES`、一条写进 `_POST_ROUTES`；`/skills` 在 `PAGE_PATHS` 且挂 `render`。"""
    for path in ("/api/skills/catalog", "/api/skills/detail", "/api/skills/params", "/api/skills/logs"):
        assert path in _GET_ROUTES, path
    assert "/api/skills/params" in _POST_ROUTES
    assert "/skills" in PAGE_PATHS

    pages = (_UI / "web" / "js" / "pages.js").read_text(encoding="utf-8")
    assert "renderSkillsPage" in pages
    assert "render: renderSkillsPage" in pages

    panel = (_UI / "web" / "js" / "plugins" / "reflection" / "setting_panel.js").read_text(
        encoding="utf-8"
    )
    assert "'skill-params'" in panel and "/api/skills/params" in panel


def test_new_endpoints_need_the_token(http_get) -> None:
    """守卫不放宽：`/api/*` 一律要令牌（本叶新增的四读一写同样如此）。"""
    with serve(dev=False, skills=_FakeSkills(_FakeRegistry(_descriptor()))) as running:
        for path in (
            "/api/skills/catalog",
            "/api/skills/detail?skill=sk_demo_v1.0",
            "/api/skills/params?skill=sk_demo_v1.0",
            "/api/skills/logs?skill=sk_demo_v1.0",
        ):
            assert http_get(running, path).status == 401, path
