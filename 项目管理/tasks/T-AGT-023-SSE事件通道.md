---
id: T-AGT-023
parent: null
title: SSE 事件通道
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M11
depends_on: [T-AGT-022.2]
status: todo
decisions: [D-154]
verify:
---

# T-AGT-023 · SSE 事件通道

> **分组节点**：本节点不直接交付，交付由 [`T-AGT-023.1`](T-AGT-023.1-SSE端点与断线重连.md) / [`T-AGT-023.2`](T-AGT-023.2-前端事件接入.md) 承接；父状态与父 `depends_on` 由 `render_ledger.py render` 按 children 派生。

## 目标
把运行事件实时送到前端，并支持断线重连。

## 验收标准（Given-When-Then）
- GWT-1：由 `.1` / `.2` 合成，父节点不单独验收。

## 接口面
（④ 对齐时补：子叶的并集。）

## 可关闭的遗留
（④ 对齐时逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区后补；检查 8 兜底。）

## 假设与前提
（④ 对齐时补：子叶的并集。）
