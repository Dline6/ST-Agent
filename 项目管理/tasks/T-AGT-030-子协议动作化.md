---
id: T-AGT-030
parent: null
title: 子协议动作化
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P1
milestone: M11
depends_on: [T-AGT-029.1]
status: todo
decisions: [D-154]
verify:
---

# T-AGT-030 · 子协议动作化

> **分组节点**：本节点不直接交付，交付由 [`T-AGT-030.1`](T-AGT-030.1-配置与记忆子协议.md) / [`T-AGT-030.2`](T-AGT-030.2-分析与训练子协议.md) 承接；父状态与父 `depends_on` 由 `render_ledger.py render` 按 children 派生。

## 目标
把五个业务协议变成循环可调用的动作。

## 验收标准（Given-When-Then）
- GWT-1：由 `.1` / `.2` 合成，父节点不单独验收。

## 接口面
（④ 对齐时补：子叶的并集。）

## 可关闭的遗留
（④ 对齐时逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区后补；检查 8 兜底。）

## 假设与前提
（④ 对齐时补：子叶的并集。）
