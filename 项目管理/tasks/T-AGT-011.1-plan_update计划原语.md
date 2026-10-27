---
id: T-AGT-011.1
parent: T-AGT-011
title: plan_update（计划原语）
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M8
depends_on: [T-AGT-008.3]
status: todo
decisions: [D-137]
verify:
---

# T-AGT-011.1 · plan_update（计划原语）

## 目标
模型可显式维护步骤计划，并在执行流里可渲染。

## 验收标准（Given-When-Then）
- GWT-1：Given 一个多步任务，When 模型调用 `plan_update`，Then 计划进入工作上下文并可被执行流渲染。
- GWT-2：Given 计划更新，Then **不产生** Trace 步（决策不入 Trace），但记入 `agent_run` 报告。
- GWT-3：Given 空计划，Then 显式清空，不静默忽略。

## 接口面
<④ 对齐时实填（`doing` 起不可留占位），见 [工作流](../工作流.md) 第 ④ 步>
- 输入（消费的前置接口）：
- 输出（本任务交付的公共 API / 落盘位置）：

## 可关闭的遗留
<④ 对齐时实填：扫 [遗留问题](../遗留问题/README.md) 各层册的未闭区，逐条列「归属＝本任务」或「解封条件＝本任务 `done`」的条目（册内 id + 处置）；无命中写 `无`。>

## 假设与前提
<④ 对齐时实填；每条 `A<n>` 含 前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`>

## 涉及契约
- [05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md) —— 计划字段与渲染
- [01 §4](../../docs/技术架构-v2/01-平台共享契约.md) —— 决策不入 Trace 的口径

## 参考
- Codex 的 `update_plan` / Claude Code 的 `TodoWrite`

## 备注
做成**工具**而非循环内字段：可过闸门、可留痕、可渲染，且与其余动作同一形态。
