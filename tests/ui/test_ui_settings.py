"""设置区端点：**面缺席即 fail-closed**、输入域校验与纯函数口径（[`T-UI-018`]）。

与 `tests/integration/test_ui_settings_wiring.py`（真组合根）分工：本组只钉「没接线时会怎样」
与「表单值怎么还原成声明类型」——两者都不需要真 L0。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from st_agent.l0.storage import Store
from st_agent.ui.app import build_ui
from st_agent.l1.skills.tool_selection import (
    TOOL_SELECTION_PATH,
    set_tool_enabled,
)
from st_agent.ui.surfaces.settings.shared import (
    _bytes_text,
    _coerce_value,
    _is_inside,
    _window_start,
)

_GET_PATHS = (
    "/api/settings/storage",
    "/api/settings/llm",
    "/api/settings/data",
    "/api/settings/data/job",
    "/api/settings/sources",
    "/api/settings/web",
    "/api/settings/command",
    "/api/settings/privacy",
    "/api/settings/maintenance",
    "/api/settings/network",
    "/api/settings/offline",
    "/api/settings/agent-authorization",
)

_POST_PATHS = (
    "/api/settings/apply",
    "/api/settings/agent-authorization/rollback",
    "/api/settings/store/relocate",
    "/api/settings/llm/bootstrap",
    "/api/settings/data/sync",
    "/api/settings/data/preflight",
    "/api/settings/data/sync/cancel",
    "/api/settings/convert",
    "/api/settings/backup",
    "/api/settings/restore",
    "/api/settings/wipe",
    "/api/settings/credential",
    "/api/settings/online",
    "/api/settings/network/export",
)


@pytest.mark.parametrize("path", _GET_PATHS)
def test_read_endpoints_fail_closed_without_the_face(ui_server, http_get, auth, path: str) -> None:
    """面缺席 ⇒ `unavailable` + 点名（不 500、不装空表）；多段端点**每段**都是同一支错误。"""
    payload = http_get(ui_server, path, headers=auth(ui_server)).json()
    if "status" in payload:  # 单段端点
        assert payload["status"] == "unavailable"
        assert "未接入" in payload["reason"]
    else:  # 多段端点：逐段都是 unavailable
        assert payload, "多段端点不该回空体"
        for part in payload.values():
            assert part["status"] == "unavailable"
            assert "未接入" in part["reason"]


@pytest.mark.parametrize("path", _POST_PATHS)
def test_write_endpoints_fail_closed_without_the_face(
    ui_server, http_post, auth, path: str
) -> None:
    """写面同样 fail-closed——缺面时**不执行任何动作**，只回 `unavailable` + 点名。"""
    payload = http_post(ui_server, path, {}, headers=auth(ui_server)).json()
    assert payload["status"] == "unavailable"
    assert "未接入" in payload["reason"]


@dataclass(frozen=True)
class _StubEntry:
    config_id: str
    display_name: str = "某项"
    value_schema: dict = None  # type: ignore[assignment]
    default: object = True

    @property
    def panel_form_spec(self):
        @dataclass(frozen=True)
        class _Spec:
            widget: str = "toggle"
            label: str = "某项"
            help_text: str = "说明"

        return _Spec()


class _StubRegistry:
    def __init__(self, entries):
        self._entries = tuple(entries)

    def list(self):
        return self._entries

    def entry(self, config_id):
        return next((e for e in self._entries if e.config_id == config_id), None)

    def set(self, config_id, value, *, trace_id=None):  # pragma: no cover - 未被本组调用
        return None


def test_face_present_but_missing_a_handle_names_the_handle() -> None:
    """面在但缺句柄 ⇒ `unavailable` **点名缺哪一支**——不半途抛成一条假的 failed。"""

    class _Partial:  # 只有 store，没有 config_registry
        store = None

    app = build_ui(host="127.0.0.1", port=0, settings=_Partial())
    payload = app.api_settings_sources()
    assert payload["panel"]["status"] == "unavailable"
    assert "config_registry" in payload["panel"]["reason"]


def test_face_with_the_registry_but_no_entries_is_an_honest_empty_panel() -> None:
    """条目为空是**内容**问题，不是「面缺席」——面板照常出，只是没有可调项。"""

    class _OnlyRegistry:
        config_registry = _StubRegistry(())

    app = build_ui(host="127.0.0.1", port=0, settings=_OnlyRegistry())
    payload = app.api_settings_sources()
    assert payload["panel"]["status"] == "ok"
    assert payload["panel"]["data"]["slots"]["entries"] == []


def test_boolean_and_number_form_values_are_restored_to_declared_types() -> None:
    """`<select>` 回传的字符串按 `value_schema.type` 还原；还原不了即抛（不猜测）。"""
    boolean = _StubEntry(config_id="x.flag", value_schema={"type": "boolean"})
    assert _coerce_value(boolean, "false") is False
    assert _coerce_value(boolean, "true") is True
    assert _coerce_value(boolean, True) is True
    with pytest.raises(ValueError):
        _coerce_value(boolean, "maybe")

    number = _StubEntry(config_id="x.days", value_schema={"type": "number"}, default=30)
    assert _coerce_value(number, "30") == 30
    assert _coerce_value(number, 30) == 30
    with pytest.raises(ValueError):
        _coerce_value(number, "三十")
    with pytest.raises(ValueError):
        _coerce_value(number, True)

    integer = _StubEntry(config_id="x.steps", value_schema={"type": "integer"}, default=8)
    assert _coerce_value(integer, "8") == 8
    assert _coerce_value(integer, 8) == 8
    with pytest.raises(ValueError):
        _coerce_value(integer, "八")
    with pytest.raises(ValueError):
        _coerce_value(integer, True)

    free = _StubEntry(config_id="x.host", value_schema={"type": "string"}, default="a")
    assert _coerce_value(free, "b") == "b"


def test_backup_destination_containment_check() -> None:
    """归档落点判定要**解析路径**（`..` 与符号链接都算），否则「根内」会被绕过。"""
    root = Path("C:/data/store")
    assert _is_inside(root / "inner.stbackup", root) is True
    assert _is_inside(root, root) is True
    assert _is_inside(root.parent / "elsewhere.stbackup", root) is False
    assert _is_inside(root / ".." / "escape.stbackup", root) is False


def test_window_hours_are_validated_not_clamped() -> None:
    """窗口为非正整数或超上限即拒——**不静默夹取**（夹取会让用户以为筛的是自己填的区间）。"""
    assert _window_start("") is not None
    assert _window_start("48") is not None
    for bad in ("abc", "0", "-3", "999999"):
        with pytest.raises(ValueError):
            _window_start(bad)


def test_bytes_text_uses_fixed_binary_steps() -> None:
    assert _bytes_text(0) == "0 B"
    assert _bytes_text(1023) == "1023 B"
    assert _bytes_text(1536) == "1.5 KiB"
    assert _bytes_text(3 * 1024 * 1024) == "3.0 MiB"


# ── 运行期授权页（[T-AGT-012.2]） ──────────────────────────────────────────────

class _AgentAuthFace:
    """运行期授权面的替身（形状同 `ReflectionFacade.agent_authorization`）。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def agent_authorization(self) -> dict:
        return {
            "available": True,
            "tier": "autonomous",
            "tiers": ["manual", "collaborative", "autonomous"],
            "default_tier": "autonomous",
            "tier_source": "default",
            "tier_config_id": "agent.authorization",
            "grading_config_id": "evolution.risk-grading",
            "grading_note": "与演进授权共用同一张清单；本页只呈现",
            "grading": [
                {"prefix": "skill.sk_local_sql", "risk_class": "autonomous-ok"},
            ],
            "risk_classes": [
                "autonomous-ok", "collaborative-required", "never-autonomous",
            ],
            "changes": [],
        }

    def rollback_agent_change(self, change_id: str) -> dict:
        self.calls.append(change_id)
        return {"available": True, "applied": True, "note": "已按变更前取值回放"}


