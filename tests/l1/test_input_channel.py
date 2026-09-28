"""T-L1-010.1 测试：`input_schema` 运行期输入通道（01 §2 · 03 §1.2 步骤 2）。

对象类输入（`parameters` 承载不了的对象）由调用方经 `run(inputs=…)` 供给，
按描述体 `input_schema` 核验——判定件与输出侧同一个 `contracts.check_payload`。

GWT 对照（任务文件 5 条）：
- GWT-1 合法输入到达执行器（原样，不裁键、不改写）
- GWT-2 不合契约即拦（`validation_failed` + 违规点，执行器不被调用）
- GWT-3 空 schema 不校验 / 未声明属性不受约束 / 缺省 `{}`
- GWT-4 依赖链不继承 `inputs`
- GWT-5 留痕含 `inputs` 快照（SkillRun）

层间依赖（任务 GWT-5 的另一半——L1 不读 L2）由 `tests/test_layering.py` 兜底。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.storage import Store
from st_agent.l1.runner import RUN_PREFIX, SkillRunner
from st_agent.l1.runner.models import SkillRun
from st_agent.l1.skills import SkillRegistry

PASS = "correct horse battery staple"

PARENT = "sk_probe_parent"
CHILD = "sk_probe_child"

PORTFOLIO_SCHEMA = {
    "type": "object",
    "properties": {"portfolio": {"type": "object"}},
}


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


def _register(registry: SkillRegistry, base: str, **overrides) -> str:
    """注册一个探针 Skill（默认带 `portfolio` 对象声明）。"""
    seed: dict = {
        "name": base.replace("_", "-"),
        "description": "输入通道探针（T-L1-010.1 用例）",
        "input_schema": PORTFOLIO_SCHEMA,
        "output_schema": {"type": "object"},
        "parameters": (),
        "dependencies": (),
        "source": "user-built",
    }
    seed.update(overrides)
    return registry.register(base, version="1.0", **seed).skill_id


def _recorder(seen: dict, key: str):
    """执行器探针：记下本次收到的 `ctx.inputs`，恒 `ok`。"""

    def _fn(ctx, params):
        seen[key] = dict(ctx.inputs)
        return ResultEnvelope.ok({"probe": key})

    return _fn


def _runner_with(store: Store) -> tuple[SkillRunner, SkillRegistry]:
    """建注册表与 runner（执行器由用例自行登记）。"""
    registry = SkillRegistry(store)
    runner = SkillRunner(store, registry)
    return runner, registry


# ───────────────────────── GWT-1 合法输入到达执行器 ─────────────────────────


class TestGwt1Delivery:
    def test_valid_inputs_reach_executor(self, store):
        seen: dict = {}
        runner, registry = _runner_with(store)
        skill_id = _register(registry, PARENT)
        runner.register_executor(skill_id, _recorder(seen, PARENT))
        payload = {"portfolio": {"codes": ["sh.600001"], "source": "attention.holdings"}}
        out = runner.run(skill_id, inputs=payload)
        assert out.envelope.status == "ok"
        assert seen[PARENT] == payload

    def test_undeclared_keys_are_not_constrained(self, store):
        """01 §2 方言：`properties` 只约束已声明且已出现的属性。"""
        seen: dict = {}
        runner, registry = _runner_with(store)
        skill_id = _register(registry, PARENT)
        runner.register_executor(skill_id, _recorder(seen, PARENT))
        out = runner.run(skill_id, inputs={"portfolio": {}, "extra": 1})
        assert out.envelope.status == "ok"
        assert seen[PARENT] == {"portfolio": {}, "extra": 1}


# ───────────────────────── GWT-2 不合契约即拦 ─────────────────────────


class TestGwt2Validation:
    def test_type_mismatch_is_rejected(self, store):
        called: list = []
        runner, registry = _runner_with(store)
        skill_id = _register(registry, PARENT)
        runner.register_executor(skill_id, lambda ctx, p: called.append(1) or
                                 ResultEnvelope.ok({}))
        out = runner.run(skill_id, inputs={"portfolio": "不是对象"})
        assert out.envelope.status == "validation_failed"
        assert "input_schema" in out.envelope.reason
        assert "$.portfolio" in out.envelope.reason      # 违规点定位到字段
        assert called == []                              # 执行器不被调用

    def test_rejected_run_is_still_logged(self, store):
        runner, registry = _runner_with(store)
        skill_id = _register(registry, PARENT)
        runner.register_executor(skill_id, lambda ctx, p: ResultEnvelope.ok({}))
        out = runner.run(skill_id, inputs={"portfolio": 1})
        raw = json.loads(
            runner._store.get("execution_log",
                              f"{RUN_PREFIX}{out.skill_run_id}.json").decode("utf-8"))
        assert raw["envelope"]["status"] == "validation_failed"


# ───────────────────────── GWT-3 空 schema / 缺省 ─────────────────────────


class TestGwt3EmptySchema:
    def test_empty_schema_does_not_validate(self, store):
        seen: dict = {}
        runner, registry = _runner_with(store)
        skill_id = _register(registry, PARENT, input_schema={})
        runner.register_executor(skill_id, _recorder(seen, PARENT))
        out = runner.run(skill_id, inputs={"anything": 1})
        assert out.envelope.status == "ok"
        assert seen[PARENT] == {"anything": 1}

    def test_inputs_default_to_empty_dict(self, store):
        seen: dict = {}
        runner, registry = _runner_with(store)
        skill_id = _register(registry, PARENT)
        runner.register_executor(skill_id, _recorder(seen, PARENT))
        out = runner.run(skill_id)
        assert out.envelope.status == "ok"
        assert seen[PARENT] == {}


# ───────────────────────── GWT-4 依赖链不继承 ─────────────────────────


class TestGwt4Dependencies:
    def test_dependency_receives_empty_inputs(self, store):
        seen: dict = {}
        runner, registry = _runner_with(store)
        child_id = _register(registry, CHILD, input_schema={})
        parent_id = _register(registry, PARENT, dependencies=(child_id,))
        runner.register_executor(child_id, _recorder(seen, CHILD))
        runner.register_executor(parent_id, _recorder(seen, PARENT))
        payload = {"portfolio": {"codes": ["sh.600001"]}}
        out = runner.run(parent_id, inputs=payload)
        assert out.envelope.status == "ok"
        assert seen[PARENT] == payload
        assert seen[CHILD] == {}          # inputs 不沿依赖链下发


# ───────────────────────── GWT-5 留痕 ─────────────────────────


class TestGwt5Audit:
    def test_skill_run_records_inputs_snapshot(self, store):
        runner, registry = _runner_with(store)
        skill_id = _register(registry, PARENT)
        runner.register_executor(skill_id, lambda ctx, p: ResultEnvelope.ok({}))
        payload = {"portfolio": {"codes": ["sh.600001"]}}
        out = runner.run(skill_id, inputs=payload)
        raw = json.loads(
            runner._store.get("execution_log",
                              f"{RUN_PREFIX}{out.skill_run_id}.json").decode("utf-8"))
        assert raw["inputs"] == payload

    def test_legacy_record_without_inputs_still_parses(self, store):
        """A3：留痕模型扩字段不破坏既有读取面（旧记录缺 `inputs` 仍可解析）。"""
        runner, registry = _runner_with(store)
        skill_id = _register(registry, PARENT)
        runner.register_executor(skill_id, lambda ctx, p: ResultEnvelope.ok({}))
        out = runner.run(skill_id)
        raw = json.loads(
            runner._store.get("execution_log",
                              f"{RUN_PREFIX}{out.skill_run_id}.json").decode("utf-8"))
        raw.pop("inputs")
        assert SkillRun.model_validate(raw).inputs == {}
