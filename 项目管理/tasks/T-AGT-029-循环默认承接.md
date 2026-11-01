---
id: T-AGT-029
parent: null
title: 循环默认承接
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §3.1](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M11
depends_on: [T-AGT-020, T-AGT-022.1]
status: todo
decisions: [D-154]
verify:
---

# T-AGT-029 · 循环默认承接

> **分组节点**：本节点不直接交付，交付由 [`T-AGT-029.1`](T-AGT-029.1-对话入口改造与会话预取.md) / [`T-AGT-029.2`](T-AGT-029.2-快捷指令短路保留.md) 承接；父状态与父 `depends_on` 由 `render_ledger.py render` 按 children 派生。

## 目标
把对话入口从意图理解换成循环。

## 验收标准（Given-When-Then）
- GWT-1：由 `.1` / `.2` 合成，父节点不单独验收。

## 接口面
（④ 对齐时补：子叶的并集。）

## 可关闭的遗留
（④ 对齐时逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区后补；检查 8 兜底。）

## 假设与前提
（④ 对齐时补：子叶的并集。）
