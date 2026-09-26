"""T-L1-001.5 测试：沙箱接线 SkillRunner 注入（03 §1.5 + 01 §10 / §11）。

GWT 对照（任务文件 4 条 + 回归）：
- GWT-W1 禁用生效：Skill 被 ``sandbox.disable`` 后经 runner 执行 →
  直接 ``validation_failed`` 拦截且执行器不被调用
- GWT-W2 网络出口受限：执行器用 ``ctx.gateway`` 向声明外主机出网 →
  拦截 + BehaviorViolation 留痕 + 底层 sender 未被触碰
- GWT-W3 文件/命令出口受限：经 ``ctx.sandbox`` 读声明外路径 / 执行未批准
  命令 → 拦截 + 不执行原操作
- GWT-W4 LLM 路径受限：端点 provider 的目标主机不在声明 ``net_access`` 内 →
  拦截 + 不触碰底层 client；``provider → host`` 映射按 01 §7 条目形态持久化并读回
- 回归：不接沙箱时 ``SkillRunner`` 行为与现状一致（ctx 句柄原样透传）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from st_agent.contracts.registry_types import ConfigEntry
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.llm import ENDPOINT_PREFIX, EndpointRegistry, StreamEvent
from st_agent.l0.net import EgressGateway
from st_agent.l0.storage import Store
from st_agent.l1.runner import SkillRunner
from st_agent.l1.sandbox import (
    PROVIDER_HOST_PREFIX,
    VIOLATION_PREFIX,
    GuardedLlmClient,
    ProviderHostExistsError,
    ProviderHostNotFoundError,
    ProviderHostRegistry,
    SandboxValidationError,
    SandboxViolationError,
    SandboxedGateway,
    SkillSandbox,
)
from st_agent.l1.skills import SkillRegistry, ensure_official_pack

PASS = "correct horse battery staple"

CACHE_SCOPE = "local_read:<data/cache/**>"
OK_NET = "net_access:<*.ok.com>"

PROVIDER = "p1"


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def registry(store: Store) -> SkillRegistry:
    reg = SkillRegistry(store)
    ensure_official_pack(reg)
    return reg


def make_skill(reg: SkillRegistry, *, base: str, permissions: tuple[str, ...]):
    return reg.register(
        base=base, name="演示功能", description="演示用 Skill 描述",
        input_schema={"type": "object"}, output_schema={"type": "object"},
        permissions=permissions,
    )


def approved_of(reg: SkillRegistry, skill_id: str) -> tuple[str, ...]:
    return reg.get(skill_id).permissions


def make_gateway(store: Store, calls: list[tuple[str, str]]) -> EgressGateway:
    def sender(kind, host, timeout_ms):
        calls.append((kind, host))
        return (0, 12, ())
    return EgressGateway(store, sender=sender)


class RecordingLlm:
    """``LlmClient`` 替身：记录被调用次数（用于断言「未触碰底层」）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def invoke(self, endpoint_id: str, prompt: str, **_kwargs):
        self.calls.append((endpoint_id, prompt))
        yield StreamEvent(kind="chunk", text="ok")


def llm_echo_executor() -> object:
    """执行器：经 ``ctx.llm`` 发起一次 LLM 调用，把事件类型回填信封。"""
    def _fn(ctx, _params):
        events = list(ctx.llm.invoke("ep_1", "hi", initiator="t", purpose="t"))
        return ResultEnvelope.ok({"kinds": [e.kind for e in events]})
    return _fn


# ───────────────────────── GWT-W1 禁用生效 ─────────────────────────


