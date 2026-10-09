---
id: T-AGT-005.2
parent: T-AGT-005
title: 逐步闸门与终止交还
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M5
depends_on: [T-AGT-005.1, T-AGT-004.2]
status: done
decisions: [D-090, D-091, D-097]
verify: L3 侧新用例 17 例（GWT-6..8 + 闸门三态 + 四类 fail-closed + 跨层键空间/词表相乘；执行面真 SkillRunner + 真 Store + 真官方 Pack、判据面真 RuntimeAuthorization）· 范围 1302 passed（248.46s）· 全量 3786 passed / 0 failed / 14 deselected（765.95s）· verify_docs --strict 0 断链 · 未触发联网面 · 见执行日志 [T-AGT-005]
---

# T-AGT-005.2 · 逐步闸门与终止交还

## 目标

兑现 [D-090](../../../决策日志.md) T-3 的 **L3 侧**——闸门的消费方（[05 §10 授权闸门与交还](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。两件：

1. **逐步闸门**：接 [`.2`](T-AGT-004.2-循环驱动、协议端口与上界.md) 的**授权端口**（`AGENT_GATE_METHOD = "authorize"`，**每步执行之前**问一次），实现 `ActionGate`——把 `(skill_id, tool_name, arguments)` 适配成**动作标识** `skill.<base>`（[`ToolEntry.name`](../../../../src/st_agent/l1/skills/tool_catalog.py) 即为 base），再经**注入的鸭子端口**取 [`.1`](T-AGT-005.1-运行期授权档位与共享清单.md) 的判据，转成循环认的 `GateDecision`。**闸门本体不含风险类语义**（那是 L6 的判据面）——它只做派生、适配与 **fail-closed**。
2. **终止交还**（[D-090](../../../决策日志.md) ⑦）：碰到**非** `autonomous-ok` 的动作即**终止循环并交还用户**——**不挂起、不恢复**（[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。交还须**显式**：待确认的那一步（工具 + 参数）+ 闸门给出的原因 + 已收集的证据（为零时**明说为零**，不留「像是有结果」的空壳），且状态**非 `ok`**——**不把半程结果呈现为已完成**。

## 验收标准（Given-When-Then）

- **GWT-6 · 遇需协作即终止交还**：Given 循环**首步**就是 `collaborative-required` 的动作（注入真 `RuntimeAuthorization` 判据面），When 驱动，Then **一步不执行**、循环终止于 `needs_confirmation`，且产出**显式**说明「下一步需确认」+ 待执行的那一步（工具名与参数）+ 闸门原因 + 已收集证据（此时为零，**明说零**）。
- **GWT-7 · 交还不伪装完成**：Given GWT-6 的情形，When 取产出，Then 状态**非 `ok`**（`empty`）；留痕的 `termination` 记 `needs_confirmation`、被拦下的那一步**不落** `steps`（未执行的步绝不出现在「已执行动作」里）。
- **GWT-8 · 不淹变更历史**：Given 一次 N 步自主循环跑完（运行期档 `autonomous` + 动作落 `autonomous-ok` 范围），When 读变更历史（`facade.changes()` 的 `execution_log/*-change/` 与 [`EvolutionChangeFlow`](../../../../src/st_agent/l6/change.py) 的 `reflection/changes/` 两处），Then **两处都无新增 `ChangeRecord`**——工具调用不进变更流（[08 §5](../../../../docs/技术架构-v2/08-L6-反思演进.md)）。

## 接口面

- **输入**：
  - **本任务 `.1` 交付的判据面**：[`RuntimeAuthorization.decide(action_id) -> ActionDecision`](../../../../src/st_agent/l6/runtime_authorization.py) 与 `ActionDecision.verdict`——**鸭子端口**，本叶只按 `AGENT_DECISION_METHOD = "decide"` 取用，**不 import `st_agent.l6`**（`LAYER_ORDER` 为 `l3` < `l6`，[铁律 7](../../../工程宪法.md)）。
  - [`T-AGT-004.2`](T-AGT-004.2-循环驱动、协议端口与上界.md) 的授权端口与调用点：`AGENT_GATE_METHOD = "authorize"` · `GateDecision` / `GateVerdict` / `GATE_VERDICTS` · `run_agent_loop(gate=…)` 的调用形如 `gate.authorize(skill_id=…, tool_name=…, arguments=…)`（[`src/st_agent/l3/runtime/loop.py`](../../../../src/st_agent/l3/runtime/loop.py)）。
  - [`T-AGT-004.3`](T-AGT-004.3-产出接入、留痕与失败语义.md) 的产出面：`LoopOutcome`（含 `pending` / `gate_reason`）· [`build_agent_output`](../../../../src/st_agent/l3/runtime/report.py) · `conclude_agent_run`。
  - [`T-AGT-003`](T-AGT-003-工具目录读面.md) 的 [`ToolEntry.name`](../../../../src/st_agent/l1/skills/tool_catalog.py)（＝`skill_id` 的 base，动作标识后缀的来源）。
  - 契约口径：[05 §10 授权闸门与交还](../../../../docs/技术架构-v2/05-L3-对话主入口.md) · [01 §7 运行期动作的授权档位与风险分级](../../../../docs/技术架构-v2/01-平台共享契约.md)。
- **输出**：
  - **新增** [`src/st_agent/l3/runtime/gate.py`](../../../../src/st_agent/l3/runtime/gate.py)：`ActionGate`（`authorize(*, skill_id, tool_name, arguments) -> GateDecision`）+ `AGENT_DECISION_METHOD = "decide"` + 动作标识前缀常量；端口缺失 / 方法缺失 / 返回不可识别一律 `denied`（fail-closed，同循环既有的 `_gate_decision` 取向）。
  - **只增** [`src/st_agent/l3/runtime/report.py`](../../../../src/st_agent/l3/runtime/report.py)：零步**交还**的 reason 组装（`needs_confirmation` / `denied` 时把待确认动作、闸门原因、已收集证据数写进 `reason`）——**状态映射不变**（仍 `empty`，T-AGT-004.3 的 `NO_STEP_STATUS` 断言零回归）。
  - **只增** [`src/st_agent/l3/runtime/__init__.py`](../../../../src/st_agent/l3/runtime/__init__.py)：导出 `ActionGate` / `AGENT_DECISION_METHOD`。
  - 测试：**新增** `tests/l3/test_agent_gate.py`（GWT-6/7/8 + 闸门三态 + 端口缺失/坏返回的 fail-closed + 跨层注入面），执行面一律**真** `SkillRunner` + 真 `Store` + 真 `RuntimeAuthorization`。

## 可关闭的遗留

- 无（2026-10-08 开工逐册读未闭区复核：`L3` 册仅 `A3`（自主查证循环的上界是否开放端用户可调）未闭，归属**人决**、本叶**不动上界**；全册 grep `闸门` / 动作标识 / 运行期授权**零命中**）

## 假设与前提

- **A1 · 闸门落在 L3 侧、判据经鸭子端口注入**——前提与影响同父 [T-AGT-005 A3](T-AGT-005-动作闸门接入.md)（[铁律 7](../../../工程宪法.md)：`l3` < `l6`，写入 import 当场判失败）。
  **验证方式**：GWT-6 经注入面成立；`tests/test_layering.py` 全绿且 `l3/runtime/**` **无** `st_agent.l6` import（AST 断言）。
- **A2 · 动作标识由 `tool_name` 直接派生**（本次 ④ 对齐拍定）——前提：`ToolEntry.name` **就是** base（[T-AGT-003](T-AGT-003-工具目录读面.md) 的版本折叠口径），故一次工具调用的动作标识是 `skill.<tool_name>`，**不需要**从 `skill_id` 再剥版本。
  **若错的影响**：若目录粒度改为逐版本投影，动作标识会随版本漂移（同一能力因升版换标识），清单需按版本逐条登记——**返工面＝`ActionGate` 的派生一处 + 清单登记面**。
  **验证方式**：GWT-6 的断言核对闸门**实际**取到的动作标识串；另以「同 base 两个已注册版本」的目录条目断言派生**同一**标识（与 [T-AGT-003](T-AGT-003-工具目录读面.md) GWT-2 同源）。
- **A3 · 交还保持 `empty` 状态、只把 reason 写显式**（本次 ④ 对齐拍定）——前提：GWT-7 只要求「状态**非 `ok`**」（`empty` 已满足），而 T-AGT-004.3 已 `done` 的终止原因映射表（零步 `needs_confirmation` / `denied` → `empty`）被 `tests/l3/test_agent_report.py` 钉住；改状态会改写既有交付面。
  **若错的影响**：若日后要求交还也出结构化描述（带 `pending` / `gate_reason` 的表），返工面 = `build_agent_output` 的映射表一行 + T-AGT-004.3 的 `NO_STEP_STATUS` 断言一处（属**有据可依的改写**，非静默变更）。
  **验证方式**：GWT-6 断言 reason 含三项要素；GWT-7 断言状态非 `ok`；`tests/l3/test_agent_report.py` 的 `NO_STEP_STATUS` 用例**不改**即全绿（零回归即证映射未动）。

## 涉及契约

- [05 §10 授权闸门与交还](../../../../docs/技术架构-v2/05-L3-对话主入口.md)（非 `autonomous-ok` 即终止交还 · 交还须显式 · 不挂起不恢复）
- [01 §7 运行期动作的授权档位与风险分级](../../../../docs/技术架构-v2/01-平台共享契约.md)（动作标识键空间）
- [08 §5](../../../../docs/技术架构-v2/08-L6-反思演进.md)（运行期工具调用不进变更流）
- [铁律 7](../../../工程宪法.md)（鸭子端口注入，不 import L6）

## 参考

- [D-090](../../../决策日志.md)（⑦ 终止交还）· [D-091](../../../决策日志.md)（契约落点 ④）· [D-097](../../../决策日志.md)（本批拆分与四处口径）
- 上游：[`.1`](T-AGT-005.1-运行期授权档位与共享清单.md) · [`T-AGT-004.2`](T-AGT-004.2-循环驱动、协议端口与上界.md) · [`T-AGT-004.3`](T-AGT-004.3-产出接入、留痕与失败语义.md)
- 下游：[`T-AGT-006`](T-AGT-006-第七类意图investigate与派发去向接线.md) · [`T-INT-006`](T-INT-006-M5集成关卡受控自主闭环.md)

## 备注

**交还的「不挂起」是有意的**——本期**不做**循环的挂起与恢复（那要异步 + 状态持久化，[D-090](../../../决策日志.md) ⑦）；故闸门拦下的那一步**只交还、不排队**：用户确认后是**新的一次**派发，不复用本次的循环状态。
