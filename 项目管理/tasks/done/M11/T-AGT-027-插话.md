---
id: T-AGT-027
parent: null
title: 插话
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P2
milestone: M11
depends_on: [T-AGT-022.1, T-AGT-023.2]
status: done
decisions: [D-154, D-163]
verify:
---

# T-AGT-027 · 插话

> **分组节点**：本节点不直接交付，交付由 [`T-AGT-027.1`](T-AGT-027.1-steer-inbox与上下文注入.md) / [`T-AGT-027.2`](T-AGT-027.2-前端运行中输入.md) 承接；父状态与父 `depends_on` 由 `render_ledger.py render` 按 children 派生。

## 目标
运行中允许用户补充信息。

## 验收标准（Given-When-Then）
- GWT-1：由 `.1` / `.2` 合成，父节点不单独验收。

## 接口面

子叶并集（详见 [`T-AGT-027.1`](T-AGT-027.1-steer-inbox与上下文注入.md) / [`T-AGT-027.2`](T-AGT-027.2-前端运行中输入.md)）：

- **输入**：[`runs.py`](../../../../src/st_agent/l3/runtime/runs.py) 的活动 run 注册表与控制句柄 · [`loop.py`](../../../../src/st_agent/l3/runtime/loop.py) 的 `run_agent_loop` · [`context.py`](../../../../src/st_agent/l3/runtime/context.py) 的工作上下文 · [`report.py`](../../../../src/st_agent/l3/runtime/report.py) 的留痕 · [`events.py`](../../../../src/st_agent/l3/runtime/events.py) 的运行事件 · [`session.py`](../../../../src/st_agent/l3/chat/session.py) 的会话存储 · [`chat.js`](../../../../src/st_agent/ui/web/js/pages/chat.js) 的对话宿主与 [`run_events.js`](../../../../src/st_agent/ui/web/js/run_events.js) 的事件接入。
- **输出**：`SteerInbox` / `SteerMessage`（`src/st_agent/l3/runtime/steer.py`，新）· `run_agent_loop(steer_inbox=…)` / `WorkContext.steers` / `LoopOutcome.steered` / `AgentRunRecord.steered` · 第 12 类运行事件 `steer` · `ChatMessage.steer` · `POST /api/chat/run/<id>/steer` 与对话门面 `steer_run` · 前端运行中输入与送达标记。

## 可关闭的遗留

无（子叶逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区，无归属本任务的条目）。

## 假设与前提

子叶并集，详见 `T-AGT-027.1` 的 A1–A6 与 `T-AGT-027.2` 的 A1–A4：插话受理即落会话、竞态转为普通消息且不自动开新任务、只在步骤边界按到达顺序排空、预算取舍最后才舍插话、新增 `steer` 运行事件、`ChatMessage.steer` 向后兼容；前端运行中输入可用、运行中提交恒为插话、送达标记以回执与事件为准、运行结束仍回 `ok`。
