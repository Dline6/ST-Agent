"""逐步授权闸门（T-AGT-005.2；[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 的 ② 过闸门）。

[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 的四段循环里，**每个待执行动作先经授权闸门**
（[01 §7](../../../../docs/技术架构-v2/01-平台共享契约.md) 的运行期授权条目），**仅 `autonomous-ok` 才自主执行**。
本模块交付闸门本体——它是**L3 侧的消费方**：判据（清单风险类 × 运行期档位）归 L6
（[`RuntimeAuthorization`](../../l6/runtime_authorization.py)，[`T-AGT-005.1`](../../../项目管理/tasks/T-AGT-005.1-运行期授权档位与共享清单.md)），
经**注入的鸭子端口**取用——L3 向下不依赖 L6（`LAYER_ORDER` 为 `l3` < `l6`），**不 import `st_agent.l6`**
（[铁律 7](../../../项目管理/工程宪法.md)，同 [`DispatchBus`](../dispatch/bus.py) 的 `deliberations` 先例）。

三条口径：

- **只做派生与适配，不做政策**——把 `(skill_id, tool_name, arguments)` 适配成 [01 §7](../../../../docs/技术架构-v2/01-平台共享契约.md)
  的**动作标识** `skill.<base>`（[`ToolEntry.name`](../../l1/skills/tool_catalog.py) 即 base，故同 base 升版
  沿用同一标识），再把判据端口的结论转成循环认的 :class:`~st_agent.l3.runtime.loop.GateDecision`。
  「某动作属哪一风险类、当前档位能否自主」的语义**不在本模块**。
- **一律 fail-closed**（[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md) / [01 §7](../../../../docs/技术架构-v2/01-平台共享契约.md)）——
  未注入判据端口 / 端口没有该判据方法 / 调用抛错 / 返回结论不可识别，**四种情形一律 `denied` 并给出成因**；
  **缺省不注入即一步都不执行**（循环侧的 GWT-3 已钉死这条链）。
- **运行期动作不写任何留痕**——闸门是**纯判断**：不落盘、不记 `ChangeRecord`（每次工具调用都立变更
  记录会把变更历史淹掉，[08 §5](../../../../docs/技术架构-v2/08-L6-反思演进.md)）。执行留痕归 L1
  `SkillRunner`，循环留痕归 [`.report`](report.py)。
"""

from __future__ import annotations

from typing import Any

from st_agent.l3.runtime.loop import GATE_VERDICTS, GateDecision

__all__ = [
    "AGENT_DECISION_METHOD",
    "SKILL_ACTION_PREFIX",
    "ActionGate",
    "action_id_of",
]

AGENT_DECISION_METHOD = "decide"
"""判据端口的**方法名**（供组合根注入 L6 的授权面）。

与 [`RuntimeAuthorization.decide`](../../l6/runtime_authorization.py) 同名——两处是 [01 §7](../../../../docs/技术架构-v2/01-平台共享契约.md)
键空间同一件事的两侧（L6 判、L3 取），由 `tests/l3/test_agent_gate.py` 的跨层用例钉住。
"""

SKILL_ACTION_PREFIX = "skill."
"""工具调用类动作标识的前缀（[01 §7](../../../../docs/技术架构-v2/01-平台共享契约.md) 的键空间）。

L6 侧另有一份同值常量（[`skill_action_id`](../../l6/runtime_authorization.py)）——**判据的键**与
**键的构造**分居两层，两处的相乘性同样由 `tests/l3/test_agent_gate.py` 跨层钉住（同
`AGENT_TERMINATION_LABELS` 对循环终止原因的「枚举副本 + 测试钉住」做法）。
"""


def action_id_of(tool_name: str) -> str:
    """一次工具调用的**动作标识**（``skill.<tool_name>``）。

    `tool_name` 即 ``skill_id`` 版本剥离后的能力身份（[`ToolEntry.name`](../../l1/skills/tool_catalog.py)），
    故清单**不必按版本逐条登记**（[01 §2](../../../../docs/技术架构-v2/01-平台共享契约.md) 的版本折叠口径）。
    """
    return f"{SKILL_ACTION_PREFIX}{tool_name}"


