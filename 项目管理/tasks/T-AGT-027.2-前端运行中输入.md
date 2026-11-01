---
id: T-AGT-027.2
parent: T-AGT-027
title: 前端运行中输入
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/PRD-v2-Agent/14-desktop-form.md
arch_link: "[14 §3](../../docs/PRD-v2-Agent/14-desktop-form.md)"
priority: P2
milestone: M11
depends_on: [T-AGT-027.1, T-AGT-023.2]
status: todo
decisions: [D-154]
verify:
---

# T-AGT-027.2 · 前端运行中输入

## 目标
运行中输入框可用，提交即插话；有明确「已送达运行中任务」提示。

## 验收标准（Given-When-Then）
- GWT-1：Given 运行中，Then 输入框可用且提交为插话。
- GWT-2：Given 提交成功，Then 标注已送达且不打断当前步骤。

## 接口面
（④ 对齐时补：对话输入组件与插话端点。）

## 可关闭的遗留
（④ 对齐时逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区后补；检查 8 兜底。）

## 假设与前提
（④ 对齐时补：插话不排队成新任务；开新任务须先停止。）
