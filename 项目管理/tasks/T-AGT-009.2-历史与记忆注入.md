---
id: T-AGT-009.2
parent: T-AGT-009
title: 历史与记忆注入
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M8
depends_on: [T-AGT-009.1]
status: todo
decisions: [D-137]
verify:
---

# T-AGT-009.2 · 历史与记忆注入

## 目标
把 `assemble_context` 的两个产出（近端消息 + 记忆切片）挂进循环工作上下文。

## 验收标准（Given-When-Then）
- GWT-1：Given 一个多轮会话，When 用户追问「那沪市呢」，Then 循环上下文含上一轮任务与结果。
- GWT-2：Given 用户有持仓 / 关注记忆，When 循环装配，Then 记忆切片进入上下文且受预算约束。
- GWT-3：Given 无历史且无记忆，Then 上下文仍可装配，空态**显式**而非静默。

## 接口面
<④ 对齐时实填（`doing` 起不可留占位），见 [工作流](../工作流.md) 第 ④ 步>
- 输入（消费的前置接口）：
- 输出（本任务交付的公共 API / 落盘位置）：

## 可关闭的遗留
<④ 对齐时实填：扫 [遗留问题](../遗留问题/README.md) 各层册的未闭区，逐条列「归属＝本任务」或「解封条件＝本任务 `done`」的条目（册内 id + 处置）；无命中写 `无`。>

## 假设与前提
<④ 对齐时实填；每条 `A<n>` 含 前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`>

## 涉及契约
- [05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md) —— 循环工作上下文的字段面
- [03 §1](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md) —— SkillContext 是否增记忆句柄（与 G10 同题）

## 参考
- `l3/runtime/context.py`（`WorkContext`）
- `l3/chat/session.py`（`assemble_context`）
- `l2/memory/reader.py`

## 备注
**不得**把 `assemble_context` 当「循环工作上下文」本身——两者内容与消费者不同（调研报告 §8.4 已钉死这条）。本叶只把它的**两个产出**作为新字段并入。
