---
id: T-AGT-005.1
parent: T-AGT-005
title: 运行期授权档位与共享清单
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/08-L6-反思演进.md
arch_link: "[08 §5](../../docs/技术架构-v2/08-L6-反思演进.md)"
priority: P0
milestone: M5
depends_on: []
status: done
decisions: [D-090, D-091, D-097]
verify: L6 侧新用例 27 例（GWT-1..5 + 档位切换留痕落 agent-change/ + 族适配器 + build_l6 接线；清单与档位一律真 Store + 真 EvolutionAuthorization）· 范围（l3∪l6 映射并集）1302 passed（248.46s）· 全量 3786 passed / 0 failed / 14 deselected（765.95s；基线 3740 +46 逐项归因无残差）· verify_docs --strict 检查 1–9 全过 0 断链 · 未触发联网面 · 见执行日志 [T-AGT-005]
---

# T-AGT-005.1 · 运行期授权档位与共享清单

## 目标

兑现 [D-090](../决策日志.md) T-3 的 **L6 侧**——清单与档位的 owner（[01 §7](../../docs/技术架构-v2/01-平台共享契约.md) 登记口径 / [08 §5](../../docs/技术架构-v2/08-L6-反思演进.md) 共享声明）。三件：

1. **键空间纳入动作标识**：既有 `RiskClass` 三态与那张白名单清单**不变**，`classify` 的键空间加入**运行期动作标识**（`skill.<base>` / `memory.write` / `net.egress`）——`memory.write` 命既有 `memory.` 前缀（红线）、`net.egress` 未命中即保守（[01 §7](../../docs/技术架构-v2/01-平台共享契约.md) 的白名单语义），**不新登记第二张清单**。
2. **新增运行期档位条目 `agent.authorization`**：与 `evolution.authorization` **并列不合并**（两条独立条目的配置项，缺省 `collaborative`，损坏即 fail-closed），含面板规格；切换**留痕**（[01 §7](../../docs/技术架构-v2/01-平台共享契约.md) 的登记项三能力）。
3. **判据面**：`decide(action_id) -> ActionDecision`——把「清单风险类 × 运行期档位 → 三态结论」这条**政策**留在清单与档位旁边（同 [08 §5](../../docs/技术架构-v2/08-L6-反思演进.md) 的「审批门只回答是否**自动**放行」口径），供 [`.2`](T-AGT-005.2-逐步闸门与终止交还.md) 经鸭子端口取用。
4. **不进变更流**：运行期**工具调用**不产生 `ChangeRecord`（[08 §5](../../docs/技术架构-v2/08-L6-反思演进.md)）——本叶只保证**档位配置切换**走 01 §7 的留痕面，运行期动作本身不落任何变更记录。

## 验收标准（Given-When-Then）

- **GWT-1 · 单一清单**：Given 运行期判据面与 `EvolutionAuthorization` 两个读取方，When 各自取风险清单，Then 得**同一张**（同一 `config_id` `evolution.risk-grading` 与同一份取值）；运行期侧**无**第二个 `*-grading` 条目、无第二份规则表。
- **GWT-2 · 红线单点**：Given 动作标识 `memory.write`（与任一 `memory.*`），When 分别经 `EvolutionAuthorization.classify` 与运行期判据面 `decide` 判，Then 两处均得 `never-autonomous`；**改一处清单两处同变**（只登记一处）。
- **GWT-3 · 动作标识可判**：Given 动作标识 `skill.sk_stock_watch`，When 判其风险类，Then 按**首个命中前缀**（`skill.`）得 `collaborative-required`；Given 未命中任何规则的 `net.egress`，Then 保守得 `collaborative-required`（**不静默放行**）。
- **GWT-4 · 档位彼此独立**：Given 演进档 `autonomous`、运行期档 `collaborative`，When `decide` 一个风险类为 `autonomous-ok` 的动作，Then **不放行**（结论 ≠ `autonomous-ok`，档位取运行期条目）；Given 运行期档切到 `autonomous` 后同一动作，Then 放行。两档互不顶替。
- **GWT-5 · 缺省与损坏都放行不了**：Given 运行期档条目**未落值**，When `decide`，Then 档取 `collaborative`、**不放行**；Given 条目**形态损坏**，Then 显式不放行 + 原因（fail-closed），**不臆测为自主**。