class TestGwtW1Disable:
    def test_disabled_skill_blocked_and_executor_not_called(self, store, registry):
        desc = make_skill(registry, base="sk_disabled", permissions=())
        sandbox = SkillSandbox(store)
        sandbox.disable(desc.skill_id)
        runner = SkillRunner(store, registry, sandbox=sandbox)
        called: list = []
        runner.register_executor(
            desc.skill_id, lambda ctx, p: called.append(1) or ResultEnvelope.ok({}))

        out = runner.run(desc.skill_id, {}, approved_permissions=())

        assert out.envelope.status == "validation_failed"
        assert "已被禁用" in (out.envelope.reason or "")
        assert called == []                     # 执行器未被调用
        assert out.envelope.data is None

    def test_disabled_blocks_dependencies_too(self, store, registry):
        """受禁用的是依赖本身时，下游见 dependency_failed（上游显式失败）。"""
        dep = make_skill(registry, base="sk_dep_disabled", permissions=())
        top = registry.register(
            base="sk_top_over_disabled", name="演示下游", description="演示用 Skill 描述",
            input_schema={"type": "object"}, output_schema={"type": "object"},
            dependencies=(dep.skill_id,),  # type: ignore[call-arg]
        )
        sandbox = SkillSandbox(store)
        sandbox.disable(dep.skill_id)
        runner = SkillRunner(store, registry, sandbox=sandbox)
        runner.register_executor(top.skill_id, lambda ctx, p: ResultEnvelope.ok({}))

        out = runner.run(top.skill_id, {}, approved_permissions=())

        assert out.envelope.status == "dependency_failed"

    def test_enable_restores(self, store, registry):
        desc = make_skill(registry, base="sk_reenabled", permissions=())
        sandbox = SkillSandbox(store)
        sandbox.disable(desc.skill_id)
        sandbox.enable(desc.skill_id)
        runner = SkillRunner(store, registry, sandbox=sandbox)
        runner.register_executor(desc.skill_id, lambda ctx, p: ResultEnvelope.ok({"ran": True}))

        out = runner.run(desc.skill_id, {}, approved_permissions=())

        assert out.envelope.status == "ok"


# ───────────────────────── GWT-W2 网络出口受限 ─────────────────────────


class TestGwtW2NetBoundary:
    def test_out_of_scope_host_blocked_sender_untouched(self, store, registry):
        desc = make_skill(registry, base="sk_net_wire", permissions=(OK_NET,))
        calls: list = []
        sandbox = SkillSandbox(store)
        runner = SkillRunner(store, registry, gateway=make_gateway(store, calls),
                             sandbox=sandbox)

        def _fn(ctx, _params):
            assert isinstance(ctx.gateway, SandboxedGateway)
            return ctx.gateway.execute("data_fetch", "evil.example.com",
                                       initiator="t", purpose="t")

        runner.register_executor(desc.skill_id, _fn)
        out = runner.run(desc.skill_id, {},
                         approved_permissions=approved_of(registry, desc.skill_id))

        assert out.envelope.status == "validation_failed"
        assert calls == []                      # 底层 sender 未被触碰
        violations = sandbox.violations()
        assert len(violations) == 1
        assert violations[0].kind == "net_access"
        assert violations[0].trace_id == out.trace.trace_id.value

    def test_in_scope_host_passes_through(self, store, registry):
        desc = make_skill(registry, base="sk_net_wire_ok", permissions=(OK_NET,))
        calls: list = []
        runner = SkillRunner(store, registry, gateway=make_gateway(store, calls),
                             sandbox=SkillSandbox(store))

        def _fn(ctx, _params):
            return ctx.gateway.execute("data_fetch", "api.ok.com",
                                       initiator="t", purpose="t")

        runner.register_executor(desc.skill_id, _fn)
        out = runner.run(desc.skill_id, {},
                         approved_permissions=approved_of(registry, desc.skill_id))

        assert out.envelope.status == "ok"
        assert calls == [("data_fetch", "api.ok.com")]

    def test_violation_persisted_minimal_payload(self, store, registry):
        desc = make_skill(registry, base="sk_net_audit", permissions=(OK_NET,))
        sandbox = SkillSandbox(store)
        runner = SkillRunner(store, registry, gateway=make_gateway(store, []),
                             sandbox=sandbox)

        def _fn(ctx, _params):
            return ctx.gateway.execute("data_fetch", "evil.example.com",
                                       initiator="t", purpose="t")

        runner.register_executor(desc.skill_id, _fn)
        runner.run(desc.skill_id, {},
                   approved_permissions=approved_of(registry, desc.skill_id))

        files = [n for n in store.list_files("execution_log")
                 if n.startswith(VIOLATION_PREFIX)]
        assert len(files) == 1
        record = sandbox.violations()[0]
        assert record.skill_id == desc.skill_id
        # A7：留痕与事件载荷不含被访问资源串（主机 / 路径 / 命令）
        raw = store.get("execution_log", files[0]).decode("utf-8")
        assert "evil" not in raw and "evil" not in record.warning


# ───────────────────────── GWT-W3 文件/命令出口受限 ─────────────────────────


