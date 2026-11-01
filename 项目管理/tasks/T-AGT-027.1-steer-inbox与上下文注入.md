---
id: T-AGT-027.1
parent: T-AGT-027
title: steer inbox 与上下文注入
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P2
milestone: M11
depends_on: [T-AGT-022.1]
status: todo
decisions: [D-154]
verify:
---

# T-AGT-027.1 · steer inbox 与上下文注入

## 目标
步骤边界排空 inbox，把插话作为带「运行中插入」标记的 user 消息并入下一轮上下文；与「模型给出结束」竞态时转为下一条普通消息。

## 验收标准（Given-When-Then）
- GWT-1：Given 运行中插话，Then 下一步上下文含该消息并带标记。
- GWT-2：Given 插话与结束竞态，Then 转为下一条普通消息。
- GWT-3：Given 多条插话，Then 按到达顺序全部保留。

## 接口面
（④ 对齐时补：`run_agent_loop` 的 `steer_inbox` 参数、上下文装配的历史段。）

## 可关闭的遗留
（④ 对齐时逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区后补；检查 8 兜底。）

## 假设与前提
（④ 对齐时补：插话不进事件面板、只进模型上下文；每次注入受预算约束。）
