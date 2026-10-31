---
id: T-AGT-012.1
parent: T-AGT-012
title: 运行期档位缺省与动作级分级
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/08-L6-反思演进.md
arch_link: "[08 §5](../../../../docs/技术架构-v2/08-L6-反思演进.md)"
priority: P0
milestone: M8
depends_on: []
status: done
decisions: [D-137, D-143]
verify: 范围套件 2338 passed / 2 errors（test_shell 环境性）；见执行日志 [T-AGT-012.1]
---

# T-AGT-012.1 · 运行期档位缺省与动作级分级

## 目标
agent 档独立缺省 `autonomous`，风险清单按动作细化（具体规则先于 `skill.` 泛化规则）。

## 验收标准（Given-When-Then）
- GWT-1：Given 出厂配置，When 循环调用**只读**原语，Then 自主执行，不再要求逐步确认。
- GWT-2：Given 写类 / 出网动作，Then 仍需确认；`memory.*` 恒 `never-autonomous`。
- GWT-3：Given 档位条目或清单损坏，Then fail-closed（不放行）。
- GWT-4：Given 演进档位 `evolution.authorization`，Then **不被**本项改变。

## 接口面
- 输入（消费的前置接口）：
  - `DEFAULT_RISK_GRADING` / `classify()`（[`l6/authorization.py`](../../../../src/st_agent/l6/authorization.py)，T-L6-003.1 交付）——共享清单的单一事实源
  - `RuntimeAuthorization`（[`l6/runtime_authorization.py`](../../../../src/st_agent/l6/runtime_authorization.py)，T-AGT-005.1 交付）——档位、判据与条目
  - `ActionGate.action_id_of`（[`l3/runtime/gate.py`](../../../../src/st_agent/l3/runtime/gate.py)，T-AGT-005.2 交付）——工具名 → `skill.<base>`
  - 内置原语 `LOCAL_SQL` / `DATA_STATUS`（[`l1/builtin/`](../../../../src/st_agent/l1/builtin)，T-AGT-010.1 / `.2` 交付）——base 为 `sk_local_sql` / `sk_data_status`
  - 01 §7 统一配置门面与 [`l6/registry_adapter.py`](../../../../src/st_agent/l6/registry_adapter.py) 的 `agent_family`（T-AGT-005.1 交付）
- 输出（本任务交付的公共 API / 落盘位置）：
  - `DEFAULT_RISK_GRADING` 在 `skill.` 通则**之前**增两条具体规则：`skill.sk_local_sql` / `skill.sk_data_status` → `autonomous-ok`（[`l6/authorization.py`](../../../../src/st_agent/l6/authorization.py)）
  - `DEFAULT_AGENT_TIER = "autonomous"` 及 `RuntimeAuthorization` 缺省档（[`l6/runtime_authorization.py`](../../../../src/st_agent/l6/runtime_authorization.py)，经 `st_agent.l6` 导出）
  - `agent.authorization` 条目文案（面板帮助文本 / 对话描述）同步缺省口径
  - 契约口径：[01 §7](../../../../docs/技术架构-v2/01-平台共享契约.md) 的运行期授权段、[08 §5](../../../../docs/技术架构-v2/08-L6-反思演进.md) 的共享清单一节与 [05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 的闸门段

## 可关闭的遗留
无（逐册核过 L0–L3 / L5 / L6 未闭区；无「归属＝本任务」或「解封条件＝本任务 `done`」的条目）

## 假设与前提
- A1：「只读原语」＝内置原语（`sk_local_sql` / `sk_data_status`），**不含**官方 12 个 Skill；后者仍按 `skill.` 通则需确认。若错（官方 Skill 也应在出厂即自主）：须再登记 12 条具体规则，且与演进的 `skill.<base>.<param>` 前缀交叠面扩大（见 A4）。验证：`tests/l6/test_runtime_authorization.py` 与 `tests/integration/test_m5_autonomy.py` 的出厂配置用例。
- A2：运行期缺省档改为 `autonomous` 只放宽清单已标 `autonomous-ok` 的动作；其余动作、未登记动作与 `memory.*` 红线行为不变。若错（缺省不应为自主）：GWT-1 不成立，须改回并另找解锁路径。验证：同一批用例的写类 / 出网 / memory 断言。
- A3：共享清单的具体规则放在 `skill.` 通则之前即可生效，**不改判据机制**（`classify` 首个前缀命中，T-AGT-005.1 既有实现）。若错：须给清单加匹配模式（精确 / 动作专用）。验证：`classify("skill.sk_local_sql")` 与 `classify("skill.sk_stock_watch")` 的顺序断言。
- A4：`skill.sk_local_sql` / `skill.sk_data_status` 前缀同时会命中演进的同名配置前缀；但内置原语**不可编辑、不可版本化**（D-139），演进侧不存在可落值的 `skill.sk_local_sql.*` 条目，故无实际放宽。若错（出现可编辑条目）：须给清单加「仅动作」标记，否则演进侧会误判可自主。验证：`EvolutionAuthorization.classify("skill.sk_local_sql.sql")` 的现状断言 + 内置 Skill 的 `set_parameters` 拒绝用例。

## 涉及契约
- [01 §7](../../../../docs/技术架构-v2/01-平台共享契约.md) —— 两条档位条目互不影响
- [08 §5](../../../../docs/技术架构-v2/08-L6-反思演进.md) —— 清单是单一事实源

## 参考
- `l6/authorization.py` 的 `classify`（按序取首个前缀命中）
- `l6/runtime_authorization.py`

## 备注
`classify` 是顺序匹配，故「具体规则写在 `skill.` 之前」即可细化，**不需要改判据机制**，只改清单内容与缺省值。
