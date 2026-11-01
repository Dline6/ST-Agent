---
id: T-AGT-029.1
parent: T-AGT-029
title: 对话入口改造与会话预取
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

# T-AGT-029.1 · 对话入口改造与会话预取

## 目标
`/api/chat` 的默认路径改为启动一次 agent run；会话历史与记忆在进入循环前预取。

## 验收标准（Given-When-Then）
- GWT-1：Given 任意自然语言，Then 进入循环而非先分类。
- GWT-2：Given 会话已有历史，Then 循环上下文包含历史与记忆。
- GWT-3：Given 模型直接回答，Then 产出模型正文。

## 接口面
（④ 对齐时补：`DialogFacade._turn` 的默认分支、`_AgentInvestigator` 的会话 / 记忆注入。）

## 可关闭的遗留
（④ 对齐时逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区后补；检查 8 兜底。）

## 假设与前提
（④ 对齐时补：现有 `resume` 承接确认后续跑；不改变 `investigate` 的既有载荷形态。）