## 接口面

- **输入**：
  - [`EvolutionAuthorization`](../../src/st_agent/l6/authorization.py) 的既有面（**清单唯一事实源**）：`classify(config_id) -> RiskClass` · `grading()` · `entries()` / `entry()` · `Tier` / `TIERS` / `DEFAULT_TIER` / `DEFAULT_RISK_GRADING` / `RiskClass` / `PermitDecision`。
  - [`EvolutionStore`](../../src/st_agent/l6/authorization_store.py)：条目读写（`current` / `set` / `entry`）与变更留痕（`changes`）。
  - [01 §7 条目形态](../../src/st_agent/contracts/registry_types.py)：`ConfigEntry` / `ChangePolicy` / `PanelField`；[L1 门面](../../src/st_agent/l1/registry/facade.py) 的 `register_family`（本层**注入**消费，不 import L1 之上的任何东西）。
  - 契约口径：[01 §7 运行期动作的授权档位与风险分级](../../docs/技术架构-v2/01-平台共享契约.md) · [08 §5 共享口径](../../docs/技术架构-v2/08-L6-反思演进.md)。
- **输出**：
  - **新增** [`src/st_agent/l6/runtime_authorization.py`](../../src/st_agent/l6/runtime_authorization.py)：`RuntimeAuthorization`（`tier()` / `set_tier()` / `entries()` / `entry()` / `decide(action_id) -> ActionDecision`）+ `ActionDecision`（`verdict` / `risk_class` / `tier` / `reason`）+ 常量 `AGENT_TIER_CONFIG_ID = "agent.authorization"` / `AGENT_CHANGE_PREFIX = "agent-change/"` / `ActionVerdict`。
  - **只增** [`src/st_agent/l6/registry_adapter.py`](../../src/st_agent/l6/registry_adapter.py)：`AgentFamily` / `agent_family`（`agent.*` 族，标量档位可写、委托 `RuntimeAuthorization.set_tier`）。
  - **只增** [`src/st_agent/l6/runtime.py`](../../src/st_agent/l6/runtime.py)：`L6Stack.agent_authorization` 字段 + `build_l6` 注册 `agent.*` 族（缺省自建、可覆写注入替身）。
  - **参数化** [`src/st_agent/l6/authorization_store.py`](../../src/st_agent/l6/authorization_store.py)：`EvolutionStore` 只增可选 `change_prefix` 形参（缺省 `evolution-change/`，既有调用路径**逐字节不变**），供运行期档位落 `agent-change/`。
  - 测试：**新增** `tests/l6/test_runtime_authorization.py`（GWT-1–5 + 档位切换留痕 + 族适配器）。
  - **供 [`.2`](T-AGT-005.2-逐步闸门与终止交还.md) 消费的鸭子端口**：`AGENT_DECISION_METHOD = "decide"`，调用形如 `authz.decide(action_id) -> ActionDecision | 结论串`；**缺省不注入即由闸门 fail-closed**。

## 可关闭的遗留

- 无（2026-10-08 开工逐册读未闭区复核：`L6` 册仅 `B1`（主动提案落地点）未闭、与授权档位**无关**；`A` 段 0 条。全册 grep `agent.authorization` / 闸门 / 动作标识 / 运行期授权**零命中**）

## 假设与前提

- **A1 · 运行期档位是独立条目**——前提与影响同父 [T-AGT-005 A1](T-AGT-005-动作闸门接入.md)（[D-090](../决策日志.md) ①：演进每周低频、运行期每轮高频，两种节律）。
  **验证方式**：GWT-4 以「两档互不顶替」钉死；`agent.authorization` 与 `evolution.authorization` 是**两条** `config_id`。
