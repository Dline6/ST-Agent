---
id: T-AGT-011
parent: null
title: 计划与提问
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M8
depends_on: [T-AGT-008.3, T-AGT-009.1, T-AGT-012.1]
status: todo
decisions: [D-137]
verify:
---

# T-AGT-011 · 计划与提问

> **分组节点**：本节点不直接交付，交付由 [`T-AGT-011.1`](T-AGT-011.1-plan_update计划原语.md) / [`T-AGT-011.2`](T-AGT-011.2-ask_user循环内提问.md) / [`T-AGT-011.3`](T-AGT-011.3-待确认与待输入步骤的一键续跑.md) 承接；父状态与父 `depends_on` 由 `render_ledger.py render` 按 children 派生。

## 目标
让循环能显式列计划、也能在中途向用户要信息。

## 验收标准（Given-When-Then）
- 分组节点不单独验收——由三叶（计划 / 提问 / 续跑）合成。

## 接口面
<④ 对齐时实填（`doing` 起不可留占位），见 [工作流](../工作流.md) 第 ④ 步>
- 输入（消费的前置接口）：
- 输出（本任务交付的公共 API / 落盘位置）：

## 可关闭的遗留
<④ 对齐时实填：扫 [遗留问题](../遗留问题/README.md) 各层册的未闭区，逐条列「归属＝本任务」或「解封条件＝本任务 `done`」的条目（册内 id + 处置）；无命中写 `无`。>

## 假设与前提
<④ 对齐时实填；每条 `A<n>` 含 前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`>

## 涉及契约
- [05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md) —— 计划与终止语义
- [05 §3.2](../../docs/技术架构-v2/05-L3-对话主入口.md) —— 问项来源

## 参考
- 调研报告 §13.3 的 N5 / N6
- Codex `update_plan` / `request_user_input` 的机制对照

## 备注
这两件是三家里「最像 Agent」的机制，实现成本却低于任何数据原语。
