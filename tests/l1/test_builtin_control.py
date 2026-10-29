"""T-AGT-011.1 / T-AGT-011.2 测试：循环控制原语（``plan_update`` / ``ask_user``）。

两叶的**声明体与直接调用**归 L1；循环内的拦截语义在 ``tests/l3/test_agent_plan.py`` /
``tests/l3/test_agent_ask.py`` 验。本文件只钉三件事：

- 两条原语登记进**同一个**注册面（工具目录可见，``source=builtin``）；
- ``plan_update``：合法计划归一化、空数组即清空、非法形态显式拒（不返回半份计划）；
- ``ask_user``：一问一参、超预算保留前 3 条并**显式记**截断项、零问项且无方向即拒。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.storage import Store
from st_agent.l1.builtin import BUILTIN_PRIMITIVES, install_builtins
from st_agent.l1.builtin.ask_user import ASK_USER, MAX_ASK_QUESTIONS, normalize_questions
from st_agent.l1.builtin.plan_update import (
    DEFAULT_MAX_PLAN_STEPS,
    PLAN_UPDATE,
    normalize_plan,
)
from st_agent.l1.runner import SkillRunner
from st_agent.l1.skills import SkillRegistry

PASS = "control-primitives-passphrase"


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def runtime(store: Store):
    registry = SkillRegistry(store)
    runner = SkillRunner(store, registry)
    install_builtins(registry, runner, primitives=(PLAN_UPDATE, ASK_USER))
    return registry, runner


# ───────────────────────── 登记面 ─────────────────────────


def test_control_primitives_are_registered_on_the_same_surface(runtime):
    registry, _runner = runtime
    catalog = {entry.name: entry for entry in registry.tool_catalog()}
    assert {"sk_plan_update", "sk_ask_user"} <= set(catalog)
    for base in ("sk_plan_update", "sk_ask_user"):
        descriptor = registry.get(f"{base}_v1.0")
        assert descriptor.source == "builtin"
        assert descriptor.permissions == ()           # 无文件 / 网络 / 命令出口


def test_seed_table_carries_only_supported_primitives():
    assert {seed.base for seed in BUILTIN_PRIMITIVES} >= {"sk_plan_update", "sk_ask_user"}


# ───────────────────────── plan_update 归一化 ─────────────────────────


def test_plan_normalizes_defaults_missing_status_to_pending():
    steps, error = normalize_plan([{"title": "查同步水位"}, {"title": "跑 SQL", "status": "done"}])
    assert error == ""
    assert steps == (
        {"title": "查同步水位", "status": "pending"},
        {"title": "跑 SQL", "status": "done"},
    )


def test_empty_plan_is_a_valid_explicit_clear():
    steps, error = normalize_plan([])
    assert (steps, error) == ((), "")


@pytest.mark.parametrize(
    "raw, fragment",
    [
        (None, "缺少 plan"),
        ("不是数组", "须为数组"),
        ([{"status": "done"}], "缺少 title"),
        ([{"title": "x", "status": "doing"}], "不在枚举"),
        ([{"title": "x"}] * (DEFAULT_MAX_PLAN_STEPS + 1), "超过上限"),
    ],
)
def test_invalid_plan_returns_reason_and_no_half_plan(raw, fragment):
    steps, error = normalize_plan(raw)
    assert steps == ()
    assert fragment in error


def test_plan_executor_returns_the_normalized_plan(runtime):
    _registry, runner = runtime
    run = runner.run("sk_plan_update_v1.0", {}, inputs={"plan": [{"title": "第一步"}]})
    assert run.envelope.status == "ok"
    assert run.envelope.data["cleared"] is False
    assert run.envelope.data["plan"] == [{"title": "第一步", "status": "pending"}]


def test_plan_executor_rejects_bad_shape(runtime):
    _registry, runner = runtime
    run = runner.run("sk_plan_update_v1.0", {}, inputs={"plan": [{"title": ""}]})
    assert run.envelope.status == "validation_failed"
    assert "title" in run.envelope.reason


# ───────────────────────── ask_user 归一化 ─────────────────────────


def test_ask_question_normalizes_one_parameter_per_question():
    ask = normalize_questions(
        [{"parameter": "window", "prompt": "窗口（天）", "choices": ["7", "30"], "default": 30}],
        ["近一周", "近一月"],
    )
    assert ask.ok
    assert ask.questions == (
        {"parameter": "window", "prompt": "窗口（天）", "choices": ["7", "30"], "default": 30},
    )
    assert ask.directions == ("近一周", "近一月")
    assert ask.note == ""


def test_ask_questions_over_budget_keep_first_three_and_note_the_rest():
    raw = [{"parameter": f"p{index}", "prompt": f"问 {index}"} for index in range(5)]
    ask = normalize_questions(raw)
    assert ask.ok
    assert len(ask.questions) == MAX_ASK_QUESTIONS
    assert "p3" in ask.note and "p4" in ask.note  # 被截断项显式记录，不静默丢


@pytest.mark.parametrize(
    "questions, directions, fragment",
    [
        ([{"parameter": "x"}], None, "缺少 parameter 或 prompt"),
        ([{"prompt": "x"}], None, "缺少 parameter 或 prompt"),
        (None, None, "既没有有效问项也没有方向候选"),
        ({"parameter": "x"}, None, "须为数组"),
    ],
)
def test_invalid_ask_returns_reason(questions, directions, fragment):
    ask = normalize_questions(questions, directions)
    assert not ask.ok
    assert fragment in ask.error


def test_ask_executor_returns_structured_questions(runtime):
    _registry, runner = runtime
    run = runner.run("sk_ask_user_v1.0", {}, inputs={"questions": [
        {"parameter": "window", "prompt": "窗口（天）"},
    ]})
    assert run.envelope.status == "ok"
    assert run.envelope.data["questions"][0]["parameter"] == "window"
