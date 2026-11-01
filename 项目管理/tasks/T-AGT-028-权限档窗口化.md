---
id: T-AGT-028
parent: null
title: 权限档窗口化
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/01-平台共享契约.md
arch_link: "[01 §7](../../docs/技术架构-v2/01-平台共享契约.md)"
priority: P1
milestone: M11
depends_on: [T-AGT-020]
status: todo
decisions: [D-154]
verify:
---

# T-AGT-028 · 权限档窗口化

> **分组节点**：本节点不直接交付，交付由 [`T-AGT-028.1`](T-AGT-028.1-会话级覆盖与三档切换.md) / [`T-AGT-028.2`](T-AGT-028.2-manual与collaborative差异补齐.md) 承接；父状态与父 `depends_on` 由 `render_ledger.py render` 按 children 派生。

## 目标
把三档权限开进对话窗口，并支持会话级覆盖。

## 验收标准（Given-When-Then）
- GWT-1：由 `.1` / `.2` 合成，父节点不单独验收。

## 接口面
（④ 对齐时补：子叶的并集。）

## 可关闭的遗留
（④ 对齐时逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区后补；检查 8 兜底。）

## 假设与前提
（④ 对齐时补：子叶的并集。）
