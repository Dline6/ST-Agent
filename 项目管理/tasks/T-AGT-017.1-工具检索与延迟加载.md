---
id: T-AGT-017.1
parent: T-AGT-017
title: 工具检索与延迟加载
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

# T-AGT-017.1 · 工具检索与延迟加载

## 目标
目录超过阈值时只暴露摘要与检索入口，按需展开。

## 验收标准（Given-When-Then）
- GWT-1：Given 目录超过阈值，When 装配，Then 只暴露摘要与检索入口。
- GWT-2：Given 模型检索，Then 按需展开匹配条目。
- GWT-3：Given 目录未超阈值，Then 行为与现在一致。

## 接口面
<④ 对齐时实填（`doing` 起不可留占位），见 [工作流](../工作流.md) 第 ④ 步>
- 输入（消费的前置接口）：
- 输出（本任务交付的公共 API / 落盘位置）：

## 可关闭的遗留
<④ 对齐时实填：扫 [遗留问题](../遗留问题/README.md) 各层册的未闭区，逐条列「归属＝本任务」或「解封条件＝本任务 `done`」的条目（册内 id + 处置）；无命中写 `无`。>

## 假设与前提
<④ 对齐时实填；每条 `A<n>` 含 前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`>

## 涉及契约
- [01 §2](../../docs/技术架构-v2/01-平台共享契约.md) —— 工具暴露面
- [05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md) —— 工作上下文

## 参考
- Codex `tool_search` / Claude Code 的延迟加载

## 备注
阈值属实现口径；未超阈值时必须逐字节不变。