def test_agent_authorization_panel_shows_default_source_and_read_only_grading() -> None:
    app = build_ui(host="127.0.0.1", port=0, reflection=_AgentAuthFace())
    payload = app.api_settings_agent_authorization()
    assert payload["panel"]["status"] == "ok"
    slots = payload["panel"]["data"]["slots"]
    tier, grading = slots["entries"]
    assert tier["config_id"] == "agent.authorization"
    assert tier["current"] == "autonomous" and tier["actions"] == ["set"]
    assert "当前来源" in tier["description"] and "出厂缺省" in tier["description"]
    assert "出厂缺省" in tier["note"] and "尚未由用户修改" in tier["note"]
    assert grading["config_id"] == "evolution.risk-grading"
    assert grading["actions"] == [] and grading["detail"]
    assert "只呈现" in grading["note"]
    assert payload["trail"]["status"] == "ok"


def test_agent_authorization_is_unavailable_without_the_reflection_face() -> None:
    app = build_ui(host="127.0.0.1", port=0)
    payload = app.api_settings_agent_authorization()
    for part in payload.values():
        assert part["status"] == "unavailable"
        assert "未接入" in part["reason"]


class _BudgetSettings:
    """只有配置注册表的最小设置面——预算段**不依赖反思面**（T-UI-036 GWT-4）。"""

    config_registry = _StubRegistry((
        _StubEntry(
            config_id="investigate.context_budget", display_name="上下文预算",
            value_schema={"type": "integer", "minimum": 1, "maximum": 32000},
            default=16000,
        ),
        _StubEntry(
            config_id="investigate.max_steps", display_name="步数上界",
            value_schema={"type": "integer", "minimum": 1, "maximum": 32},
            default=8,
        ),
        _StubEntry(
            config_id="investigate.max_llm_calls", display_name="调用次数上界",
            value_schema={"type": "integer", "minimum": 1, "maximum": 64},
            default=12,
        ),
    ))


