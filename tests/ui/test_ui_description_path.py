"""出站路径的端到端：描述 → 注册表必填槽 → 中性化门 → 信封（[01 §12]）。

走真 HTTP（dev 示例端点），因为这三件事的顺序与「阻断时不回可渲染描述」正是要在
**真实出站路径**上成立的——单测各自成立、拼起来顺序错了，是这类面最容易出的问题。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from st_agent.contracts.ui_description import UiDescription, new_description_id
from st_agent.ui.app import build_ui


def _fetch(http_get, auth, running, kind):
    return http_get(running, f"/api/dev/description?kind={kind}", headers=auth(running))


def test_clean_description_is_ok_and_carries_the_description(ui_server_dev, http_get, auth) -> None:
    response = _fetch(http_get, auth, ui_server_dev, "report_card")
    assert response.status == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["render"]["presentation"] == "normal"
    assert payload["data"]["component_type"] == "report_card"
    assert payload["data"]["description_id"].startswith("desc_")


def test_table_sample_splits_generated_columns_from_data_rows(ui_server_dev, http_get, auth) -> None:
    payload = _fetch(http_get, auth, ui_server_dev, "table").json()
    assert payload["status"] == "ok"
    assert payload["data"]["text_kinds"] == {"columns": "generated", "rows": "data"}


def test_reserved_type_passes_the_server_and_is_left_to_the_renderer(
    ui_server_dev, http_get, auth
) -> None:
    """已登记未实现型：服务端不该假装懂它，也不该判它非法——降级是**渲染面**的事。"""
    payload = _fetch(http_get, auth, ui_server_dev, "reserved").json()
    assert payload["status"] == "ok"
    assert payload["data"]["component_type"] == "pinned_board"


def test_generated_violation_is_blocked_before_it_leaves(ui_server_dev, http_get, auth) -> None:
    """§6 执行点 2：阻断渲染——回 `validation_failed`，**不带描述载荷**。"""
    payload = _fetch(http_get, auth, ui_server_dev, "generated-violation").json()
    assert payload["status"] == "validation_failed"
    assert payload["data"] is None
    assert payload["render"]["presentation"] == "input_error"
    assert "我" not in payload["reason"]  # 违规原文不随信封出网


def test_data_violation_passes(ui_server_dev, http_get, auth) -> None:
    """同一措辞在 `data` 槽 → 放行（[D-053]）。"""
    payload = _fetch(http_get, auth, ui_server_dev, "data-violation").json()
    assert payload["status"] == "ok"


def test_unknown_description_kind_is_404(ui_server_dev, http_get, auth) -> None:
    assert _fetch(http_get, auth, ui_server_dev, "nope").status == 404


def test_description_endpoint_absent_when_dev_is_off(ui_server, http_get, auth) -> None:
    assert _fetch(http_get, auth, ui_server, "report_card").status == 404


def test_missing_required_slot_is_blocked_before_the_renderer() -> None:
    """缺必填槽在**服务端**就拦住——不把一张残缺的卡送到浏览器，让前端去猜。"""
    app = build_ui(host="127.0.0.1", port=1)
    payload = app.api_description(
        UiDescription(
            description_id=new_description_id(),
            component_type="table",
            slots={"columns": []},
            text_kinds={"columns": "generated"},
        )
    )
    assert payload["status"] == "validation_failed"
    assert "rows" in payload["reason"]


@pytest.mark.parametrize(
    "kind,component_type",
    [
        ("trace_timeline", "trace_timeline"),
        ("context_card", "context_card"),
        ("config_draft_card", "config_draft_card"),
        ("config_draft_panel", "config_draft_card"),
        ("conflict_adjudication_card", "conflict_adjudication_card"),
        ("divergence_map", "divergence_map"),
        ("divergence_map_unanimous", "divergence_map"),
    ],
)
def test_l3_sample_descriptions_leave_the_gate_intact(
    ui_server_dev, http_get, auth, kind, component_type
) -> None:
    """L3 六型的走查样本走**同一条**出站校验路径（真描述件产物，非手抄）。"""
    payload = _fetch(http_get, auth, ui_server_dev, kind).json()
    assert payload["status"] == "ok", payload.get("reason")
    assert payload["data"]["component_type"] == component_type


def test_context_card_without_labels_is_blocked() -> None:
    """`labels`（段名 / 原因）是本批收紧后的必填槽——缺它渲染件出不了完整卡片（D-064）。"""
    app = build_ui(host="127.0.0.1", port=1)
    payload = app.api_description(
        UiDescription(
            description_id=new_description_id(),
            component_type="context_card",
            slots={"sections": []},
            text_kinds={"sections": "data"},
        )
    )
    assert payload["status"] == "validation_failed"
    assert "labels" in payload["reason"]


def test_dev_panel_lists_every_sample_kind() -> None:
    """dev 面板的按钮清单与样本清单**不漂移**——否则走查路径会静默少一条。"""
    from st_agent.ui.dev import samples

    panel = (
        Path(__file__).resolve().parents[2] / "src" / "st_agent" / "ui" / "dev" / "web" / "devpanel.js"
    ).read_text(encoding="utf-8")
    block = panel.split("const DESCRIPTIONS = [", 1)[1].split("];", 1)[0]
    listed = re.findall(r"'([\w-]+)'", block)
    assert listed == list(samples.DESCRIPTION_KINDS)
