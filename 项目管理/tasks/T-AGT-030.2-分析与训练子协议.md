---
id: T-AGT-030.2
parent: T-AGT-030
title: 分析与训练子协议
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P2
milestone: M11
depends_on: [T-AGT-029.1]
status: todo
decisions: [D-154]
verify:
---

# T-AGT-030.2 · 分析与训练子协议

## 目标
`analyze` / `train` / `explain` 变为循环可调动作，分别触发 L4 分歧图、L6 训练协议、Trace 展开。

## 验收标准（Given-When-Then）
- GWT-1：Given 复杂决策，Then 触发 L4 并出分歧图。
- GWT-2：Given 训练修正，Then 走 L6 协议。
- GWT-3：Given 追问推理，Then 展开对应 Trace。

## 接口面
（④ 对齐时补：`DispatchBus` 的 `deliberations` / `trainings` 端口与 `describe_trace`。）

## 可关闭的遗留
（④ 对齐时逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区后补；检查 8 兜底。）

## 假设与前提
（④ 对齐时补：L3 仍不 import L4 / L6，一律经鸭子端口。）