def test_agent_authorization_exposes_budget_without_the_reflection_face() -> None:
    """预算段独立于反思面：档位 / 留痕不可用时，三条预算仍可调且顺序固定。"""
    app = build_ui(host="127.0.0.1", port=0, settings=_BudgetSettings())
    payload = app.api_settings_agent_authorization()
    assert payload["panel"]["status"] == "unavailable"
    assert payload["trail"]["status"] == "unavailable"
    budget = payload["budget"]
    assert budget["status"] == "ok", budget.get("reason")
    slots = budget["data"]["slots"]
    assert slots["surface"] == "settings-config"
    entries = slots["entries"]
    assert [item["config_id"] for item in entries] == [
        "investigate.max_steps", "investigate.max_llm_calls", "investigate.context_budget",
    ]
    assert [item["current"] for item in entries] == [8, 12, 16000]
    assert all(item["actions"] == ["set"] and item["input"] == "text" for item in entries)
    assert "1–32" in entries[0]["note"]


class _ToolEntry:
    def __init__(self, name: str) -> None:
        self.name = name
        self.skill_id = f"{name}_v1.0"
        self.description = f"{name} 的说明"


class _SkillsStub:
    def __init__(self, names: tuple[str, ...]) -> None:
        self._entries = tuple(_ToolEntry(name) for name in names)

    def tool_catalog(self):
        return self._entries


class _ToolSettings(_BudgetSettings):
    """预算三条目 + 工具目录 + 真 Store（工具开关落盘用）。"""

    def __init__(self, store, names: tuple[str, ...]) -> None:
        self.store = store
        self.skills = _SkillsStub(names)


def test_agent_page_lists_tools_grouped_and_enabled(tmp_path: Path) -> None:
    store = Store.create(tmp_path / "root", "correct horse battery staple")
    settings = _ToolSettings(
        store, ("sk_plan_update", "sk_web_search", "sk_custom_thing"),
    )
    app = build_ui(host="127.0.0.1", port=0, settings=settings, reflection=_AgentAuthFace())
    payload = app.api_settings_agent_authorization()
    tools = payload["tools"]
    assert tools["status"] == "ok", tools.get("reason")
    values = tools["data"]["slots"]["values"]
    groups = {group["key"]: group for group in values["groups"]}
    assert groups["control"]["tools"][0]["name"] == "sk_plan_update"
    assert groups["web"]["tools"][0]["name"] == "sk_web_search"
    assert groups["other"]["tools"][0]["name"] == "sk_custom_thing"
    assert values["total"] == 3 and values["enabled"] == 3
    assert groups["control"]["tools"][0]["config_id"] == "agent-tools/sk_plan_update"


