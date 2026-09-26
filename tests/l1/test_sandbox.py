"""T-L1-001.3 测试：03 §1.5 执行沙箱越界拦截。

GWT 对照（任务文件 3 条）：
- GWT-S1 文件越界拦截：声明 local_read 限某目录，访问声明外路径 →
  拦截 + BehaviorViolation 事件留痕 + 不执行原操作
- GWT-S2 网络越界拦截：未声明 net_access 发起出网 → 拦截 + 事件 +
  不触碰网关 sender
- GWT-S3 命令审批：未获 exec_command 批准请求命令执行 → 拦截 +
  警示「这个 Skill 行为异常」+ 提供禁用选项
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from st_agent.contracts.neutrality import NeutralityGuard
from st_agent.l0.net import EgressGateway
from st_agent.l0.storage import Store
from st_agent.l1.sandbox import (
    DISABLED_PREFIX,
    VIOLATION_PREFIX,
    WARNING_TEXT,
    SandboxValidationError,
    SandboxViolationError,
    SkillSandbox,
    host_in_scope,
    normalize_path,
    path_in_scope,
)
from st_agent.l1.skills import SkillRegistry, ensure_official_pack

PASS = "correct horse battery staple"

CACHE_SCOPE = "local_read:<data/cache/**>"
BAOSTOCK_NET = "net_access:<*.baostock.com>"
EXEC = "exec_command"


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def registry(store: Store) -> SkillRegistry:
    reg = SkillRegistry(store)
    ensure_official_pack(reg)
    return reg


@pytest.fixture()
def sandbox(store: Store) -> SkillSandbox:
    return SkillSandbox(store)


def make_skill(registry: SkillRegistry, *, base: str, permissions: tuple[str, ...]):
    return registry.register(
        base=base, name="演示功能", description="演示用 Skill 描述",
        input_schema={"type": "object"}, output_schema={"type": "object"},
        permissions=permissions,
    )


def make_gateway(store: Store, calls: list):
    def sender(kind, host, timeout_ms):
        calls.append((kind, host))
        return (0, 12, ())
    return EgressGateway(store, sender=sender)


# ───────────────────────── GWT-S1 文件越界拦截 ─────────────────────────


class TestGwtS1FileBoundary:
    def test_declared_and_approved_allowed(self, registry, sandbox):
        desc = make_skill(registry, base="sk_fs_ok", permissions=(CACHE_SCOPE,))
        ses = sandbox.session(desc, (CACHE_SCOPE,), trace_id="tr_fs_1")
        verdict = ses.check_file("data/cache/market.db")
        assert verdict.allowed and verdict.event is None

    def test_outside_scope_blocked(self, registry, sandbox):
        desc = make_skill(registry, base="sk_fs_out", permissions=(CACHE_SCOPE,))
        ses = sandbox.session(desc, (CACHE_SCOPE,), trace_id="tr_fs_2")
        verdict = ses.check_file("data/other/secret.json")
        assert not verdict.allowed
        assert verdict.kind == "local_read"
        assert verdict.warning == WARNING_TEXT
        assert verdict.event is not None and verdict.event.event == "BehaviorViolation"

    def test_blocked_does_not_run_original_op(self, registry, sandbox):
        desc = make_skill(registry, base="sk_fs_norun", permissions=(CACHE_SCOPE,))
        ses = sandbox.session(desc, (CACHE_SCOPE,), trace_id="tr_fs_3")
        touched: list = []

        env = ses.read_file("data/elsewhere/x", lambda p: touched.append(p))

        assert env.status == "validation_failed"
        assert touched == []          # 原操作未执行
        assert "这个 Skill 行为异常" in (env.reason or "")

    def test_allowed_read_runs_reader(self, registry, sandbox):
        desc = make_skill(registry, base="sk_fs_read", permissions=(CACHE_SCOPE,))
        ses = sandbox.session(desc, (CACHE_SCOPE,), trace_id="tr_fs_4")
        env = ses.read_file("data/cache/market.db", lambda p: {"rows": 3})
        assert env.status == "ok" and env.data == {"rows": 3}

    def test_traversal_blocked(self, registry, sandbox):
        desc = make_skill(registry, base="sk_fs_trav", permissions=(CACHE_SCOPE,))
        ses = sandbox.session(desc, (CACHE_SCOPE,), trace_id="tr_fs_5")
        verdict = ses.check_file("data/cache/../../secrets/key.json")
        assert not verdict.allowed

    def test_declared_but_unapproved_blocked(self, registry, sandbox):
        desc = make_skill(registry, base="sk_fs_unapp", permissions=(CACHE_SCOPE,))
        ses = sandbox.session(desc, (), trace_id="tr_fs_6")
        verdict = ses.check_file("data/cache/market.db")
        assert not verdict.allowed
        assert "未获用户批准" in verdict.reason

    def test_violation_persisted_and_minimal_payload(self, registry, sandbox, store):
        desc = make_skill(registry, base="sk_fs_audit", permissions=(CACHE_SCOPE,))
        ses = sandbox.session(desc, (CACHE_SCOPE,), trace_id="tr_fs_7")
        ses.check_file("data/other/secret.json")

        records = sandbox.violations()
        assert len(records) == 1
        rec = records[0]
        assert rec.skill_id == desc.skill_id and rec.kind == "local_read"
        assert rec.trace_id == "tr_fs_7"
        # 载荷最小口径（A7）：事件只含 skill_id 与越界类别，不含资源串
        event = ses.check_file("data/other/secret.json").event
        assert event is not None
        assert set(event.payload) == {"skill_id", "violation"}
        assert "secret" not in json.dumps(event.payload)
        # 落盘可查
        files = [n for n in store.list_files("execution_log")
                 if n.startswith(VIOLATION_PREFIX)]
        assert len(files) == 2

    def test_fresh_sandbox_reads_persisted_violations(self, registry, sandbox, store):
        desc = make_skill(registry, base="sk_fs_reload", permissions=(CACHE_SCOPE,))
        sandbox.session(desc, (CACHE_SCOPE,), trace_id="tr_fs_8").check_file("nope/x")
        assert len(SkillSandbox(store).violations()) == 1


# ───────────────────────── GWT-S2 网络越界拦截 ─────────────────────────


class TestGwtS2NetBoundary:
    def test_declared_and_approved_allowed(self, registry, sandbox):
        desc = make_skill(registry, base="sk_net_ok", permissions=(BAOSTOCK_NET,))
        ses = sandbox.session(desc, (BAOSTOCK_NET,), trace_id="tr_net_1")
        assert ses.check_net("api.baostock.com").allowed
        assert ses.check_net("baostock.com").allowed   # *.x 亦匹配裸域 x

    def test_undeclared_net_blocked(self, registry, sandbox):
        desc = make_skill(registry, base="sk_net_undecl", permissions=(CACHE_SCOPE,))
        ses = sandbox.session(desc, (CACHE_SCOPE,), trace_id="tr_net_2")
        verdict = ses.check_net("api.baostock.com")
        assert not verdict.allowed and verdict.kind == "net_access"

    def test_outside_host_pattern_blocked(self, registry, sandbox):
        desc = make_skill(registry, base="sk_net_out", permissions=(BAOSTOCK_NET,))
        ses = sandbox.session(desc, (BAOSTOCK_NET,), trace_id="tr_net_3")
        assert not ses.check_net("evil.example.com").allowed

    def test_blocked_never_touches_sender(self, registry, sandbox, store):
        desc = make_skill(registry, base="sk_net_nosend", permissions=(BAOSTOCK_NET,))
        ses = sandbox.session(desc, (BAOSTOCK_NET,), trace_id="tr_net_4")
        calls: list = []
        gw = ses.guard_gateway(make_gateway(store, calls))
        env = gw.execute("data_fetch", "evil.example.com",
                         initiator="skill", purpose="演示出网")
        assert env.status == "validation_failed"
        assert calls == []            # 不触碰底层 sender

    def test_allowed_net_goes_through_gateway(self, registry, sandbox, store):
        desc = make_skill(registry, base="sk_net_send", permissions=(BAOSTOCK_NET,))
        ses = sandbox.session(desc, (BAOSTOCK_NET,), trace_id="tr_net_5")
        calls: list = []
        gw = ses.guard_gateway(make_gateway(store, calls))
        env = gw.execute("data_fetch", "api.baostock.com",
                         initiator="skill", purpose="演示出网")
        assert env.status == "ok" and calls == [("data_fetch", "api.baostock.com")]

    def test_stream_blocked_raises(self, registry, sandbox, store):
        desc = make_skill(registry, base="sk_net_stream", permissions=(BAOSTOCK_NET,))
        ses = sandbox.session(desc, (BAOSTOCK_NET,), trace_id="tr_net_6")
        gw = ses.guard_gateway(make_gateway(store, []))
        with pytest.raises(SandboxViolationError):
            gw.stream("llm_call", "evil.example.com",
                      initiator="skill", purpose="演示流式")

    def test_guarded_gateway_exposes_only_read_state(self, registry, sandbox, store):
        desc = make_skill(registry, base="sk_net_surface", permissions=(BAOSTOCK_NET,))
        ses = sandbox.session(desc, (BAOSTOCK_NET,), trace_id="tr_net_7")
        gw = ses.guard_gateway(make_gateway(store, []))
        assert gw.online is True
        assert not hasattr(gw, "_sender")   # 无绕过入口


# ───────────────────────── GWT-S3 命令审批 ─────────────────────────


class TestGwtS3CommandApproval:
    def test_undeclared_command_blocked_with_warning(self, registry, sandbox):
        desc = make_skill(registry, base="sk_cmd_undecl", permissions=(CACHE_SCOPE,))
        ses = sandbox.session(desc, (CACHE_SCOPE,), trace_id="tr_cmd_1")
        verdict = ses.check_command()
        assert not verdict.allowed and verdict.kind == "exec_command"
        assert verdict.warning == WARNING_TEXT
        assert "这个 Skill 行为异常" in verdict.warning
        assert "禁用" in verdict.warning

    def test_declared_but_unapproved_blocked(self, registry, sandbox):
        desc = make_skill(registry, base="sk_cmd_unapp", permissions=(EXEC,))
        ses = sandbox.session(desc, (), trace_id="tr_cmd_2")
        assert not ses.check_command().allowed

    def test_approved_allows_command(self, registry, sandbox):
        desc = make_skill(registry, base="sk_cmd_ok", permissions=(EXEC,))
        ses = sandbox.session(desc, (EXEC,), trace_id="tr_cmd_3")
        assert ses.check_command().allowed

    def test_blocked_does_not_run_command(self, registry, sandbox):
        desc = make_skill(registry, base="sk_cmd_norun", permissions=(CACHE_SCOPE,))
        ses = sandbox.session(desc, (CACHE_SCOPE,), trace_id="tr_cmd_4")
        ran: list = []
        env = ses.run_command("rm -rf /", lambda c: ran.append(c))
        assert env.status == "validation_failed" and ran == []

    def test_allowed_runs_command(self, registry, sandbox):
        desc = make_skill(registry, base="sk_cmd_run", permissions=(EXEC,))
        ses = sandbox.session(desc, (EXEC,), trace_id="tr_cmd_5")
        env = ses.run_command("echo hi", lambda c: {"stdout": c})
        assert env.status == "ok" and env.data == {"stdout": "echo hi"}

    def test_warning_text_is_neutral(self):
        assert NeutralityGuard().check_output(WARNING_TEXT).passed


# ───────────────────────── 禁用选项（03 §1.5） ─────────────────────────


class TestDisableOption:
    def test_disable_blocks_all_exits(self, registry, sandbox):
        desc = make_skill(registry, base="sk_dis", permissions=(CACHE_SCOPE,))
        sandbox.disable(desc.skill_id)
        assert sandbox.is_disabled(desc.skill_id)
        ses = sandbox.session(desc, (CACHE_SCOPE,), trace_id="tr_dis_1")
        assert not ses.check_file("data/cache/market.db").allowed
        assert not ses.check_net("api.baostock.com").allowed
        assert not ses.check_command().allowed

    def test_disable_persisted_and_reloadable(self, registry, sandbox, store):
        desc = make_skill(registry, base="sk_dis_persist", permissions=(CACHE_SCOPE,))
        sandbox.disable(desc.skill_id)
        assert any(n.startswith(DISABLED_PREFIX) for n in store.list_files("config"))
        assert desc.skill_id in SkillSandbox(store).disabled_skills()

    def test_enable_restores(self, registry, sandbox, store):
        desc = make_skill(registry, base="sk_dis_toggle", permissions=(CACHE_SCOPE,))
        sandbox.disable(desc.skill_id)
        sandbox.enable(desc.skill_id)
        assert not sandbox.is_disabled(desc.skill_id)
        assert SkillSandbox(store).disabled_skills() == ()
        assert not any(n.startswith(DISABLED_PREFIX) for n in store.list_files("config"))


# ───────────────────────── 作用域匹配与入参校验 ─────────────────────────


class TestScopesAndValidation:
    def test_normalize_path(self):
        assert normalize_path("data//cache/./x.json") == "data/cache/x.json"
        assert normalize_path("data\\cache\\x") == "data/cache/x"

    def test_path_in_scope(self):
        assert path_in_scope("data/cache/a/b.json", "data/cache/**")
        assert not path_in_scope("data/cacheEB/x", "data/cache/**")
        assert not path_in_scope("data/cache/../../secrets/k", "data/cache/**")
        assert path_in_scope("data/cache/x.db", "data/cache/*.db")

    def test_host_in_scope(self):
        assert host_in_scope("api.baostock.com", "*.baostock.com")
        assert host_in_scope("baostock.com", "*.baostock.com")
        assert not host_in_scope("notbaostock.com.evil.com", "*.baostock.com")
        assert host_in_scope("API.BaoStock.com", "*.baostock.com")

    def test_session_requires_trace_id(self, registry, sandbox):
        desc = make_skill(registry, base="sk_need_trace", permissions=(CACHE_SCOPE,))
        with pytest.raises(SandboxValidationError):
            sandbox.session(desc, (), trace_id="  ")

    def test_empty_path_rejected(self, registry, sandbox):
        desc = make_skill(registry, base="sk_bad_path", permissions=(CACHE_SCOPE,))
        ses = sandbox.session(desc, (CACHE_SCOPE,), trace_id="tr_bad_1")
        with pytest.raises(SandboxValidationError):
            ses.check_file("")

    def test_violation_kind_matches_permission_vocabulary(self, registry, sandbox):
        from st_agent.contracts.registry_types import PermissionAction
        desc = make_skill(registry, base="sk_vocab", permissions=(CACHE_SCOPE,))
        ses = sandbox.session(desc, (CACHE_SCOPE,), trace_id="tr_vocab")
        verdict = ses.check_file("outside/x")
        assert verdict.kind in PermissionAction.__args__