class TestGwtW3FileCommandBoundary:
    def test_out_of_scope_read_blocked_and_not_run(self, store, registry):
        desc = make_skill(registry, base="sk_fs_wire", permissions=(CACHE_SCOPE,))
        runner = SkillRunner(store, registry, sandbox=SkillSandbox(store))
        touched: list = []

        def _fn(ctx, _params):
            assert ctx.sandbox is not None
            return ctx.sandbox.read_file("data/other/secret.json",
                                         lambda p: touched.append(p))

        runner.register_executor(desc.skill_id, _fn)
        out = runner.run(desc.skill_id, {},
                         approved_permissions=approved_of(registry, desc.skill_id))

        assert out.envelope.status == "validation_failed"
        assert touched == []                    # 原操作未执行

    def test_in_scope_read_runs(self, store, registry):
        desc = make_skill(registry, base="sk_fs_wire_ok", permissions=(CACHE_SCOPE,))
        runner = SkillRunner(store, registry, sandbox=SkillSandbox(store))
        runner.register_executor(
            desc.skill_id,
            lambda ctx, p: ctx.sandbox.read_file("data/cache/market.db",
                                                 lambda _p: {"rows": 3}))

        out = runner.run(desc.skill_id, {},
                         approved_permissions=approved_of(registry, desc.skill_id))

        assert out.envelope.status == "ok"
        assert out.envelope.data == {"rows": 3}

    def test_command_not_declared_blocked_and_not_run(self, store, registry):
        """未声明 ``exec_command`` 的 Skill 经 ctx 会话请求命令执行 → 拦截。

        注：声明的 Skill 若未获批准，runner 的权限闸门会先拦下（执行器不进），
        故此处取「未声明」这一形态，才能真正走到沙箱的命令核对点。
        """
        desc = make_skill(registry, base="sk_cmd_wire", permissions=())
        runner = SkillRunner(store, registry, sandbox=SkillSandbox(store))
        touched: list = []
        runner.register_executor(
            desc.skill_id,
            lambda ctx, p: ctx.sandbox.run_command("rm -rf /", lambda c: touched.append(c)))

        out = runner.run(desc.skill_id, {}, approved_permissions=())

        assert out.envelope.status == "validation_failed"
        assert "exec_command" in (out.envelope.reason or "")
        assert touched == []


# ───────────────────────── GWT-W4 LLM 路径受限 ─────────────────────────


@pytest.fixture()
def endpoints(store: Store) -> EndpointRegistry:
    reg = EndpointRegistry(store)
    reg.register("ep_1", kind="local", provider=PROVIDER,
                 capability={"max_context_tokens": 4096})
    return reg


@pytest.fixture()
def hosts(store: Store) -> ProviderHostRegistry:
    reg = ProviderHostRegistry(store)
    reg.register(PROVIDER, "api.thirdparty.com")
    return reg


