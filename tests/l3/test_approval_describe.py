"""T-L3-006 · `permission_approval_card` 描述件与渲染面对齐。

三处锁：
- **描述形状**（必填槽齐、逐条 `items` + 按 `permission` 并联的 `labels`）
- **中性分栏**（措辞与对话文案是 `generated`，声明原文与批准态是 `data`）
- **两侧不漂移**（`ui/registry.py` 的必填槽与本模块一致；本模块自有的来源标签常量过 [01 §6]）
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from l3_helpers import NOW

from st_agent.contracts.neutrality import NeutralityGuard
from st_agent.contracts.permissions import describe_permission
from st_agent.l3.approval.panel import (
    ApprovalItem,
    CapabilityApprovalView,
    SOURCE_LABELS,
)
from st_agent.l3.render import describe_approval
from st_agent.ui.registry import spec_for

CST = timezone(timedelta(hours=8))
DECIDED = datetime(2026, 9, 29, 9, 0, tzinfo=CST)
CACHE_READ = "local_read:<data/cache/**>"
NET_BAOSTOCK = "net_access:<*.baostock.com>"


def _view(*, approved: bool = True) -> CapabilityApprovalView:
    """真措辞（`describe_permission`）+ 两种批准态（一项已批、一项待批）。"""
    return CapabilityApprovalView(
        key="sk_demo_eligibility",
        source="skill",
        source_label=SOURCE_LABELS["skill"],
        items=tuple(
            ApprovalItem(
                permission=decl,
                description=describe_permission(decl),
                state=("approved" if approved else "pending")
                if index == 0 else "pending",
                decided_at=DECIDED if (index == 0 and approved) else None,
                chat_text=(
                    f"{describe_permission(decl)}｜"
                    f"{('已批准' if approved else '待批准') if index == 0 else '待批准'}"
                ),
                panel_field={
                    "name": decl, "widget": "approval_toggle",
                    "options": ["approved", "rejected", "pending"],
                    "value": ("approved" if approved else "pending")
                    if index == 0 else "pending",
                },
            )
            for index, decl in enumerate((CACHE_READ, NET_BAOSTOCK))
        ),
    )


class TestApprovalDescriptionShape:
    """描述形状：必填槽齐、逐条 items + 按 `permission` 并联的 labels。"""

    def test_component_type_and_required_slots(self):
        description = describe_approval(_view(), now=NOW).data

        assert description.component_type == "permission_approval_card"
        assert {"items", "labels"} <= set(description.slots)

    def test_required_slots_match_server_side_spec(self):
        """两侧不漂移：本描述件的槽覆盖了 `ui/registry.py` 声明的必填槽。"""
        spec = spec_for("permission_approval_card")
        assert spec is not None and spec.implemented

        description = describe_approval(_view(), now=NOW).data
        assert set(spec.required_slots) <= set(description.slots)

    def test_items_carry_permission_state_and_panel_field(self):
        description = describe_approval(_view(), now=NOW).data
        items = description.slots["items"]

        # 逐条：两条各占一项，声明原文 + 状态 + 控件都在数据槽
        assert [i["permission"] for i in items] == [CACHE_READ, NET_BAOSTOCK]
        assert [i["state"] for i in items] == ["approved", "pending"]
        assert items[0]["panel_field"]["widget"] == "approval_toggle"

    def test_labels_parallel_items_by_permission(self):
        """中性措辞 / 对话文案在 `labels`（生成文案槽），按 `permission` 与 items 并联。"""
        description = describe_approval(_view(), now=NOW).data
        labels = description.slots["labels"]

        assert set(labels) == {CACHE_READ, NET_BAOSTOCK}
        assert labels[CACHE_READ]["chat_text"] == f"{describe_permission(CACHE_READ)}｜已批准"
        # decided_at 摊成可 JSON 化的字符串（§12 只承载已解析值）
        assert description.slots["items"][0]["decided_at"] == DECIDED.isoformat()
        assert description.slots["items"][1]["decided_at"] is None


class TestApprovalDescriptionNeutrality:
    """中性分栏：措辞是 `generated`（过 §6），声明原文与批准态是 `data`。"""

    def test_text_kinds_split_by_source(self):
        description = describe_approval(_view(), now=NOW).data
        kinds = description.text_kinds

        assert kinds["items"] == "data"  # 声明原文是注册期数据，不是本层文案
        assert kinds["labels"] == "generated"  # 措辞 / 状态标签是生成文案
        assert kinds["key"] == "data"

    def test_source_labels_are_neutral(self):
        """本模块自有固定文案（来源标签）逐条过 [01 §6] 执行点 2。"""
        guard = NeutralityGuard()
        for label in SOURCE_LABELS.values():
            assert guard.check_output(f"{label}权限申请").passed, label

    def test_title_has_no_recommendation(self):
        """抬头只说「这是谁的权限申请」，不含任何建议（铁律 4）。"""
        description = describe_approval(_view(), now=NOW).data

        assert description.title == "Skill权限申请"
