---
id: T-AGT-018.1
parent: T-AGT-018
title: 并行工具调用
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

# T-AGT-018.1 · 并行工具调用

## 目标
一轮多个工具调用，逐个过闸门后并行执行。

## 验收标准（Given-When-Then）
- GWT-1：Given 一轮多个 `tool_calls`，When 执行，Then 逐个过闸门并并行执行。
- GWT-2：Given 任一被拒，Then 该步被拒、其余按规则处理，不静默丢。
- GWT-3：Given 底层并发不安全，Then 显式退回串行并标注。

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
- `l3/runtime/loop.py:282`（现「一轮只认第一个调用」）

## 备注
**先验证再承诺**：`SkillRunner` 与 `Store` 的并发安全没有证据，任务 `假设与前提` 须先列这条验证。
