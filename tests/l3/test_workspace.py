"""T-UI-013 · 工作区（钉住的持久组件）的 L3 侧：落盘与重解析（[05 §6]；[01 §5] / [§12]）。

本组钉 L3 侧的四件事（HTTP 面与页面渲染由 `tests/ui/test_ui_workspace.py` 钉，
真组合根上的装配由 `tests/integration/test_ui_workspace_wiring.py` 钉）：

1. **落盘**——钉住物进 L0 **既有** `config` 分区、一行一文件，`description_id` 即文件名
   （[01 §1]：该 id 是「『钉』到工作区后的**寻址单位**」）；
2. **六态不混用**——空工作区是 `empty` ＋ 原因（不是 `ok` 空数组）、无来源可重解析是
   `unavailable` ＋ 原因、来源给出的型不符是 `dependency_failed`——三种都不是「静默留空」；
3. **重解析的寻址不变**——刷新**沿用**原 `description_id`，只换 `slots` / `as_of`
   （否则「刷新」会变成「又钉了一件」）；
4. **来源面键是枚举不是 URL**（[01 §12] 的「动作不进描述」）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.ui_description import checked_description, new_description_id
from st_agent.l3.errors import WorkspacePinNotFoundError, WorkspaceValidationError
from st_agent.l3.workspace import (
    PIN_PARTITION,
    PIN_SOURCE_KEYS,
    SOURCE_BY_COMPONENT_TYPE,
    WorkspaceFacade,
    WorkspacePin,
    WorkspacePinStore,
)

NOW = datetime(2026, 10, 11, 9, 0, tzinfo=timezone(timedelta(hours=8)))
LATER = NOW + timedelta(hours=1)


def _context_card(description_id: str | None = None, *, title: str = "当前画像摘要"):
    """一份 `context_card` 描述（本批**唯一**有只读重解析来源的型）。"""
    return checked_description(
        description_id=description_id or new_description_id(),
        component_type="context_card",
        title=title,
        slots={
            "sections": [{"key": "risk", "value": "稳健"}],
            "labels": {"risk": "风险偏好"},
        },
        text_kinds={"sections": "data", "labels": "generated"},
        as_of=NOW,
    )


def _table(description_id: str | None = None):
    """一份 `table` 描述（没有只读重解析来源的型；也用作「来源给错型」的载荷）。"""
    return checked_description(
        description_id=description_id or new_description_id(),
        component_type="table",
        title="持仓",
        slots={"columns": [{"key": "code", "label": "代码"}], "rows": [{"code": "sh.600000"}]},
        text_kinds={"columns": "generated", "rows": "data"},
        as_of=NOW,
    )


@pytest.fixture
def pins(store) -> WorkspacePinStore:
    return WorkspacePinStore(store)


def _pin(pins: WorkspacePinStore, description, *, source_key=None, pinned_at=NOW) -> WorkspacePin:
    """直接经 store 落一条钉住物（绕开门面，专测落盘 / 寻址那一层）。"""
    return pins.save(
        WorkspacePin(description=description, source_key=source_key, pinned_at=pinned_at)
    )


# ── 落盘与寻址 ──────────────────────────────────────────────────────────────


def test_pin_lands_in_the_config_partition_under_the_description_id(pins, store) -> None:
    """`description_id` 同时是文件名与寻址单位（[01 §1]）——这是「钉住后怎么找回它」。"""
    description = _context_card()
    _pin(pins, description)

    assert store.list_files(PIN_PARTITION) == (
        f"workspace/pins/{description.description_id}.json",
    )


def test_pins_are_newest_first(pins) -> None:
    older = _pin(pins, _context_card(), pinned_at=NOW)
    newer = _pin(pins, _table(), pinned_at=LATER)

    assert [pin.description.description_id for pin in pins.pins()] == [
        newer.description.description_id,
        older.description.description_id,
    ]


def test_get_returns_the_stored_pin(pins) -> None:
    stored = _pin(pins, _context_card(), source_key="chat.context")
    assert pins.get(stored.description.description_id) == stored


def test_get_unknown_id_raises(pins) -> None:
    with pytest.raises(WorkspacePinNotFoundError):
        pins.get("desc_" + "0" * 20)


def test_unpin_removes_the_record(pins, store) -> None:
    stored = _pin(pins, _context_card(), source_key=None)
    pins.unpin(stored.description.description_id)
    assert store.list_files(PIN_PARTITION) == ()


def test_unpin_unknown_id_raises(pins) -> None:
    with pytest.raises(WorkspacePinNotFoundError):
        pins.unpin("desc_" + "0" * 20)


# ── 六态（空 / 不可用 / 依赖失败，互不冒充） ────────────────────────────────


def test_empty_workspace_is_empty_with_a_reason(pins) -> None:
    envelope = WorkspaceFacade(pins).pins()
    assert envelope.status == "empty"
    assert envelope.reason


def test_pins_returns_the_description_sequence(pins) -> None:
    first = _pin(pins, _context_card(), source_key="chat.context").description
    second = _pin(pins, _table(), source_key=None).description

    envelope = WorkspaceFacade(pins).pins()

    assert envelope.status == "ok"
    assert len(envelope.data) == 2
    assert {item.description_id for item in envelope.data} == {
        first.description_id,
        second.description_id,
    }


def test_missing_description_is_rejected(pins) -> None:
    with pytest.raises(WorkspaceValidationError):
        WorkspaceFacade(pins).pin({})


def test_malformed_description_is_rejected(pins) -> None:
    with pytest.raises(WorkspaceValidationError):
        WorkspaceFacade(pins).pin({"description": {"component_type": "nope"}})


def test_unknown_source_key_is_rejected(pins) -> None:
    with pytest.raises(WorkspaceValidationError):
        WorkspaceFacade(pins).pin({"description": _table().model_dump(mode="json"),
                                  "source": "https://example.invalid/api"})


# ── 重解析 ──────────────────────────────────────────────────────────────────


def test_context_card_gets_the_only_registered_default_source(pins) -> None:
    assert SOURCE_BY_COMPONENT_TYPE["context_card"] in PIN_SOURCE_KEYS


def test_pin_records_no_source_for_a_type_without_a_read_face(pins) -> None:
    envelope = WorkspaceFacade(pins).pin({"description": _table().model_dump(mode="json")})
    assert envelope.status == "ok"
    assert pins.get(envelope.data.description_id).source_key is None


def test_refresh_without_a_source_key_is_unavailable(pins) -> None:
    """没有来源面键 ⇒ 明文说「没有可重取的来源」，不是静默原样返回。"""
    envelope = WorkspaceFacade(pins).pin({"description": _table().model_dump(mode="json")})
    refreshed = WorkspaceFacade(pins).refresh({"description_id": envelope.data.description_id})
    assert refreshed.status == "unavailable"
    assert refreshed.reason


def test_refresh_with_an_unwired_source_is_unavailable(pins) -> None:
    """来源面键在枚举里、但本进程没接取数口——如实说「未接」，不拿旧数据充新。"""
    envelope = WorkspaceFacade(pins).pin({"description": _context_card().model_dump(mode="json")})
    refreshed = WorkspaceFacade(pins).refresh({"description_id": envelope.data.description_id})
    assert refreshed.status == "unavailable"
    assert "chat.context" in refreshed.reason


def test_refresh_keeps_the_description_id_and_swaps_the_binding(pins) -> None:
    """重解析出的是**新铸** id 的描述，刷新必须把它换成**原 id**——否则就成了「又钉一件」。"""
    pinned = _context_card(title="旧标题")
    WorkspaceFacade(pins).pin({"description": pinned.model_dump(mode="json")})
    fresh = _context_card(title="新标题")
    assert fresh.description_id != pinned.description_id
    facade = WorkspaceFacade(pins, resolvers={"chat.context": lambda: ResultEnvelope.ok(fresh)})

    refreshed = facade.refresh({"description_id": pinned.description_id})

    assert refreshed.status == "ok"
    assert refreshed.data.description_id == pinned.description_id
    assert refreshed.data.title == "新标题"
    # 落盘的也是新绑定——「记录数据绑定」的那一半被刷新了，不是只改了响应
    assert pins.get(pinned.description_id).description.title == "新标题"


def test_refresh_when_the_source_is_not_ok_is_unavailable(pins) -> None:
    pinned = _context_card()
    WorkspaceFacade(pins).pin({"description": pinned.model_dump(mode="json")})
    facade = WorkspaceFacade(
        pins,
        resolvers={
            "chat.context": lambda: ResultEnvelope.unavailable("上游不可用", last_updated_at=NOW)
        },
    )
    refreshed = facade.refresh({"description_id": pinned.description_id})
    assert refreshed.status == "unavailable"
    assert "上游不可用" in refreshed.reason


def test_refresh_when_the_source_gives_another_type_is_dependency_failed(pins) -> None:
    """装配错误要**点名两边的型**——不静默当成刷新成功。"""
    pinned = _context_card()
    WorkspaceFacade(pins).pin({"description": pinned.model_dump(mode="json")})
    facade = WorkspaceFacade(pins, resolvers={"chat.context": lambda: ResultEnvelope.ok(_table())})

    refreshed = facade.refresh({"description_id": pinned.description_id})

    assert refreshed.status == "dependency_failed"
    assert "context_card" in refreshed.reason
    assert "table" in refreshed.reason


def test_refresh_unknown_id_raises(pins) -> None:
    with pytest.raises(WorkspacePinNotFoundError):
        WorkspaceFacade(pins).refresh({"description_id": "desc_" + "0" * 20})


def test_unpin_through_the_facade_reports_the_id(pins) -> None:
    envelope = WorkspaceFacade(pins).pin({"description": _context_card().model_dump(mode="json")})
    body = {"description_id": envelope.data.description_id}
    assert WorkspaceFacade(pins).unpin(body).status == "ok"
    assert WorkspaceFacade(pins).pins().status == "empty"
