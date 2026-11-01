---
id: T-AGT-024.1
parent: T-AGT-024
title: 取消透传与 cancelled 终止
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M11
depends_on: [T-AGT-022.1]
status: todo
decisions: [D-154]
verify:
---

# T-AGT-024.1 · 取消透传与 cancelled 终止

## 目标
把 `threading.Event` 透传到循环：LLM 调用可中断、动作边界检查；终止原因新增 `cancelled`，已执行写操作如实列出且不回滚。

## 验收标准（Given-When-Then）
- GWT-1：Given 运行中置取消，Then 当前工具跑完后终止，`termination=cancelled`。
- GWT-2：Given LLM 调用进行中置取消，Then 调用被中断。
- GWT-3：Given 已执行写操作，Then 产出如实列出且不回滚。

## 接口面
（④ 对齐时补：`run_agent_loop` 的 `cancel` 参数、`TerminationReason` 枚举、`describe_agent_run` 的标签表。）

## 可关闭的遗留
（④ 对齐时逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区后补；检查 8 兜底。）

## 假设与前提
（④ 对齐时补：取消只在动作边界与 LLM 调用边界生效；不中断正在执行的写操作。）
