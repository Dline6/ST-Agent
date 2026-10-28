---
id: T-AGT-009
parent: null
title: 循环上下文的会话与记忆面
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M8
depends_on: []
status: done
decisions: [D-137]
verify:
---

# T-AGT-009 · 循环上下文的会话与记忆面

> **分组节点**：本节点不直接交付，交付由 [`T-AGT-009.1`](T-AGT-009.1-会话身份进派发链.md) / [`T-AGT-009.2`](T-AGT-009.2-历史与记忆注入.md) / [`T-AGT-009.3`](T-AGT-009.3-上下文预算与超限策略.md) 承接；父状态与父 `depends_on` 由 `render_ledger.py render` 按 children 派生。

## 目标
让循环看见「这一轮属于哪个会话、之前聊过什么、这个人关注什么」。

## 验收标准（Given-When-Then）
- 分组节点不单独验收——由三叶（会话身份 / 历史与记忆 / 预算）合成。

## 接口面
- 输入（消费的前置接口）：
  - `SessionStore` / `assemble_context`（`l3/chat/session.py`，`chat_history` 分区）
  - `MemoryReader.query(SliceQuery)`（`l2/memory/reader.py`）
  - `DispatchBus.dispatch`（`l3/dispatch/bus.py`）与循环端口鸭子面（`composition/agent.py`）
  - `run_agent_loop` / `assemble_work_context`（`l3/runtime/loop.py` / `context.py`）
  - 01 §7 统一配置门面（`l1/registry`）与 `investigate.` 族适配器（`l3/runtime/bounds.py`）
- 输出（本任务交付的公共 API / 落盘位置）：
  - `DispatchBus.dispatch(..., session_id=…)` / `DispatchOutcome.session_id`（`.1`）
  - `WorkContext.history` / `.memory` / `.dropped`，`run_agent_loop(..., history=, memory=)`（`.2` / `.3`）
  - 新配置项 `investigate.context_budget`（01 §7，`.3`）
  - 组合根 `_AgentInvestigator` 的会话 / 记忆取数接线（`composition/agent.py` / `runtime/m5.py`）
  - 留痕字段 `AgentRunRecord.session_id` / `context_dropped` / `budget_chars`

## 可关闭的遗留
无（逐册核过 L0–L3 / L5 / L6 未闭区；L0 `C3` / `C5` / `D1`–`D3`、L5 `A1` 均属人决或人配，无「归属＝本任务」或「解封条件＝本任务 `done`」的条目）

## 假设与前提
- A1：三叶合成交付，父节点不单独验收；接口面按各叶 `## 接口面` 汇总。若错：父节点被误当实现任务。验证：`.1`–`.3` 各自 GWT 与 `render_ledger.py` 的父状态派生。
- A2：会话 / 记忆只经组合根预取注入循环，循环本体不读存储（同 `.2` A3）。若错：循环与存储耦合、单测需真库。验证：`run_agent_loop` 既有 Fake 用例零改动。
- A3：预算超限只做「如实取舍 + 标注」，不做压缩（D-090 冻结范围，`.3` A3）。若错：须解冻压缩并扩里程碑。

## 涉及契约
- [05 §4](../../docs/技术架构-v2/05-L3-对话主入口.md) —— 派发签名携带会话身份
- [05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md) —— 工作上下文扩面

## 参考
- 调研报告 §13.3 的 N1 / N2
- `l3/chat/session.py` 的 `assemble_context`（已建成、零生产调用）

## 备注
**本组的资产是现成的**：`assemble_context` 在 [T-L3-002] 就建好了，但至今只有测试在调。本组把它接进循环，而不是另写一套装配。