class TestGwtW4LlmBoundary:
    def test_provider_host_out_of_scope_blocked(self, store, registry, endpoints, hosts):
        desc = make_skill(registry, base="sk_llm_wire", permissions=(OK_NET,))
        llm = RecordingLlm()
        sandbox = SkillSandbox(store, endpoints=endpoints, provider_hosts=hosts)
        runner = SkillRunner(store, registry, llm=llm, sandbox=sandbox)
        runner.register_executor(desc.skill_id, llm_echo_executor())

        out = runner.run(desc.skill_id, {},
                         approved_permissions=approved_of(registry, desc.skill_id))

        assert out.envelope.status == "ok"
        assert out.envelope.data == {"kinds": ["error"]}   # 单条 error，无 chunk
        assert llm.calls == []                             # 底层 client 未被触碰
        assert sandbox.violations()[0].kind == "net_access"

    def test_provider_host_in_scope_delegates(self, store, registry, endpoints, hosts):
        hosts.update(PROVIDER, "api.ok.com")
        desc = make_skill(registry, base="sk_llm_wire_ok", permissions=(OK_NET,))
        llm = RecordingLlm()
        sandbox = SkillSandbox(store, endpoints=endpoints, provider_hosts=hosts)
        runner = SkillRunner(store, registry, llm=llm, sandbox=sandbox)
        runner.register_executor(desc.skill_id, llm_echo_executor())

        out = runner.run(desc.skill_id, {},
                         approved_permissions=approved_of(registry, desc.skill_id))

        assert out.envelope.data == {"kinds": ["chunk"]}
        assert llm.calls == [("ep_1", "hi")]
        assert sandbox.violations() == ()

    def test_unconfigured_sandbox_fails_closed(self, store, registry):
        """沙箱未配端点/提供方映射 → 无法核对范围，LLM 出网一律拒绝。"""
        desc = make_skill(registry, base="sk_llm_noconfig", permissions=(OK_NET,))
        llm = RecordingLlm()
        runner = SkillRunner(store, registry, llm=llm, sandbox=SkillSandbox(store))
        runner.register_executor(desc.skill_id, llm_echo_executor())

        out = runner.run(desc.skill_id, {},
                         approved_permissions=approved_of(registry, desc.skill_id))

        assert out.envelope.data == {"kinds": ["error"]}
        assert llm.calls == []

    def test_unregistered_endpoint_fails_closed(self, store, registry, endpoints, hosts):
        desc = make_skill(registry, base="sk_llm_noendpoint", permissions=(OK_NET,))
        llm = RecordingLlm()
        sandbox = SkillSandbox(store, endpoints=endpoints, provider_hosts=hosts)
        runner = SkillRunner(store, registry, llm=llm, sandbox=sandbox)

        def _fn(ctx, _params):
            return ResultEnvelope.ok(
                {"kinds": [e.kind for e in ctx.llm.invoke(
                    "ep_unknown", "hi", initiator="t", purpose="t")]})

        runner.register_executor(desc.skill_id, _fn)
        out = runner.run(desc.skill_id, {},
                         approved_permissions=approved_of(registry, desc.skill_id))

        assert out.envelope.data == {"kinds": ["error"]}
        assert llm.calls == []

    def test_ctx_llm_is_guarded_proxy(self, store, registry, endpoints, hosts):
        desc = make_skill(registry, base="sk_llm_proxy", permissions=(OK_NET,))
        runner = SkillRunner(store, registry, llm=RecordingLlm(),
                             sandbox=SkillSandbox(store, endpoints=endpoints,
                                                  provider_hosts=hosts))
        seen: list = []
        runner.register_executor(
            desc.skill_id,
            lambda ctx, p: seen.append(type(ctx.llm).__name__) or ResultEnvelope.ok({}))

        runner.run(desc.skill_id, {},
                   approved_permissions=approved_of(registry, desc.skill_id))

        assert seen == [GuardedLlmClient.__name__]


# ───────────────────────── provider → host 映射（01 §7 条目形态） ─────────


class TestProviderHostRegistry:
    def test_roundtrip_config_entry_seven_fields(self, store):
        reg = ProviderHostRegistry(store)
        reg.register(PROVIDER, "api.thirdparty.com")

        entry = reg.get(PROVIDER)
        assert isinstance(entry, ConfigEntry)
        assert entry.default == "api.thirdparty.com"
        assert entry.value_schema == {"type": "string"}
        assert entry.scope == "global"
        assert entry.panel_form_spec.widget == "text"
        assert entry.change_policy.requires_confirmation is True
        assert entry.display_name and entry.description_for_chat

    def test_persisted_and_read_back_by_fresh_registry(self, store):
        ProviderHostRegistry(store).register(PROVIDER, "api.thirdparty.com")
        fresh = ProviderHostRegistry(store)
        assert fresh.resolve(PROVIDER) == "api.thirdparty.com"
        files = [n for n in store.list_files("config")
                 if n.startswith(PROVIDER_HOST_PREFIX)]
        assert files == [f"{PROVIDER_HOST_PREFIX}{PROVIDER}.json"]

    def test_resolve_unknown_returns_none(self, store):
        assert ProviderHostRegistry(store).resolve("nope") is None

    def test_duplicate_register_rejected_update_wins(self, store):
        reg = ProviderHostRegistry(store)
        reg.register(PROVIDER, "a.example.com")
        with pytest.raises(ProviderHostExistsError):
            reg.register(PROVIDER, "b.example.com")
        reg.update(PROVIDER, "b.example.com")
        assert reg.resolve(PROVIDER) == "b.example.com"
        with pytest.raises(ProviderHostNotFoundError):
            reg.update("nope", "b.example.com")

    def test_list_sorted(self, store):
        reg = ProviderHostRegistry(store)
        reg.register("p2", "b.example.com")
        reg.register("p1", "a.example.com")
        assert [e.config_id for e in reg.list()] == [
            "llm-provider-host/p1", "llm-provider-host/p2"]

    def test_illegal_provider_and_host_rejected(self, store):
        reg = ProviderHostRegistry(store)
        for bad in ("../escape", "a/b", "", "p with space"):
            with pytest.raises(SandboxValidationError):
                reg.resolve(bad)
        with pytest.raises(SandboxValidationError):
            reg.register("p_ok", "  ")
        with pytest.raises(SandboxValidationError):
            reg.register("p_ok", "a b.com")


