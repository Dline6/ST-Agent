"""T-L1-006.2 测试：组合根的 L1 能力面装配（03 §1.1 / §1.2 / §1.3 / §1.5 / §2）。

GWT 对照（任务文件 4 条）：
- GWT-1 装配完整（沙箱已携端点注册表与 `provider → host` 映射）
- GWT-2 官方 Pack 自动可用（Story 02 验收①）——空 `Store` 且未配端点/凭据
- GWT-3 端到端一次执行可追溯（信封 / SkillRun / Trace / 复用登记）
- GWT-4 重复装配幂等（不抛 `SkillExistsError`、版本与改过的参数保留）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.registry_types import SemVer
from st_agent.l0.storage import Store
from st_agent.l1.runtime import L1Runtime, build_l1_runtime
from st_agent.l1.skills.ids import base_of
from st_agent.l1.skills.official.install import uncovered_bases
from st_agent.l1.workflow.models import ParamBinding, WorkflowDAG, WorkflowNode

PASS = "correct horse battery staple"
LOCAL_CAP = {"max_context_tokens": 4_096, "supports_structured_output": False}
AGGREGATE = "sk_data_aggregate_v1.0"
TARGET = "sk_st_list_sync_v1.0"
FLOW_ID = "wf_daily_brief_v1.0"


class _FakeMarketQuery:
    """恒空取数源：`empty` 是合法结论（01 §5），足以跑通装配后的完整流水线。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def query(self, sql: str, params: tuple = ()) -> ResultEnvelope:
        self.calls.append((sql, params))
        return ResultEnvelope.empty("测试数据面为空")


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def market_query() -> _FakeMarketQuery:
    return _FakeMarketQuery()


@pytest.fixture()
def runtime(store: Store, market_query: _FakeMarketQuery) -> L1Runtime:
    return build_l1_runtime(store, market_query=market_query)


class TestGwt1AssemblyComplete:
    def test_l1_facades_are_wired_to_the_same_store(self, runtime: L1Runtime, store: Store):
        assert runtime.store is store
        assert runtime.skills is not None
        assert runtime.sandbox is not None
        assert runtime.runner is not None
        assert runtime.outputs is not None
        assert runtime.workflows is not None

    def test_sandbox_carries_endpoints_and_provider_hosts(self, store: Store,
                                                          market_query: _FakeMarketQuery):
        rt = build_l1_runtime(store, market_query=market_query)
        rt.endpoints.register("local-main", kind="local", provider="local-llama",
                              capability=LOCAL_CAP)
        rt.provider_hosts.register("local-llama", "api.example.com")
        desc = rt.skills.get(AGGREGATE)
        session = rt.sandbox.session(desc, desc.permissions, trace_id="tr_gwt1_probe")

        # 两个注册表都在沙箱里才解析得出目标主机（缺任一即 None → fail-closed）
        assert session.resolve_llm_host("local-main") == "api.example.com"

    def test_sandbox_fails_closed_without_a_registered_host(self, runtime: L1Runtime):
        runtime.endpoints.register("local-main", kind="local", provider="local-llama",
                                   capability=LOCAL_CAP)

        desc = runtime.skills.get(AGGREGATE)
        session = runtime.sandbox.session(desc, desc.permissions, trace_id="tr_gwt1_fail")
        assert session.resolve_llm_host("local-main") is None
        assert session.guard_llm_host("local-main").allowed is False


class TestGwt2OfficialPack:
    def test_pack_is_listed_without_endpoints_or_credentials(self, runtime: L1Runtime, store: Store):
        assert store.list_files("config") != ()          # 仅官方 Pack 描述体
        assert runtime.endpoints.list_endpoints() == ()   # 未配任何端点
        assert runtime.vault.list_credentials() == ()     # 未配任何凭据

        bases = {base_of(d.skill_id) for d in runtime.skills.list_all()}
        assert len(bases) == 12
        assert AGGREGATE in {d.skill_id for d in runtime.skills.list_all()}

    def test_every_base_has_an_executor(self):
        assert uncovered_bases() == ()


class TestGwt3EndToEndRun:
    def test_run_persists_skill_run_and_trace(self, runtime: L1Runtime, store: Store,
                                              market_query: _FakeMarketQuery):
        desc = runtime.skills.get(TARGET)

        outcome = runtime.runner.run(
            TARGET, approved_permissions=desc.permissions,
            initiator="runtime-test", purpose="装配自检",
        )

        assert outcome.envelope.status in ("ok", "empty")
        assert market_query.calls                               # 执行器确实经注入源取数
        assert any(n.startswith("skill-run/") for n in store.list_files("execution_log"))
        assert outcome.trace.steps[-1].ref == outcome.skill_run_id

    def test_output_is_registered_for_reuse(self, runtime: L1Runtime):
        desc = runtime.skills.get(TARGET)

        outcome = runtime.runner.run(
            TARGET, approved_permissions=desc.permissions,
            initiator="runtime-test", purpose="装配自检",
        )

        assert runtime.outputs.is_registered(outcome.skill_run_id)

    def test_undeclared_permissions_are_not_auto_granted(self, runtime: L1Runtime):
        # 官方 Pack 自 T-L1-009.3 起不声明权限，故另注册一个声明了权限的 Skill
        declaring = "sk_l1_declares_v1.0"
        runtime.skills.register(
            "sk_l1_declares", version="1.0", name="声明权限的装配样例",
            description="声明了一条权限的装配样例",
            permissions=("net_access:<*.declared.example>",), offline_level="none",
        )
        runtime.runner.register_executor(declaring, lambda ctx, params: ResultEnvelope.ok({}))

        # 不传 approved_permissions → 该 Skill 被拦（01 §10）
        outcome = runtime.runner.run(declaring, initiator="runtime-test", purpose="装配自检")

        assert outcome.envelope.status == "validation_failed"


class TestGwt4RebuildIdempotent:
    def test_rebuild_keeps_versions_and_edited_parameters(self, store: Store,
                                                          market_query: _FakeMarketQuery):
        rt = build_l1_runtime(store, market_query=market_query)
        rt.skills.set_parameters(AGGREGATE, {"format": "trend"})

        again = build_l1_runtime(store, market_query=market_query)

        desc = again.skills.get(AGGREGATE)
        assert {p.default for p in desc.parameters if p.name == "format"} == {"trend"}
        assert len(again.skills.list_all()) == 12

    def test_registered_workflow_survives_rebuild(self, store: Store,
                                                  market_query: _FakeMarketQuery):
        rt = build_l1_runtime(store, market_query=market_query)
        dag = WorkflowDAG(
            flow_id=FLOW_ID,
            name="每日数据聚合简报",
            description="面向演示的工作流定义",
            version=SemVer.parse("1.0"),
            nodes=(WorkflowNode(node_id="agg", skill_id=AGGREGATE,
                                params={"format": ParamBinding(kind="literal", value="简报")}),),
        )
        rt.workflows.save(dag)

        again = build_l1_runtime(store, market_query=market_query)

        assert [d.flow_id for d in again.workflows.list_all()] == [FLOW_ID]
        assert again.workflows.get(FLOW_ID).nodes[0].skill_id == AGGREGATE