- **A2 · 清单是单一事实源**——前提与影响同父 [T-AGT-005 A2](T-AGT-005-动作闸门接入.md)（[08 §7](../../docs/技术架构-v2/08-L6-反思演进.md) 的 memory 红线只登记一处）。
  **验证方式**：GWT-1 断言运行期侧与演进侧取到**同一 `config_id` 同一取值**；GWT-2 以「改一处两处同变」钉死。
- **A3 · 判定归属 L6、闸门只做适配**（本次 ④ 对齐拍定）——前提：「风险类 × 档位 → 三态结论」这条**政策**与清单 / 档位同源（[08 §5](../../docs/技术架构-v2/08-L6-反思演进.md) 的审批门语义表即此裁法）；若判定散在 L3，则 L3 需同时持有清单语义与档位语义，与 [A3 的注入面](T-AGT-005-动作闸门接入.md) 重复。
  **若错的影响**：返工面 = 判定逻辑从 L6 搬去 L3（端口签名不变，属**搬家**而非重写）。
  **验证方式**：GWT-3 / GWT-4 直接打在 `decide` 上；`.2` 的 `ActionGate` 只断言「派生 + 适配 + fail-closed」，不含风险类语义。
- **A4 · 档位切换留痕落 `agent-change/`**（本次 ④ 对齐拍定）——前提：运行期档位**不是**演进动作（[08 §5](../../docs/技术架构-v2/08-L6-反思演进.md) 明写变更流只管演进动作），故不混进 `evolution-change/`——那会让 [08 §6 的出厂重置](../../docs/技术架构-v2/08-L6-反思演进.md)「授权档复位」把运行期档一并回放。
  **若错的影响**：若并入 `evolution-change/`，出厂重置的「授权档复位」语义被扩大（用户改的运行期档被静默复位）。
  **验证方式**：`set_tier` 的留痕路径断言落在 `agent-change/`；门面 `changes()` 的**跨族**读面仍能看到它（目录以 `-change` 结尾即入读面，可追溯不丢）。

## 涉及契约

- [01 §7 运行期动作的授权档位与风险分级](../../docs/技术架构-v2/01-平台共享契约.md)（共享清单 / 分设档位 / 动作标识键空间 / 缺省与损坏都不放行）
- [08 §5 风险分级清单与运行期授权的共享口径](../../docs/技术架构-v2/08-L6-反思演进.md)（清单单一事实源 · 变更流不因共享而改变）
- [08 §6 回滚与出厂重置](../../docs/技术架构-v2/08-L6-反思演进.md)（「授权档复位」的范围＝`evolution.authorization`）
- [铁律 7](../../项目管理/工程宪法.md)（本层只**注入** L1 门面，不反向）

## 参考

- [D-090](../决策日志.md)（① T-3）· [D-091](../决策日志.md)（契约落点 ④）· [D-097](../决策日志.md)（本批拆分与四处口径）
- [D-087](../决策日志.md)（演进授权档位与风险分级清单的既有交付，本叶与之**并列不合并**）
- 下游：[`.2`](T-AGT-005.2-逐步闸门与终止交还.md)（消费 `decide` 鸭子端口）· [`T-INT-006`](T-INT-006-M5集成关卡受控自主闭环.md)（组合根接线）

## 备注

**缺省清单下运行期动作恒需协作**——既有白名单的 `skill.` 前缀是 `collaborative-required`、`memory.*` 是红线、`net.egress` 未命中即保守，故**出厂配置下没有任何工具调用可自主**。这是**有意**的保守出厂态（同 [08 §5](../../docs/技术架构-v2/08-L6-反思演进.md) 「默认仅表现层参数可自主」的取向）；「某能力可自主」须由用户把该动作标识加进清单。GWT-4 因此经**注入的**清单验（规则内容本就可配）。
