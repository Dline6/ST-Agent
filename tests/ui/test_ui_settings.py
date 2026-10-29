"""设置区端点：**面缺席即 fail-closed**、输入域校验与纯函数口径（[`T-UI-018`]）。

与 `tests/integration/test_ui_settings_wiring.py`（真组合根）分工：本组只钉「没接线时会怎样」
与「表单值怎么还原成声明类型」——两者都不需要真 L0。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from st_agent.ui.app import build_ui
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
