---
id: T-AGT-026.2
parent: T-AGT-026
title: 计划列表
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/PRD-v2-Agent/14-desktop-form.md
arch_link: "[14 §3](../../docs/PRD-v2-Agent/14-desktop-form.md)"
priority: P1
milestone: M11
depends_on: [T-AGT-026.1]
status: todo
decisions: [D-154]
verify:
---

# T-AGT-026.2 · 计划列表

## 目标
只在模型主动调 `sk_plan_update` 时显示计划，状态随事件更新；未给出则不显示。

## 验收标准（Given-When-Then）
- GWT-1：Given 模型调 `sk_plan_update`，Then 计划列表出现且状态更新。
- GWT-2：Given 模型未给计划，Then 不显示计划块（不伪造）。

## 接口面
（④ 对齐时补：`plan_updated` 事件与计划组件。）

## 可关闭的遗留
（④ 对齐时逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区后补；检查 8 兜底。）

## 假设与前提
（④ 对齐时补：计划状态枚举沿用 pending / active / done。）
