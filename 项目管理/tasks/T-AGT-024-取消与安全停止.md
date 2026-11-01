---
id: T-AGT-024
parent: null
title: 取消与安全停止
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P1
milestone: M11
depends_on: [T-AGT-022.1, T-AGT-023.1]
status: todo
decisions: [D-154]
verify:
---

# T-AGT-024 · 取消与安全停止

> **分组节点**：本节点不直接交付，交付由 [`T-AGT-024.1`](T-AGT-024.1-取消透传与cancelled终止.md) / [`T-AGT-024.2`](T-AGT-024.2-停止端点与停止按钮.md) 承接；父状态与父 `depends_on` 由 `render_ledger.py render` 按 children 派生。

## 目标
让运行可被用户停止，且停止语义诚实。

## 验收标准（Given-When-Then）
- GWT-1：由 `.1` / `.2` 合成，父节点不单独验收。

## 接口面
（④ 对齐时补：子叶的并集。）

## 可关闭的遗留
（④ 对齐时逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区后补；检查 8 兜底。）

## 假设与前提
（④ 对齐时补：子叶的并集。）
