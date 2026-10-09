---
id: T-AGT-006
parent: null
title: 第七类意图 investigate 与派发去向接线
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §3.1](../../../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M5
depends_on: [T-AGT-004.3, T-AGT-005]
status: done
decisions: [D-090, D-091, D-098]
verify: 范围套件（tests/contracts + test_layering + test_tools + tests/integration + tests/l3）1033 passed（239.15s）+ 补跑 tests/l6 527 passed（62.90s）；新用例 11 例钉 GWT-1..7（Fake 循环端口 / 缺省 fail-closed 点名 T-INT-006 / 未确认不派发 / 确认卡恰好一条目）；verify_docs --strict 检查 1–9 全过、0 断链。执行日志 [T-AGT-006]
---

# T-AGT-006 · 第七类意图 `investigate` 与派发去向接线

## 目标

把循环**接进对话主入口**——新增第七类意图并接通它的派发去向，使循环可被用户一次性触发。

1. **意图枚举**：`INTENT_KINDS` 与 `IntentKind` 增 `investigate`（[05 §3.1](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 的机器可读副本）。
2. **派发去向**：总线路由表增 `investigate` 去向，经**组合根注入的循环端口**触发 [`T-AGT-004`](T-AGT-004-循环运行时.md) 的循环（[05 §4](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 的接入态）。
3. **确认卡承载**：`INTENT_TARGETS` 增该意图的派发目标描述；**无意图级参数**（[05 §3.2](../../../../docs/技术架构-v2/05-L3-对话主入口.md)：循环上界是系统预算、非用户可调项），故确认卡只有「意图」一条目。
4. **未注入即 fail-closed**：循环端口缺省不注入时，该去向按 [05 §4](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 的「未接入去向」口径回 `unavailable` + 点名，**不伪造执行**。

## 验收标准（Given-When-Then）

- **GWT-1 · 枚举扩到七类**：Given 意图枚举，When 读，Then 含 `investigate`，且 `RouteSpec` 路由表与之**逐项对应**（既有的集合断言不破）。
- **GWT-2 · 确认卡可产出**：Given 一次收敛为 `investigate` 的意图，When 出确认卡，Then 卡**至少一条目**且抬头计数正确——**不因无参数而构造失败**。
- **GWT-3 · 派发经注入端口**：Given 注入一个 Fake 循环端口，When 派发已确认的 `investigate` 卡，Then 端口被调用且产出信封**原样透出**（不重包、不改 status、不吞 reason）。
- **GWT-4 · 缺省 fail-closed**：Given **未注入**循环端口，When 派发，Then `unavailable` + `reason` **点名归属任务**，**不伪造执行**。
- **GWT-5 · 未确认不派发**：Given 一张**未确认**的 `investigate` 卡，When 派发，Then `validation_failed`（既有总线口径不因新去向放宽）。
- **GWT-6 · 既有六类行为逐字节不变**：Given 既有六类的任一去向，When 跑既有用例，Then 全绿且**行为无变化**。
- **GWT-7 · 快捷指令可引用**：Given 快捷指令的意图模板枚举，When 读，Then 含 `investigate`（[05 §8](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 的枚举与 §3.1 同源）。

## 接口面

- **输入**：
  - [`T-AGT-004.3`](T-AGT-004.3-产出接入、留痕与失败语义.md) 的循环**产出面**——`conclude_agent_run(store, outcome) -> AgentRunReport`，其 `.envelope` 即该去向要透出的信封（`src/st_agent/l3/runtime/report.py`）。
  - [`T-AGT-005`](T-AGT-005-动作闸门接入.md) 的闸门（`RuntimeAuthorization.decide` / `ActionGate.authorize`）——**循环内部生效**，本任务不重复接线，仅经端口间接消费。
  - 既有 L3 面：[`DispatchBus`](../../../../src/st_agent/l3/dispatch/bus.py)（路由表 + 注入端口范式，构造形参见 `runner` / `deliberations` / `trainings`）· [`IntentProtocol`](../../../../src/st_agent/l3/intent/protocol.py)（`_intent_item` 直取 `INTENT_TARGETS[draft.intent]`）· [`CommandRegistry`](../../../../src/st_agent/l3/commands/registry.py)（`INTENT_KINDS` / `IntentKind`）。
  - 契约口径：[05 §3.1 / §3.2 / §4 / §8 / §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md) · [01 §5](../../../../docs/技术架构-v2/01-平台共享契约.md) · [01 §7](../../../../docs/技术架构-v2/01-平台共享契约.md)。
- **输出**（本任务交付的公共 API 与落盘位置）：
  - `src/st_agent/l3/commands/registry.py`——`INTENT_KINDS` 追加 `"investigate"`（尾位，随 05 §3.1 表序）+ `IntentKind` 扩为七类 Literal。
  - `src/st_agent/l3/dispatch/bus.py`——`INVESTIGATE_ABSENT_REASON` 常量 · `ROUTE_SPECS` 追加 `investigate` 去向（`wired=True`，尾位）· `_dispatch_investigate` · `DispatchBus(investigations=…)` 构造注入位 · `DispatchOutcome.investigation` 载荷槽。
  - `src/st_agent/l3/intent/protocol.py`——`INTENT_TARGETS` 追加 `investigate` 条目（尾位，与 `INTENT_KINDS` 同序）；`INTENT_PARAM_SPECS` **不加**（假设 A2）。
  - `src/st_agent/app.py`——`_build_l3(..., investigations=None)` 形参并透传 `DispatchBus`（＝「把循环注入总线」的注射点）；**不**新增 M5 组合根、**不**改 `__main__.py`（假设 A6，实际装配归 [`T-INT-006`](T-INT-006-M5集成关卡受控自主闭环.md)）。
  - 测试：`tests/l3/test_dispatch_bus.py` 补 `investigate` 去向用例（GWT-3/4/5）；`tests/l3/test_commands_registry.py` / `tests/l3/test_intent_protocol.py` 的枚举断言同批跟上（GWT-1/2/7）。

## 可关闭的遗留

- 无。开工 ④ 逐册读六本遗留册未闭区（2026-10-08 复核）：L0 册 `C3` / `C5`（皆**人决**）· L1 册全段闭 · L2 册全段闭 · L3 册 `A3`（**人决**，上界是否开放端用户可调）· L5 册空 · L6 册 `B1`（待人立项，主动提案落地点）——均「归属 / 解封条件」**非本任务**，无本批可闭项。

## 假设与前提

- **A1 · 四处枚举必须同批改齐（陷阱二）**——前提：新增意图要动**四处**，漏任何一处都有后果，其中一处是**硬失败**：

  | 位置 | 漏了会怎样 |
  | --- | --- |
  | [`commands/registry.py`](../../../../src/st_agent/l3/commands/registry.py) 的 `INTENT_KINDS` + `IntentKind` | 类型不认；理解提示词的枚举由 `INTENT_KINDS` 插值（**自动跟随**） |
  | [`dispatch/bus.py`](../../../../src/st_agent/l3/dispatch/bus.py) 的 `ROUTE_SPECS` | **`:139` 的 assert 当场炸**（好事——集合断言 `ROUTE_BY_INTENT == INTENT_KINDS` 是强制函数） |
  | [`intent/protocol.py`](../../../../src/st_agent/l3/intent/protocol.py) 的 `INTENT_TARGETS` | **`_intent_item` 直取 `INTENT_TARGETS[draft.intent]` → `KeyError`**——**暗雷**：前两处齐了才会跑到这里 |
  | 同文件的 `INTENT_PARAM_SPECS` | 本意图**无参数**，故**不加**（加了反而臆造问项） |

  **若错的影响**：`INTENT_TARGETS` 漏项是**运行期 KeyError**（不是构造期失败），且只在真的收敛到该意图时才暴露——**单测若只测前两处会漏**。
  **验证方式**：GWT-2 以「确认卡可产出」钉死（该用例必走 `_intent_item`）。
- **A2 · 无意图级参数是**刻意**的，不是遗漏**——前提：[D-090](../../../决策日志.md) ⑥ 定「上界是系统预算、非用户可调项」；[05 §3.2](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 因此明写该意图无参数声明。
  **若错的影响**：若日后要用户可调深度，须回 [D-090](../../../决策日志.md) 复议并加 `INTENT_PARAM_SPECS` 条目（**那是契约语境的变化**，不只是加一行）。
  **验证方式**：GWT-2 断言确认卡恰好一条目。
- **A3 · 循环端口走**注入**而非就地构造**——前提：[05 §4](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 明写「注入而非就地构造，与 `runner` / `deliberations` / `trainings` 同一取向，以使总线可在**不接 LLM** 的前提下单测」。
  **若错的影响**：总线绑上 LLM 依赖，其既有 30+ 条单测被迫带替身。
  **验证方式**：GWT-3 / GWT-4 以 Fake 端口与缺省 fail-closed 成对钉住。
- **A4 · 首刀只接 `investigate`，不覆写 `query`**——前提：[D-090](../../../决策日志.md) ⑤ 的保护取向——`query` 的**确定性是可审计性的一部分**。
  **若错的影响**：若改 `query` 为多步，简单查询会被迫付循环的成本与不确定性。
  **验证方式**：GWT-6 以「既有六类逐字节不变」钉死。
- **A5 · 循环端口的形态（构造形参名 / 端口方法名 / 调用式）**——前提：与既有去向**同型**——构造形参 `investigations`（同 `runner` / `deliberations` / `trainings` 的注入取向），端口方法 `investigate(confirmation, *, values=None, now=None)`，只取返回载荷里的 `.envelope`（鸭子面，本层不认循环类型）。[05 §4](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 只定「注入」与「只见鸭子面」，未定方法名，故此处为**实现口径**。
  **若错的影响**：端口形态是 [`T-INT-006`](T-INT-006-M5集成关卡受控自主闭环.md) 装配时的交接点——改名会连带改其装配代码（返工面小，但属跨任务交接面）。
  **验证方式**：GWT-3 以 Fake 端口钉住调用式与「信封原样透出」。
- **A6 · `app.py` 的改动边界＝只落「注入缝」**——前提：本任务在 [`_build_l3`](../../../../src/st_agent/app.py) 增 `investigations` 形参并透传 `DispatchBus`（「把循环注入总线」的注射点）；循环端口的**实际装配**（protocol / runner / tools / gate / store）、M5 组合根与 [`__main__.py`](../../../../src/st_agent/__main__.py) 换默认根归 [`T-INT-006`](T-INT-006-M5集成关卡受控自主闭环.md)（[05 §4](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 明写「循环的**装配**归 M5 集成关卡」）。
  **若错的影响**：若本任务越界装配，会与 `T-INT-006` 声明的交付面（「M5 组合根：循环装配 + 授权面注入 + 探测步骤接线」）重叠或冲突；反之若本任务完全不碰 `app.py`，则该去向在生产组合根里**无注射点**。
  **验证方式**：本任务不改 `__main__.py`、不新增 `build_m5_runtime`；GWT-3/4 只经 `DispatchBus` 新注入位验，`build_m5_runtime` 的端到端生效由 `T-INT-006` 的 GWT-7 断。
- **A7 · 未注入时的「点名归属任务」落在 `reason` 文案**——前提：`investigate` 去向登记为 `wired=True, owner=None`（同其余六类；既有用例 `test_no_route_is_left_unwired` 断言**全表** `owner is None`），故 [GWT-4](#验收标准given-when-then) 的「点名归属任务」只能落在 `reason` 文本里——点名**装配归属方** [`T-INT-006`](T-INT-006-M5集成关卡受控自主闭环.md)（同 A6 的依据）。**不复用** `_pending` 路径（那条要求 `owner` 非空）。
  **若错的影响**：若改登记为 `wired=False, owner=…`，会破既有「全表 `wired=True` 且 `owner is None`」的断言（GWT-6「既有六类行为逐字节不变」），并把一条**已实现**的去向假装成未接入。
  **验证方式**：GWT-4 断言 `unavailable` 且 `reason` 含 `T-INT-006` 字样。

## 涉及契约

- [05 §3.1 意图分类](../../../../docs/技术架构-v2/05-L3-对话主入口.md)（第七类）· [§3.2](../../../../docs/技术架构-v2/05-L3-对话主入口.md)（无 target 意图 / 无参数）· [§4 任务派发总线](../../../../docs/技术架构-v2/05-L3-对话主入口.md)（去向与接入态）· [§8 快捷指令](../../../../docs/技术架构-v2/05-L3-对话主入口.md)
- [01 §5 ResultEnvelope](../../../../docs/技术架构-v2/01-平台共享契约.md) · [01 §6 中性化校验](../../../../docs/技术架构-v2/01-平台共享契约.md)
- [铁律 2](../../../工程宪法.md) · [铁律 7](../../../工程宪法.md)（去向经鸭子端口）

## 参考

- [D-090](../../../决策日志.md)（D-b 首刀路径 / 落点 A）· [D-091](../../../决策日志.md)（契约落点 ①）
- 调研：[Agent 自主能力调研报告](../../../../docs/Agent自主能力调研报告.md) §11.3（陷阱二）
- 先例：[`T-L3-001.3`](../M1/T-L3-001.3-快捷指令注册表.md)（`INTENT_KINDS` 的建立）· [`T-L3-002.1`](../M1/T-L3-002.1-意图理解与澄清协议.md) · [`T-L3-002.2`](../M1/T-L3-002.2-任务派发总线.md)
- 上游：[`T-AGT-004.3`](T-AGT-004.3-产出接入、留痕与失败语义.md) · [`T-AGT-005`](T-AGT-005-动作闸门接入.md)
