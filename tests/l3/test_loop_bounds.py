"""T-AGT-007 测试：循环上界开放为配置项（[05 §10](../../../docs/技术架构-v2/05-L3-对话主入口.md) / [01 §7](../../../docs/技术架构-v2/01-平台共享契约.md)）。

GWT-1..5 逐条。**GWT-5 用真** ``SkillRunner`` + 真 ``Store`` + 真官方 Pack 驱动**真**
``run_agent_loop``——「端用户改的值真的落到循环」是在真流水线上验的，不是替身自证。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from st_agent.contracts.registry_types import ChangeRecord
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.llm import ToolCall
from st_agent.l1.registry.errors import RegistryValidationError
from st_agent.l1.registry.facade import ConfigRegistryFacade
from st_agent.l1.runner import SkillRunner
from st_agent.l1.skills import SkillRegistry, ensure_official_pack
from st_agent.l3.errors import AgentRuntimeValidationError
from st_agent.l3.runtime import (
    DEFAULT_MAX_LLM_CALLS,
    DEFAULT_MAX_STEPS,
    MAX_LLM_CALLS_CEILING,
    MAX_LLM_CALLS_CONFIG_ID,
    MAX_STEPS_CEILING,
    MAX_STEPS_CONFIG_ID,
    GateDecision,
    LoopBounds,
    TurnResult,
    investigate_family,
    run_agent_loop,
)

SK_A = "sk_data_aggregate"
TASK = "查证 sh.600000 的公告与舆情"
EP = "ep_test"
NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


# ───────────────────────── 夹具与替身 ─────────────────────────


@pytest.fixture()
def skills(store) -> SkillRegistry:
    reg = SkillRegistry(store)
    ensure_official_pack(reg)
    return reg


@pytest.fixture()
def runner(store, skills) -> SkillRunner:
    out = SkillRunner(store, skills)
    out.register_executor(f"{SK_A}_v1.0", lambda ctx, params: ResultEnvelope.ok({"cards": []}))
    return out


@pytest.fixture()
def tools(skills: SkillRegistry):
    return skills.tool_catalog()


class FakeProtocol:
    """同 `ToolCallProtocol` 接口的最小替身（脚本用尽即给「结束」轮）。"""

    def __init__(self, replies) -> None:
        self._replies = list(replies)

    def tool_calls_supported(self) -> bool:
        return True

    def invoke_turn(self, context, *, initiator: str, purpose: str) -> TurnResult:
        return self._replies.pop(0) if self._replies else TurnResult(text="（脚本已尽）")


class FakeGate:
    """恒自主放行的授权端口替身（循环的上界判定与闸门无关）。"""

    def authorize(self, *, skill_id: str, tool_name: str, arguments: dict) -> GateDecision:
        return GateDecision(verdict="autonomous-ok")


def _call(name: str) -> TurnResult:
    return TurnResult(tool_calls=(ToolCall(call_id="call_1", name=name, arguments={}),))


def _facade(store) -> tuple[ConfigRegistryFacade, LoopBounds]:
    bounds = LoopBounds(store, now=lambda: NOW)
    facade = ConfigRegistryFacade(store)
    facade.register_family(investigate_family(bounds))
    return facade, bounds


# ───────────────────────── GWT-1 · 登记面可见且双通道同源 ─────────────────────────


def test_gwt1_entries_registered_with_both_channels(store) -> None:
    facade, _ = _facade(store)
    entry = facade.entry(MAX_STEPS_CONFIG_ID)
    assert entry is not None
    assert entry.config_id == MAX_STEPS_CONFIG_ID
    assert entry.scope == "global"
    assert entry.default == DEFAULT_MAX_STEPS                       # 缺省＝循环入口缺省
    assert entry.value_schema["minimum"] == 1
    assert entry.value_schema["maximum"] == MAX_STEPS_CEILING
    assert entry.change_policy.requires_confirmation is True        # 改预算需确认（A5）
    # 双通道同源：对话描述 + 面板规格都在同一条目上
    assert entry.description_for_chat
    assert entry.panel_form_spec.widget == "number"
    assert entry.panel_form_spec.label
    # 两条目都在全局列举里
    ids = {e.config_id for e in facade.list("global")}
    assert {MAX_STEPS_CONFIG_ID, MAX_LLM_CALLS_CONFIG_ID} <= ids


# ───────────────────────── GWT-2 · 落值生效 + 留痕 ─────────────────────────


def test_gwt2_set_persists_and_records_change(store) -> None:
    facade, bounds = _facade(store)
    change = facade.set(MAX_STEPS_CONFIG_ID, 5, trace_id="tr_1")
    assert isinstance(change, ChangeRecord)
    assert change.config_id == MAX_STEPS_CONFIG_ID
    assert change.old_value == DEFAULT_MAX_STEPS and change.new_value == 5
    assert change.trace_ref == "tr_1" and change.change_id
    assert bounds.steps() == 5
    # 换一个句柄（同一 store）读回 → 真落盘
    assert LoopBounds(store).steps() == 5
    # 值未变 → 不落盘、不留痕（01 §7 既有一口径）
    assert facade.set(MAX_STEPS_CONFIG_ID, 5) is None
    # 留痕可经门面的跨族读面看到
    assert [c.config_id for c in facade.changes()] == [MAX_STEPS_CONFIG_ID]


# ───────────────────────── GWT-3 · 缺省回落 ─────────────────────────


def test_gwt3_missing_value_falls_back_to_default(store) -> None:
    facade, bounds = _facade(store)
    assert bounds.steps() == DEFAULT_MAX_STEPS
    assert bounds.llm_calls() == DEFAULT_MAX_LLM_CALLS
    # 条目的 default 携当前生效值（未落值 → 缺省）
    assert facade.entry(MAX_STEPS_CONFIG_ID).default == DEFAULT_MAX_STEPS
    assert facade.entry(MAX_LLM_CALLS_CONFIG_ID).default == DEFAULT_MAX_LLM_CALLS
    assert facade.changes() == ()                                   # 未落值即无留痕


# ───────────────────────── GWT-4 · 非法取值 fail-closed ─────────────────────────


@pytest.mark.parametrize(
    "bad", [0, -1, True, 1.5, "5", MAX_STEPS_CEILING + 1, None]
)
def test_gwt4_out_of_range_rejected_and_not_persisted(store, bad) -> None:
    facade, bounds = _facade(store)
    with pytest.raises(RegistryValidationError):
        facade.set(MAX_STEPS_CONFIG_ID, bad)
    assert bounds.steps() == DEFAULT_MAX_STEPS                      # 不静默截断 / 回退
    assert facade.changes() == ()                                   # 未落盘即无留痕


def test_gwt4_owner_raises_its_own_error_type(store) -> None:
    """owner 侧的取值域门抛 L3 自己的错误类型（适配器再统一为门面词汇）。"""
    with pytest.raises(AgentRuntimeValidationError):
        LoopBounds(store).set_llm_calls(MAX_LLM_CALLS_CEILING + 1)


def test_gwt4_unknown_id_and_foreign_prefix_are_rejected(store) -> None:
    facade, bounds = _facade(store)
    with pytest.raises(RegistryValidationError):
        facade.set("investigate.unknown", 1)                        # 族内未知条目
    with pytest.raises(RegistryValidationError):
        investigate_family(bounds).apply("something.else", 1)      # 前缀外 → 拒
    assert facade.entry("investigate.unknown") is None
    assert investigate_family(bounds).entry("something.else") is None


def test_gwt4_corrupt_file_raises_explicitly(store) -> None:
    store.put("config", "investigate/max_steps.json", b"{not json")
    with pytest.raises(AgentRuntimeValidationError):
        LoopBounds(store).steps()


# ───────────────────────── GWT-5 · 端用户改的值真的落到循环 ─────────────────────────


def test_gwt5_value_drives_the_real_loop(store, runner, tools) -> None:
    facade, _ = _facade(store)
    facade.set(MAX_STEPS_CONFIG_ID, 2)                              # 端用户把步数上界改成 2
    bounds = LoopBounds(store)
    outcome = run_agent_loop(
        task=TASK, endpoint_id=EP,
        protocol=FakeProtocol([_call(SK_A)] * 4),                   # 模型永不给出结束
        runner=runner, tools=tools, gate=FakeGate(),
        max_steps=bounds.steps(), max_llm_calls=bounds.llm_calls(),
    )
    assert outcome.termination == "step_limit"
    assert outcome.at_bound is True
    assert outcome.max_steps == 2                                   # 生效的是配置项的值
    assert len(outcome.steps) == 2
    assert [s.envelope.status for s in outcome.steps] == ["ok", "ok"]
