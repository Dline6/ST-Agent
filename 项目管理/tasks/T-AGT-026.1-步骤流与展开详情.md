---
id: T-AGT-026.1
parent: T-AGT-026
title: 步骤流与展开详情
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/PRD-v2-Agent/14-desktop-form.md
arch_link: "[14 §3](../../docs/PRD-v2-Agent/14-desktop-form.md)"
priority: P1
milestone: M11
depends_on: [T-AGT-023.2]
status: todo
decisions: [D-154]
verify:
---

# T-AGT-026.1 · 步骤流与展开详情

## 目标
每个动作一行（工具 / 目标 / 状态 / 耗时），可展开参数、结果摘要与留痕指针；子 Agent 嵌套展示。

## 验收标准（Given-When-Then）
- GWT-1：Given 运行中，Then 每个动作一行且状态随事件更新。
- GWT-2：Given 结果被截断，Then 标「已截断」并可看留痕指针。
- GWT-3：Given 子 Agent，Then 嵌套展示其步骤与结论。

## 接口面
（④ 对齐时补：运行面板组件与事件字段的对应关系。）

## 可关闭的遗留
（④ 对齐时逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区后补；检查 8 兜底。）

## 假设与前提
（④ 对齐时补：只渲染事件里的客观字段，不自造文案。）
