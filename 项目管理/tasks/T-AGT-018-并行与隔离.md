---
id: T-AGT-018
parent: null
title: 并行与隔离
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P2
milestone: M10
depends_on: [T-INT-010]
status: todo
decisions: [D-137]
verify:
---

# T-AGT-018 · 并行与隔离

> **分组节点**：本节点不直接交付，交付由 [`T-AGT-018.1`](T-AGT-018.1-并行工具调用.md) / [`T-AGT-018.2`](T-AGT-018.2-子Agent上下文隔离.md) 承接；父状态与父 `depends_on` 由 `render_ledger.py render` 按 children 派生。

## 目标
一轮多动作，以及把探索装进不污染主窗的子上下文。

## 验收标准（Given-When-Then）
- 分组节点不单独验收——由并行调用与子 Agent 两叶合成。

## 接口面
<④ 对齐时实填（`doing` 起不可留占位），见 [工作流](../工作流.md) 第 ④ 步>
- 输入（消费的前置接口）：
- 输出（本任务交付的公共 API / 落盘位置）：

## 可关闭的遗留
<④ 对齐时实填：扫 [遗留问题](../遗留问题/README.md) 各层册的未闭区，逐条列「归属＝本任务」或「解封条件＝本任务 `done`」的条目（册内 id + 处置）；无命中写 `无`。>

## 假设与前提
<④ 对齐时实填；每条 `A<n>` 含 前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`>

## 涉及契约
- [05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md) —— 循环执行语义

## 参考
- D-090 冻结范围（本组为解冻项）

## 备注
**并行调用的底层已就绪**：`TurnResult.tool_calls` 已是元组，L0 与协议层都支持多调用；只是循环主动只取 `[0]`。
