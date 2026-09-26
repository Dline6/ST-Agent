"""T-L1-001.6 测试：执行输出契约校验（03 §1.2 步骤 5/6；01 §2 方言）。

GWT 对照（任务文件 4 条）：
- GWT-C1 不合契约即显式失败：``ok`` 载荷缺必填 / 类型不符 → 信封 ``failed``
  （带 log_ref 与违规点），**且不登记为可复用结果**
- GWT-C2 合规输出放行：照常 ``ok`` + 登记（行为与现状一致）
- GWT-C3 未声明不误伤：``output_schema`` 为空 → 不校验、行为同现状
- GWT-C4 校验器可复用：同一 ``check_link`` 判「上游输出 → 下游输入」匹配，
  给出确定性结论与可读不匹配说明

方言判定本身（type/properties/required/items 各关键字）的用例见
``tests/contracts/test_schema_check.py``；本文件只覆盖流水线接线面。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from st_agent.contracts import check_link
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l1.reuse import OutputNotRegisteredError, OutputRegistry
from st_agent.l1.reuse.models import REUSABLE_STATUSES
from st_agent.l1.runner import RUN_PREFIX, SkillRunner
from st_agent.l1.skills import SkillRegistry
from st_agent.l0.storage import Store

PASS = "correct horse battery staple"

STRICT = "sk_strict_demo_v1.0"
LOOSE = "sk_loose_demo_v1.0"

STRICT_OUTPUT = {
    "type": "object",
    "properties": {"risk_level": {"type": "string"}},
    "required": ["risk_level"],
}
STRICT_INPUT = {
    "type": "object",
    "properties": {"stock_id": {"type": "string"}},
    "required": ["stock_id"],
}


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def registry(store: Store) -> SkillRegistry:
    reg = SkillRegistry(store)
    reg.register(base="sk_strict_demo", name="严格输出演示",
                 description="声明 output_schema 的演示 Skill",
                 input_schema=STRICT_INPUT, output_schema=STRICT_OUTPUT,
                 permissions=("local_read:<data/cache/**>",))
    reg.register(base="sk_loose_demo", name="宽松输出演示",
                 description="未声明 output_schema 的演示 Skill",
                 permissions=("local_read:<data/cache/**>",))
    return reg


@pytest.fixture()
def outputs(store: Store) -> OutputRegistry:
    return OutputRegistry(store)


@pytest.fixture()
def runner(store: Store, registry: SkillRegistry,
           outputs: OutputRegistry) -> SkillRunner:
    return SkillRunner(store, registry, outputs=outputs)


def payload_of(runner: SkillRunner, skill_run_id: str) -> dict:
    return json.loads(
        runner._store.get("execution_log",
                          f"{RUN_PREFIX}{skill_run_id}.json").decode("utf-8"))


# ───────────────────────── GWT-C1 不合契约即显式失败 ─────────────────────────


class TestGwtC1ContractViolation:
    def test_missing_required_property_fails_and_not_registered(
            self, runner, registry, outputs):
        runner.register_executor(
            STRICT, lambda ctx, p: ResultEnvelope.ok({"other": 1}))
        out = runner.run(STRICT, {},
                         approved_permissions=("local_read:<data/cache/**>",))

        assert out.envelope.status == "failed"
        assert "risk_level" in (out.envelope.reason or "")
        assert out.envelope.log_ref == f"{RUN_PREFIX}{out.skill_run_id}.json"
        # 不登记为可复用结果（GWT-C1 下半）
        with pytest.raises(OutputNotRegisteredError):
            outputs.get(out.skill_run_id)

    def test_type_mismatch_fails_with_violation_point(self, runner, outputs):
        runner.register_executor(
            STRICT, lambda ctx, p: ResultEnvelope.ok({"risk_level": 3}))
        out = runner.run(STRICT, {},
                         approved_permissions=("local_read:<data/cache/**>",))

        assert out.envelope.status == "failed"
        assert "risk_level" in (out.envelope.reason or "")
        assert "string" in (out.envelope.reason or "")
        with pytest.raises(OutputNotRegisteredError):
            outputs.get(out.skill_run_id)

    def test_violation_still_lands_in_run_log(self, runner):
        runner.register_executor(
            STRICT, lambda ctx, p: ResultEnvelope.ok({}))
        out = runner.run(STRICT, {},
                         approved_permissions=("local_read:<data/cache/**>",))
        record = payload_of(runner, out.skill_run_id)
        assert record["envelope"]["status"] == "failed"
        assert record["skill_id"] == STRICT

    def test_downstream_sees_violating_upstream_as_dependency_failed(
            self, runner, registry):
        registry.register(base="sk_strict_consumer", name="严格上游消费演示",
                          description="依赖严格演示 Skill 的下游",
                          dependencies=(STRICT,),
                          permissions=("local_read:<data/cache/**>",))
        runner.register_executor(STRICT, lambda ctx, p: ResultEnvelope.ok({}))
        runner.register_executor(
            "sk_strict_consumer_v1.0", lambda ctx, p: ResultEnvelope.ok({"n": 1}))

        out = runner.run("sk_strict_consumer_v1.0", {},
                         approved_permissions=("local_read:<data/cache/**>",))
        assert out.envelope.status == "dependency_failed"
        assert STRICT in (out.envelope.reason or "")


# ───────────────────────── GWT-C2 合规输出放行 ─────────────────────────


class TestGwtC2CompliantPasses:
    def test_compliant_payload_ok_and_registered(self, runner, outputs):
        runner.register_executor(
            STRICT, lambda ctx, p: ResultEnvelope.ok({"risk_level": "high"}))
        out = runner.run(STRICT, {},
                         approved_permissions=("local_read:<data/cache/**>",))

        assert out.envelope.status == "ok"
        assert out.envelope.data == {"risk_level": "high"}
        assert outputs.get(out.skill_run_id).status == "ok"

    def test_optional_property_may_be_absent(self, runner):
        """未进 required 的声明属性缺省即合法（properties 只约束已出现属性）。"""
        runner.register_executor(
            STRICT, lambda ctx, p: ResultEnvelope.ok({"risk_level": "low"}))
        out = runner.run(STRICT, {},
                         approved_permissions=("local_read:<data/cache/**>",))
        assert out.envelope.status == "ok"

    def test_empty_status_skips_payload_check(self, runner, outputs):
        """``empty`` 无载荷，不得因 required 而误判失败。"""
        runner.register_executor(
            STRICT, lambda ctx, p: ResultEnvelope.empty("今日无高危信号"))
        out = runner.run(STRICT, {},
                         approved_permissions=("local_read:<data/cache/**>",))

        assert out.envelope.status == "empty"
        assert out.envelope.reason == "今日无高危信号"
        assert outputs.get(out.skill_run_id).status == "empty"


# ───────────────────────── GWT-C3 未声明不误伤 ─────────────────────────


class TestGwtC3UndeclaredSchemaUntouched:
    def test_arbitrary_payload_passes_when_schema_empty(self, runner, outputs):
        runner.register_executor(
            LOOSE, lambda ctx, p: ResultEnvelope.ok({"anything": [1, {"a": 2}]}))
        out = runner.run(LOOSE, {},
                         approved_permissions=("local_read:<data/cache/**>",))

        assert out.envelope.status == "ok"
        assert outputs.get(out.skill_run_id).status == "ok"

    def test_official_pack_skills_run_unchanged(self, runner, registry):
        """回归面：官方 Pack 描述体的 output_schema 与既有执行器输出相容。"""
        from st_agent.l1.skills import ensure_official_pack
        ensure_official_pack(registry)
        skill_id = "sk_unhat_eligibility_check_v1.0"
        runner.register_executor(
            skill_id, lambda ctx, p: ResultEnvelope.ok({"conditions": []}))
        out = runner.run(
            skill_id, {},
            approved_permissions=registry.get(skill_id).permissions)
        assert out.envelope.status == "ok"

    def test_all_reusable_statuses_unchanged(self):
        """可复用分支口径未被本批改动（ok / empty）。"""
        assert REUSABLE_STATUSES == ("ok", "empty")


# ───────────────────────── GWT-C4 校验器可复用 ─────────────────────────


class TestGwtC4ValidatorReusable:
    def test_link_verdict_from_registry_descriptors(self, registry):
        """上层（T-L1-003 连线校验 / T-L1-004 Pack 装载）只取描述体即可判定。"""
        downstream = registry.register(
            base="sk_link_consumer", name="连线消费演示",
            description="需要 stock_id 输入的下游",
            input_schema=STRICT_INPUT, output_schema={},
            permissions=("local_read:<data/cache/**>",))
        upstream = registry.get(STRICT)

        ok = check_link(upstream.output_schema, downstream.input_schema)
        assert not ok.compatible  # 上游只声明 risk_level，未承诺 stock_id
        assert "stock_id" in ok.describe()
        assert "上游未声明" in ok.describe()

        matched = check_link(STRICT_INPUT, downstream.input_schema)
        assert matched.compatible

    def test_same_validator_used_for_payload_and_link(self):
        """一个校验件两个消费面，不各造一套口径（01 §2 明令）。"""
        from st_agent.contracts import check_payload
        schema = STRICT_OUTPUT
        assert check_payload(schema, {"risk_level": "high"}).passed
        assert check_payload(schema, {}).passed is False
        assert check_link(schema, schema).compatible