def test_agent_page_reflects_disabled_tools(tmp_path: Path) -> None:
    store = Store.create(tmp_path / "root", "correct horse battery staple")
    set_tool_enabled(store, "sk_web_search", False)
    settings = _ToolSettings(store, ("sk_web_search", "sk_plan_update"))
    app = build_ui(host="127.0.0.1", port=0, settings=settings, reflection=_AgentAuthFace())
    values = app.api_settings_agent_authorization()["tools"]["data"]["slots"]["values"]
    groups = {group["key"]: group for group in values["groups"]}
    assert groups["web"]["tools"][0]["enabled"] is False
    assert values["enabled"] == 1 and values["disabled"] == 1


def test_agent_page_reports_corrupt_tool_config(tmp_path: Path) -> None:
    store = Store.create(tmp_path / "root", "correct horse battery staple")
    store.put("config", TOOL_SELECTION_PATH, b"{not json")
    settings = _ToolSettings(store, ("sk_web_search",))
    app = build_ui(host="127.0.0.1", port=0, settings=settings, reflection=_AgentAuthFace())
    tools = app.api_settings_agent_authorization()["tools"]
    assert tools["status"] == "unavailable"
    assert "损坏" in tools["reason"]


def test_tool_toggles_are_rendered_as_collapsible_groups() -> None:
    """静态断言：工具分组沿用数据同步页的 details 折叠形态（T-UI-038）。"""
    path = (
        Path(__file__).resolve().parents[2]
        / "src" / "st_agent" / "ui" / "web" / "js" / "pages" / "settings.js"
    )
    text = path.read_text(encoding="utf-8")
    assert "function renderToolsSection" in text
    assert "openToolGroups" in text
    assert "details.open = openToolGroups.has(group.key);" in text
    assert "config_id: tool.config_id" in text


def test_agent_rollback_requires_a_change_id() -> None:
    app = build_ui(host="127.0.0.1", port=0, reflection=_AgentAuthFace())
    payload = app.api_settings_agent_rollback({})
    assert payload["status"] == "validation_failed"
    assert "change_id" in payload["reason"]


def test_agent_rollback_delegates_to_the_face() -> None:
    face = _AgentAuthFace()
    app = build_ui(host="127.0.0.1", port=0, reflection=face)
    payload = app.api_settings_agent_rollback({"change_id": "chg_agent"})
    assert payload["status"] == "ok" and payload["data"]["applied"] is True
    assert face.calls == ["chg_agent"]


# ── 同步目录的折叠态与点选行为（[T-UI-030]；静态断言，不跑 JS） ──────────────────

def test_sync_groups_start_collapsed_and_selection_does_not_rebuild_the_catalog() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "src" / "st_agent" / "ui" / "web" / "js" / "pages" / "settings.js"
    )
    text = path.read_text(encoding="utf-8")
    assert "openTargets" in text, "展开态需要按组记住，才能在重建之间保持"
    assert "details.open = openTargets.has(target.target_id);" in text
    assert "['all_a', 'st', 'other'].includes" not in text, "不得再写死默认展开的三组"

    start = text.rindex("check.addEventListener('change'")
    end = text.index("});", start)
    assert "renderCatalog" not in text[start:end], (
        "勾选只改状态并刷新预检——整块重建会让折叠态与滚动位置复位"
    )
    assert "refreshCatalog" in text, "批次结束应只刷新目录状态，不整页重绘"


def test_web_page_has_an_allow_all_domains_option() -> None:
    """T-UI-037.1：开放网络页提供「允许抓取全部域名」，写入名单 `*`。"""
    path = (
        Path(__file__).resolve().parents[2]
        / "src" / "st_agent" / "ui" / "web" / "js" / "pages" / "settings.js"
    )
    text = path.read_text(encoding="utf-8")
    assert "允许抓取全部域名" in text
    assert "value: check.checked ? '*' : ''" in text
    assert "web-egress/allowed-hosts" in text


def test_data_page_has_a_startup_auto_sync_checkbox() -> None:
    """T-UI-037.3：每个「标的 × 数据集」行提供启动自动同步勾选。"""
    path = (
        Path(__file__).resolve().parents[2]
        / "src" / "st_agent" / "ui" / "web" / "js" / "pages" / "settings.js"
    )
    text = path.read_text(encoding="utf-8")
    assert "启动应用时自动同步" in text
    assert "data-sync/auto." in text
    assert "values.auto_sync" in text