# ───────────────────────── SandboxedGateway.llm_transport（A4） ─────────


class TestSandboxedGatewayLlmTransport:
    def _session(self, store, registry, *, permissions=(OK_NET,), approved=None):
        desc = make_skill(registry, base="sk_transport", permissions=permissions)
        sandbox = SkillSandbox(store)
        return sandbox.session(
            desc, permissions if approved is None else approved, trace_id="tr_llm_t")

    def test_out_of_scope_raises_and_sender_untouched(self, store, registry):
        calls: list = []
        ses = self._session(store, registry)
        gw = ses.guard_gateway(make_gateway(store, calls))
        endpoint = LlmEndpointStub(endpoint_id="ep_1", provider=PROVIDER)

        with pytest.raises(SandboxViolationError):
            list(gw.llm_transport({PROVIDER: "api.thirdparty.com"})(
                endpoint, "hi", None, 1000))

        assert calls == []

    def test_in_scope_streams_via_gateway(self, store, registry):
        calls: list = []
        ses = self._session(store, registry)
        gw = ses.guard_gateway(make_gateway(store, calls))
        endpoint = LlmEndpointStub(endpoint_id="ep_1", provider=PROVIDER)

        chunks = list(gw.llm_transport({PROVIDER: "api.ok.com"})(
            endpoint, "hi", None, 1000))

        assert chunks == []
        assert calls == [("llm_call", "api.ok.com")]

    def test_unregistered_provider_raises(self, store, registry):
        calls: list = []
        ses = self._session(store, registry)
        gw = ses.guard_gateway(make_gateway(store, calls))
        endpoint = LlmEndpointStub(endpoint_id="ep_1", provider="p_unknown")

        with pytest.raises(SandboxViolationError):
            list(gw.llm_transport({PROVIDER: "api.ok.com"})(
                endpoint, "hi", None, 1000))

        assert calls == []


class LlmEndpointStub:
    """最小端点替身（``llm_transport`` 只读 ``endpoint_id`` 与 ``provider``）。"""

    def __init__(self, *, endpoint_id: str, provider: str) -> None:
        self.endpoint_id = endpoint_id
        self.provider = provider


# ───────────────────────── 回归：不接沙箱行为不变 ─────────────────────────


class TestRegressionWithoutSandbox:
    def test_ctx_handles_passed_through_untouched(self, store, registry):
        desc = make_skill(registry, base="sk_nosandbox", permissions=(OK_NET, CACHE_SCOPE))
        gateway = make_gateway(store, [])
        llm = RecordingLlm()
        runner = SkillRunner(store, registry, llm=llm, gateway=gateway)
        seen: dict = {}

        def _fn(ctx, _params):
            seen.update(sandbox=ctx.sandbox, gateway=ctx.gateway, llm=ctx.llm)
            return ResultEnvelope.ok({})

        runner.register_executor(desc.skill_id, _fn)
        out = runner.run(desc.skill_id, {},
                         approved_permissions=approved_of(registry, desc.skill_id))

        assert out.envelope.status == "ok"
        assert seen["sandbox"] is None
        assert seen["gateway"] is gateway        # 原样透传（非受限包装）
        assert seen["llm"] is llm

    def test_no_violations_recorded(self, store, registry):
        desc = make_skill(registry, base="sk_nosandbox_clean", permissions=())
        runner = SkillRunner(store, registry)
        runner.register_executor(desc.skill_id, lambda ctx, p: ResultEnvelope.ok({}))
        runner.run(desc.skill_id, {}, approved_permissions=())
        assert [n for n in store.list_files("execution_log")
                if n.startswith(VIOLATION_PREFIX)] == []


# ───────────────────────── 契约面自检 ─────────────────────────


def test_endpoint_prefix_untouched_by_mapping():
    """映射前缀与 L0 端点前缀分属不同目录（不互相踩）。"""
    assert PROVIDER_HOST_PREFIX != ENDPOINT_PREFIX
    assert not PROVIDER_HOST_PREFIX.startswith(ENDPOINT_PREFIX)
