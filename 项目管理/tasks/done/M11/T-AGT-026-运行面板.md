---
id: T-AGT-026
parent: null
title: 运行面板
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/PRD-v2-Agent/14-desktop-form.md
arch_link: "[14 §3](../../../../docs/PRD-v2-Agent/14-desktop-form.md)"
priority: P1
milestone: M11
depends_on: [T-AGT-023.2]
status: done
decisions: [D-154]
verify:
---

# T-AGT-026 · 运行面板

> **分组节点**：本节点不直接交付，交付由 [`T-AGT-026.1`](T-AGT-026.1-步骤流与展开详情.md) / [`T-AGT-026.2`](T-AGT-026.2-计划列表.md) / [`T-AGT-026.3`](T-AGT-026.3-交还卡.md) 承接；父状态与父 `depends_on` 由 `render_ledger.py render` 按 children 派生。

## 目标
把运行中的计划、步骤与交还卡画出来。

## 验收标准（Given-When-Then）
- GWT-1：由 `.1` / `.2` / `.3` 合成，父节点不单独验收。

## 接口面

子叶并集（详各叶 §接口面）：

- **输入**：`RunEvent` 与 SSE（`T-AGT-022.2` / `T-AGT-023.1`）；`run_agent_loop` 的 `step_started` / `step_finished` / `plan_updated` / `sub_agent` / `gate_pending` / `ask_user` 事件；终轮 `agent_continuation` 载荷（`T-AGT-011.3` 线面）；既有 `action=resume` 续跑。
- **输出**：事件载荷扩字段（`call_id` / `arguments` / `truncated` / `summary` / 子 Agent 步骤与结论）与 `result_will_truncate` / `summarize_result`（`.1`）；运行面板计划块（`.2`）；交还载荷的 `reason` / `evidence` 与允许 / 拒绝卡（`.3`）。不新增事件类型、不新增端点、不改 Trace。

## 可关闭的遗留

无（逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区，无归属 / 解封条件是本任务或本批的条目）。

## 假设与前提

子叶并集，编号见各叶：`.1` A1–A5、`.2` A1–A3、`.3` A1–A4。