class ActionGate:
    """逐步授权闸门（[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）；交 `run_agent_loop(gate=…)`。

    :param decisions: 注入的**判据端口**（鸭子类型 ``decide(action_id) -> ActionDecision | 结论串``，
        如 L6 的 [`RuntimeAuthorization`](../../l6/runtime_authorization.py)）；
        **缺省 ``None`` ⇒ 一律 `denied`**（缺省不放行，循环侧的 `gate_absent` 之外再兜一层）
    :param decision_method: 判据端口的方法名（缺省 :data:`AGENT_DECISION_METHOD`）

    **不持任何状态**——同一输入恒得同一结论，故每一步都是可复算的独立判断。
    """

    def __init__(
        self, decisions: Any = None, *, decision_method: str = AGENT_DECISION_METHOD
    ) -> None:
        self._decisions = decisions
        self._decision_method = decision_method

    def authorize(
        self, *, skill_id: str, tool_name: str, arguments: dict[str, Any]
    ) -> GateDecision:
        """问一次「该动作在当前运行期档位下可否自主」（循环在**每步执行之前**调用）。

        :param skill_id: 本次执行的**实际目标**（该 base 的最高版本；本闸门只转述给判据的成因，
            动作标识取 base——版本不参与判据）
        :param tool_name: 模型调用的工具名（＝base）
        :param arguments: 模型给出的调用参数（**不参与判据**，随成因转述，便于交还时说明）
        :returns: :class:`~st_agent.l3.runtime.loop.GateDecision`——**除 `autonomous-ok` 外一律不自主**

        四种 fail-closed 情形（无端口 / 无方法 / 抛错 / 结论不可识别）都返回 `denied` 并点名成因，
        **不把「判不了」当成「可以」**。
        """
        action_id = action_id_of(tool_name)
        if self._decisions is None:
            return GateDecision(
                verdict="denied",
                reason=(
                    "未注入运行期授权判据面（L6 的 `agent.authorization` 档位与共享清单）"
                    "——按 fail-closed 拒绝执行（01 §7）"
                ),
            )
        decide = getattr(self._decisions, self._decision_method, None)
        if decide is None:
            return GateDecision(
                verdict="denied",
                reason=(
                    f"注入的判据面没有 {self._decision_method!r} 方法（须为 "
                    "`decide(action_id) -> 结论`）——按 fail-closed 拒绝执行"
                ),
            )
        try:
            raw = decide(action_id)
        except Exception as exc:  # 判据面读不出（清单/档位损坏）⇒ 不猜、不放行
            return GateDecision(
                verdict="denied",
                reason=(
                    f"运行期授权判据读不出（{type(exc).__name__}: {exc}）"
                    "——按 fail-closed 拒绝执行"
                ),
            )
        return _as_gate_decision(raw, action_id)


def _as_gate_decision(raw: Any, action_id: str) -> GateDecision:
    """判据端口的返回 → 循环认的 :class:`GateDecision`（不可识别 ⇒ `denied`）。

    认两种形态（**鸭子类型**，故 L3 不必知道 L6 的类型）：结论串（∈ :data:`GATE_VERDICTS`），
    或带 ``verdict`` / ``reason`` 两属性的对象（如 L6 的 `ActionDecision`）。
    """
    if isinstance(raw, GateDecision):
        return raw
    if isinstance(raw, str) and raw in GATE_VERDICTS:
        return GateDecision(verdict=raw)
    verdict = getattr(raw, "verdict", None)
    if isinstance(verdict, str) and verdict in GATE_VERDICTS:
        return GateDecision(verdict=verdict, reason=str(getattr(raw, "reason", "") or ""))
    return GateDecision(
        verdict="denied",
        reason=(
            f"授权判据面就动作 {action_id!r} 返回了不可识别的结论"
            f"（{type(raw).__name__}）——按 fail-closed 拒绝执行"
        ),
    )
