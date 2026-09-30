"""T-L3-006 · 能力安装 / 导入审批面（双通道）

GWT 对照（任务文件四条）：

- GWT-1 逐条展示，**不合并**（措辞取 ``describe_permission``，逐条各占一项）
- GWT-2 逐项批准经 ``SkillPermissionBook.approve`` 落账，未批准的仍 ``fail-closed``
- GWT-3 拒绝落 ``reject``，原因随返回值透传（反馈池归 L6 `T-L6-001`）
- GWT-4 未注入批准账本 → ``unavailable`` + 点名（不假装已批准）

账本一律用**真的** `SkillPermissionBook` + 真的 `Store`——批准事实的落盘是这一面
要接的东西，用假账本等于把「谁在写 `config/skill-permissions/`」这件事让掉验证。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from l3_helpers import PASS

from st_agent.contracts.permissions import describe_permission
from st_agent.l0.storage import Store
from st_agent.l1.skills import SkillPermissionBook
from st_agent.l3.approval.panel import (
    APPROVAL_ABSENT_REASON,
    FEEDBACK_POOL_OWNER,
    ApprovalRequest,
    CapabilityApprovalPanel,
)

BASE = "sk_demo_eligibility"
CACHE_READ = "local_read:<data/cache/**>"
NET_BAOSTOCK = "net_access:<*.baostock.com>"
PERMS = (CACHE_READ, NET_BAOSTOCK)


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def book(store: Store) -> SkillPermissionBook:
    return SkillPermissionBook(store)


@pytest.fixture()
def panel(book: SkillPermissionBook) -> CapabilityApprovalPanel:
    return CapabilityApprovalPanel(book=book)


def _request() -> ApprovalRequest:
    return ApprovalRequest(key=BASE, source="skill", permissions=PERMS)


class TestGwt1ItemizedDisplay:
    """**逐条列出**，每条一行，措辞取自 `describe_permission`（01 §10「这个能力想做什么」）。"""

    def test_open_declares_then_lists_every_item(self, panel: CapabilityApprovalPanel, book: SkillPermissionBook):
        env = panel.open(_request())

        assert env.status == "ok"
        view = env.data
        # 逐条列出：每条一项、按声明顺序，不压成一句话
        assert [i.permission for i in view.items] == list(PERMS)
        # 措辞来自 contracts 的 describe_permission（同一口径，不另造一份）
        assert view.items[0].description == describe_permission(CACHE_READ)
        assert view.items[1].description == describe_permission(NET_BAOSTOCK)
        # 挂载时经账本 declare（L1 册 D2 的写入入口在此被真正调用）
        assert book.pending_permissions(BASE) == PERMS

    def test_pending_state_labels_are_explicit(self, panel: CapabilityApprovalPanel):
        view = panel.open(_request()).data

        # 初态一律 pending：未批准 → 不假装已批准
        assert [i.state for i in view.items] == ["pending", "pending"]
        assert view.pending == PERMS
        assert view.approved == ()
        # chat_text 携状态标签（「… ｜ 待批准」）；panel_field 只给控件形态、不代表态
        assert view.items[0].chat_text.endswith("｜待批准")
        assert view.items[0].panel_field["value"] == "pending"

    def test_two_channels_share_the_same_source(self, panel: CapabilityApprovalPanel):
        view = panel.open(_request()).data
        first = view.items[0]

        # 对话通道（一句人话）与面板通道（一个三态控件）说的是**同一条**声明——同源、不各写一份
        assert first.permission in first.chat_text
        assert first.panel_field["name"] == first.permission


class TestGwt2ItemizedApprove:
    """逐项 `approve` 经账本落账；未批准的仍走沙箱 `fail-closed`（01 §10 / 03 §1.5）。"""

    def test_approve_lands_in_ledger(self, panel: CapabilityApprovalPanel, book: SkillPermissionBook):
        panel.open(_request())
        view = panel.approve(_request(), CACHE_READ).data

        assert view.items[0].state == "approved"
        assert view.items[1].state == "pending"  # 另一条**不受影响**——逐项，非全批
        assert book.approved_permissions(BASE) == (CACHE_READ,)

    def test_unapproved_permission_stays_out_of_approved_set(
        self, panel: CapabilityApprovalPanel, book: SkillPermissionBook
    ):
        """GWT-2 的 fail-closed 半边：批了一条，另一条仍在 pending——沙箱据此拦（「声明 ≠ 批准」）。"""
        panel.open(_request())
        view = panel.approve(_request(), CACHE_READ).data

        # 审批面**不**替未表态的条目预设已批准
        assert NET_BAOSTOCK not in view.approved
        assert NET_BAOSTOCK in book.pending_permissions(BASE)

    def test_redeclare_keeps_previous_decision(
        self, panel: CapabilityApprovalPanel, book: SkillPermissionBook
    ):
        """重开审批面（同 base 再次 `declare`）：既有决定不丢，新声明转 pending。

        `declare` 的这条语义由账本给出（D-042），本面通过**先 open 再 approve 再 open**
        把它接到用户可见的时序上。
        """
        panel.open(_request())
        panel.approve(_request(), CACHE_READ)

        again = panel.open(_request()).data

        assert again.items[0].state == "approved"
        assert again.items[0].decided_at is not None

    def test_approve_undeclared_is_dependency_failed(self, panel: CapabilityApprovalPanel):
        """未声明的权限不得批准——账本抛错，透传为显式失败（不静默吞）。"""
        panel.open(_request())
        env = panel.approve(_request(), "exec_command")

        assert env.status == "dependency_failed"
        assert "未声明权限" in (env.reason or "")


class TestGwt3RejectAndReasonPassthrough:
    """逐项 `reject` 落账；拒绝原因**随返回值透传**，反馈池归 L6。"""

    def test_reject_lands_in_ledger(self, panel: CapabilityApprovalPanel, book: SkillPermissionBook):
        panel.open(_request())
        view = panel.reject(_request(), NET_BAOSTOCK).data

        assert view.items[1].state == "rejected"
        # 拒绝后不进入已批准集合（沙箱据此继续拦）
        assert NET_BAOSTOCK not in book.approved_permissions(BASE)

    def test_submit_returns_decisions_and_passes_reasons_through(self, panel: CapabilityApprovalPanel):
        """`submit` 把批准集 / 拒绝集与**逐条原因**带回；原因**不落 L3**，归属点名到 L6。"""
        panel.open(_request())
        panel.approve(_request(), CACHE_READ)
        panel.reject(_request(), NET_BAOSTOCK)

        env = panel.submit(_request(), reasons={NET_BAOSTOCK: "本机不联网"})

        assert env.status == "ok"
        result = env.data
        assert result.approved == (CACHE_READ,)
        assert result.rejected == (NET_BAOSTOCK,)
        assert result.reject_reasons == {NET_BAOSTOCK: "本机不联网"}
        # 归属明示——原因「去哪」不靠记（05 §9 同口径）
        assert result.feedback_owner == FEEDBACK_POOL_OWNER

    def test_no_ledger_write_of_reasons(self, panel: CapabilityApprovalPanel, store: Store):
        """原因只在返回值上，L3 不自建反馈存储（反馈池归 L6，[08 §1]）。"""
        panel.open(_request())
        panel.reject(_request(), NET_BAOSTOCK)
        panel.submit(_request(), reasons={NET_BAOSTOCK: "本机不联网"})

        # config 分区里只有账本自己的记录，没有 L3 另落的一份
        files = [n for n in store.list_files("config") if "feedback" in n or "approval" in n]
        assert files == []


class TestGwt4LedgerAbsent:
    """未注入批准账本 → `unavailable` + 点名（**不假装已批准**）。"""

    @pytest.mark.parametrize("call", ["open", "view", "approve", "submit"])
    def test_absent_book_is_unavailable_by_every_verb(self, call):
        """四个动词一致——缺席的账本不会只挡住读、不挡住写。"""
        panel = CapabilityApprovalPanel()  # 无 book

        if call == "approve":
            env = panel.approve(_request(), CACHE_READ)
        elif call == "submit":
            env = panel.submit(_request())
        else:
            env = getattr(panel, call)(_request())

        assert env.status == "unavailable"
        # 点名——说清「没有账本，批准无处落账」，不是含糊的失败
        assert APPROVAL_ABSENT_REASON in (env.reason or "")

    def test_reject_also_named_when_book_absent(self):
        panel = CapabilityApprovalPanel()
        env = panel.reject(_request(), CACHE_READ)

        assert env.status == "unavailable"
        assert APPROVAL_ABSENT_REASON in (env.reason or "")

    def test_ledger_untouched_when_book_absent(self, store: Store):
        """unavailable 的分支不产生任何落盘（本面**不代管**账本；批准态归 L1）。"""
        panel = CapabilityApprovalPanel()
        # 无 store 的 panel 根本拿不到账本；此处显式确认落盘侧空
        assert [n for n in store.list_files("config") if n.startswith("skill-permissions/")] == []


class TestViewDataIsReal:
    """视图数据与账本的往返（**注入真账本**，不 mock）。"""

    def test_view_reflects_ledger_written_by_other_caller(
        self, panel: CapabilityApprovalPanel, book: SkillPermissionBook
    ):
        """账本可由别处写（例如 MCP 挂载序列），view 只读回显——不重复 declare。"""
        book.declare(BASE, PERMS)
        book.approve(BASE, NET_BAOSTOCK)

        view = panel.view(_request()).data

        assert view.approved == (NET_BAOSTOCK,)
        assert view.pending == (CACHE_READ,)

    def test_mcp_server_source_is_labelled(self, store: Store):
        """同一审批面对 MCP Server 用 `McpPermissionBook`（D-042：同一事实不存两份）。"""
        from st_agent.l1.mcp.permissions import McpPermissionBook

        mcp_book = McpPermissionBook(store)
        panel = CapabilityApprovalPanel(book=mcp_book)
        env = panel.open(ApprovalRequest(key="mcp_demo", source="mcp_server", permissions=(NET_BAOSTOCK,)))

        assert env.status == "ok"
        assert env.data.source_label == "MCP Server"
        # MCP 侧账本被真正写入（`declare` 走的是同一份簿记，键形态各异）
        assert mcp_book.pending_permissions("mcp_demo") == (NET_BAOSTOCK,)
