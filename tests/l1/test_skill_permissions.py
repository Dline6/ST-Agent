"""T-L1-009.1 测试：Skill 批准账本本体 + 权限语义的共用件。

GWT 对照（任务文件 5 条）：
- GWT-1 共用语义只有一份（契约层承载 + 既有导入路径 re-export）
- GWT-2 MCP 侧零变化（由既有 MCP 用例承载，本文件只作 identity 守卫）
- GWT-3 账本语义与 MCP 侧对齐
- GWT-4 批准随 base 跨版本存活
- GWT-5 落盘分区（Skill 与 MCP 互不串扫）
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from st_agent.contracts.permissions import (
    PERMISSION_KINDS,
    PermissionApproval,
    describe_permission,
)
from st_agent.l0.storage import Store
from st_agent.l1.mcp.permissions import McpPermissionBook
from st_agent.l1.permission_book import PermissionBook
from st_agent.l1.skills import (
    PERMISSION_PREFIX,
    SKILL_BASE_PATTERN,
    SKILL_ID_PATTERN,
    SkillPermissionBook,
    SkillPermissionError,
    SkillValidationError,
    base_of,
    skill_id_for,
)

PASS = "correct horse battery staple"
BASE = "sk_unhat_eligibility_check"
CACHE_SCOPE = "local_read:<data/cache/**>"
BAOSTOCK_NET = "net_access:<*.baostock.com>"
EXEC = "exec_command"


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def book(store: Store) -> SkillPermissionBook:
    return SkillPermissionBook(store)


def _persisted(store: Store, name: str) -> list:
    return json.loads(store.get("config", name).decode("utf-8"))


class TestSharedSemantics:
    """GWT-1：一个口径只住一份，既有导入路径仍可用。"""

    def test_both_books_share_one_bookkeeping_base(self):
        assert issubclass(SkillPermissionBook, PermissionBook)
        assert issubclass(McpPermissionBook, PermissionBook)

    def test_mcp_module_reexports_the_shared_wording(self):
        from st_agent.l1.mcp import permissions as mcp_permissions

        assert mcp_permissions.describe_permission is describe_permission
        assert mcp_permissions.PERMISSION_KINDS is PERMISSION_KINDS

    def test_mcp_models_reexport_the_approval_shape(self):
        from st_agent.l1.mcp import models as mcp_models

        assert mcp_models.PermissionApproval is PermissionApproval

    def test_three_permission_kinds_are_worded(self):
        assert set(PERMISSION_KINDS) == {"local_read", "net_access", "exec_command"}

    def test_base_pattern_mirrors_skill_id_group(self):
        """`SKILL_BASE_PATTERN` 与 `SKILL_ID_PATTERN` 第 1 组同源（不许各自漂移）。"""
        for base in (BASE, "sk_st_list_sync", "sk_mcp_srv_a_echo", "sk_x", "sk_a.b-c_d"):
            assert SKILL_BASE_PATTERN.match(base)
            assert SKILL_ID_PATTERN.match(skill_id_for(base, "1.0"))


class TestDeclare:
    def test_declare_starts_pending(self, book: SkillPermissionBook):
        approvals = book.declare(BASE, (CACHE_SCOPE, BAOSTOCK_NET))
        assert [a.decision for a in approvals] == ["pending", "pending"]
        assert book.pending_permissions(BASE) == (CACHE_SCOPE, BAOSTOCK_NET)
        assert book.approved_permissions(BASE) == ()

    def test_redeclare_keeps_existing_decisions(self, book: SkillPermissionBook):
        book.declare(BASE, (CACHE_SCOPE, BAOSTOCK_NET))
        book.approve(BASE, CACHE_SCOPE)
        book.declare(BASE, (CACHE_SCOPE, EXEC))
        assert book.approved_permissions(BASE) == (CACHE_SCOPE,)
        assert book.pending_permissions(BASE) == (EXEC,)

    def test_declare_unknown_key_is_empty(self, book: SkillPermissionBook):
        assert book.approvals(BASE) == ()
        assert book.approved_permissions(BASE) == ()


class TestDecide:
    def test_approve_and_reject(self, book: SkillPermissionBook):
        book.declare(BASE, (CACHE_SCOPE, BAOSTOCK_NET, EXEC))
        book.approve(BASE, CACHE_SCOPE)
        book.reject(BASE, BAOSTOCK_NET)
        assert book.approved_permissions(BASE) == (CACHE_SCOPE,)
        assert [a.decision for a in book.approvals(BASE)] == ["approved", "rejected", "pending"]

    def test_decided_carries_timestamp(self, book: SkillPermissionBook):
        book.declare(BASE, (CACHE_SCOPE,))
        decided = book.approve(BASE, CACHE_SCOPE)
        assert decided.decided_at is not None
        assert decided.decided_at.tzinfo is not None

    def test_undeclared_permission_cannot_be_decided(self, book: SkillPermissionBook):
        book.declare(BASE, (CACHE_SCOPE,))
        with pytest.raises(SkillPermissionError):
            book.approve(BASE, BAOSTOCK_NET)
        with pytest.raises(SkillPermissionError):
            book.reject(BASE, BAOSTOCK_NET)

    def test_pending_must_not_carry_timestamp(self):
        """形态校验由契约层的 `PermissionApproval` 承担（pydantic 包装 ValueError）。"""
        with pytest.raises(PydanticValidationError):
            PermissionApproval(
                permission=CACHE_SCOPE, decision="pending",
                decided_at=datetime.now().astimezone(),
            )

    def test_decided_must_carry_timestamp(self):
        with pytest.raises(PydanticValidationError):
            PermissionApproval(permission=CACHE_SCOPE, decision="approved")


class TestPersistence:
    def test_approval_survives_reload(self, store: Store, book: SkillPermissionBook):
        book.declare(BASE, (CACHE_SCOPE,))
        book.approve(BASE, CACHE_SCOPE)
        assert SkillPermissionBook(store).approved_permissions(BASE) == (CACHE_SCOPE,)

    def test_forget_is_idempotent(self, book: SkillPermissionBook):
        book.declare(BASE, (CACHE_SCOPE,))
        book.forget(BASE)
        book.forget(BASE)
        assert book.approvals(BASE) == ()


class TestBaseGranularity:
    """GWT-4：键为 base（能力身份），批准跨版本存活。"""

    def test_record_is_keyed_by_base_not_skill_id(self, store: Store, book: SkillPermissionBook):
        book.declare(BASE, (CACHE_SCOPE,))
        book.approve(BASE, CACHE_SCOPE)
        assert f"{PERMISSION_PREFIX}{BASE}.json" in store.list_files("config")
        # 版本号不进键
        assert f"{PERMISSION_PREFIX}{skill_id_for(BASE, '1.1')}.json" not in store.list_files("config")

    def test_next_version_reuses_the_same_approval(self, book: SkillPermissionBook):
        book.declare(BASE, (CACHE_SCOPE,))
        book.approve(BASE, CACHE_SCOPE)
        # 「取 sk_x_v1.1 的批准集」＝先剥版本得 base，故与 v1.0 同一条记录
        assert base_of(skill_id_for(BASE, "1.1")) == BASE
        assert book.approved_permissions(base_of(skill_id_for(BASE, "1.1"))) == (CACHE_SCOPE,)


class TestKeyShape:
    @pytest.mark.parametrize("bad", ["", "not_a_skill", "sk_", "../escape", "SK_UP", "sk_has space"])
    def test_bad_base_is_rejected(self, book: SkillPermissionBook, bad: str):
        with pytest.raises(SkillValidationError):
            book.declare(bad, (CACHE_SCOPE,))
        with pytest.raises(SkillValidationError):
            book.approvals(bad)


class TestPartition:
    """GWT-5：两个账本各走各的前缀，互不串扫。"""

    def test_skill_book_rejects_non_skill_keys(self, store: Store):
        with pytest.raises(SkillValidationError):
            SkillPermissionBook(store).approvals("srv_a")

    def test_separation_is_by_prefix_not_key_shape(self, store: Store):
        """隔离机制是**前缀**，不是键形态——MCP 的键空间更宽（``sk_...`` 也是合法 server_id）。"""
        assert McpPermissionBook(store).approvals(BASE) == ()  # 形态上被接受
        SkillPermissionBook(store).declare(BASE, (CACHE_SCOPE,))
        McpPermissionBook(store).declare(BASE, (BAOSTOCK_NET,))
        files = store.list_files("config")
        assert f"{PERMISSION_PREFIX}{BASE}.json" in files
        assert f"mcp-permission/{BASE}.json" in files
        # 同一字面量各自成一份记录，互不串扫
        assert SkillPermissionBook(store).approved_permissions(BASE) == ()
        assert McpPermissionBook(store).approved_permissions(BASE) == ()


class TestDescribe:
    def test_describe_explains_what_the_skill_wants(self, book: SkillPermissionBook):
        book.declare(BASE, (CACHE_SCOPE, BAOSTOCK_NET, EXEC))
        lines = book.describe(BASE)
        assert len(lines) == 3
        assert any("读取声明范围内的本地文件" in ln for ln in lines)
        assert any("访问声明的远程主机" in ln for ln in lines)
        assert any("最高风险" in ln for ln in lines)
        assert all("待批准" in ln for ln in lines)

    def test_describe_reflects_decision(self, book: SkillPermissionBook):
        book.declare(BASE, (CACHE_SCOPE,))
        book.approve(BASE, CACHE_SCOPE)
        assert book.describe(BASE)[0].endswith("已批准")


class TestCorruptRecord:
    def test_malformed_json_is_explicit(self, store: Store, book: SkillPermissionBook):
        store.put("config", f"{PERMISSION_PREFIX}{BASE}.json", b"{not json")
        with pytest.raises(SkillValidationError):
            book.approvals(BASE)

    def test_non_list_payload_is_explicit(self, store: Store, book: SkillPermissionBook):
        store.put("config", f"{PERMISSION_PREFIX}{BASE}.json", b'{"a": 1}')
        with pytest.raises(SkillValidationError):
            book.approvals(BASE)

    def test_bad_entry_is_explicit(self, store: Store, book: SkillPermissionBook):
        store.put(
            "config", f"{PERMISSION_PREFIX}{BASE}.json",
            json.dumps([{"permission": CACHE_SCOPE, "decision": "maybe"}]).encode("utf-8"),
        )
        with pytest.raises(SkillValidationError):
            book.approvals(BASE)


def test_prefix_is_a_distinct_directory():
    assert PERMISSION_PREFIX.endswith("/")
    assert re.match(r"^[a-z-]+/$", PERMISSION_PREFIX)
    assert PERMISSION_PREFIX != "mcp-permission/"
