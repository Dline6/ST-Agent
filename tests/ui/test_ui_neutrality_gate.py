"""渲染前的中性化门：**两向都钉**（[01 §6] 执行点 2；[01 §12] 的 `text_kinds` 分栏）。

只有两向都钉住，「逐槽显式标注」才算真的被用起来：`generated` 漏检 → 违规文案出网；
`data` 误检 → 用户原话被误判（[D-053]）。
"""

from __future__ import annotations

import pytest

from st_agent.contracts.ui_description import UiDescription, new_description_id
from st_agent.ui.neutrality_gate import NeutralityGate

RULES_SAY = "我认为这只标的值得关注"  # 命中 first_person（§6 词表）
NEUTRAL = "价格走势平稳"


def _description(*, title: str | None = None, slots: dict, kinds: dict) -> UiDescription:
    return UiDescription(
        description_id=new_description_id(),
        component_type="report_card",
        title=title,
        slots=slots,
        text_kinds=kinds,
    )


@pytest.fixture
def gate() -> NeutralityGate:
    return NeutralityGate()


def test_clean_description_passes(gate) -> None:
    verdict = gate.check(
        _description(slots={"sections": [{"lines": [NEUTRAL]}]}, kinds={"sections": "generated"})
    )
    assert verdict.passed
    assert verdict.reason == ""


def test_generated_slot_hits_are_blocked(gate) -> None:
    """§6 执行点 2：命中即**阻断渲染**。"""
    verdict = gate.check(
        _description(slots={"sections": [{"lines": [RULES_SAY]}]}, kinds={"sections": "generated"})
    )
    assert not verdict.passed
    assert verdict.hits == ("slots.sections",)
    assert verdict.findings


def test_data_slot_hits_are_allowed(gate) -> None:
    """同一措辞出现在 `data` 槽（用户原话 / 记忆本体）→ **放行**（[D-053]）。"""
    verdict = gate.check(
        _description(slots={"sections": [{"lines": [RULES_SAY]}]}, kinds={"sections": "data"})
    )
    assert verdict.passed


def test_title_is_always_checked(gate) -> None:
    """§12：`title` 恒为生成文案，故一律受检（不必经 `text_kinds`）。"""
    verdict = gate.check(
        _description(title=RULES_SAY, slots={"sections": []}, kinds={"sections": "data"})
    )
    assert not verdict.passed
    assert verdict.hits == ("title",)


def test_nested_generated_values_are_scanned(gate) -> None:
    """生成文案可能嵌在容器里——只查顶层字符串是不够的。"""
    verdict = gate.check(
        _description(
            slots={"sections": [{"title": NEUTRAL, "lines": [NEUTRAL, RULES_SAY]}]},
            kinds={"sections": "generated"},
        )
    )
    assert not verdict.passed


def test_mixed_kinds_are_split_by_slot(gate) -> None:
    """同一份描述里两个槽分属两栏：只有 generated 那个受检。"""
    verdict = gate.check(
        _description(
            slots={"generated_bit": [RULES_SAY], "data_bit": [RULES_SAY]},
            kinds={"generated_bit": "generated", "data_bit": "data"},
        )
    )
    assert not verdict.passed
    assert verdict.hits == ("slots.generated_bit",)


def test_reason_does_not_echo_the_offending_text(gate) -> None:
    """留痕只记位置与命中数——把违规原文抄进信封等于让它换个地方继续传播。"""
    verdict = gate.check(
        _description(slots={"sections": [{"lines": [RULES_SAY]}]}, kinds={"sections": "generated"})
    )
    assert "我" not in verdict.reason
    assert RULES_SAY not in verdict.reason
    assert "slots.sections" in verdict.reason


def test_gate_has_no_off_switch() -> None:
    """§6：校验点**不可配置关闭**——构造参数只有规则库，没有 bypass / enabled。"""
    import inspect

    parameters = inspect.signature(NeutralityGate.__init__).parameters
    assert set(parameters) == {"self", "guard"}
