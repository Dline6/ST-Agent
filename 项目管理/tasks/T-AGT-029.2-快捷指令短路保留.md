---
id: T-AGT-029.2
parent: T-AGT-029
title: 快捷指令短路保留
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §3.1](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P1
milestone: M11
depends_on: [T-AGT-029.1]
status: todo
decisions: [D-154]
verify:
---

# T-AGT-029.2 · 快捷指令短路保留

## 目标
`/` 开头的快捷指令仍在入口短路，直接取意图模板与参数预填，不进循环。

## 验收标准（Given-When-Then）
- GWT-1：Given `/盯盘`，Then 短路取意图模板与参数预填。
- GWT-2：Given 未知指令，Then 显式报错，不交循环。

## 接口面
（④ 对齐时补：入口分支与 `CommandRegistry.resolve`。）

## 可关闭的遗留
（④ 对齐时逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区后补；检查 8 兜底。）

## 假设与前提
（④ 对齐时补：快捷指令语义与现状逐字节一致。）
