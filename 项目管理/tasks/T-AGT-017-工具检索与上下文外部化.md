---
id: T-AGT-017
parent: null
title: 工具检索与上下文外部化
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P2
milestone: M10
depends_on: [T-INT-010]
status: done
decisions: [D-137, D-149]
verify: 由 T-AGT-017.1 / T-AGT-017.2 合成（执行日志两条）
---

# T-AGT-017 · 工具检索与上下文外部化

> **分组节点**：本节点不直接交付，交付由 [`T-AGT-017.1`](T-AGT-017.1-工具检索与延迟加载.md) / [`T-AGT-017.2`](T-AGT-017.2-大结果外部化.md) 承接；父状态与父 `depends_on` 由 `render_ledger.py render` 按 children 派生。

## 目标
工具多了不挤爆窗口，结果大了不撑爆上下文。

## 验收标准（Given-When-Then）
- 分组节点不单独验收——由工具检索与结果外部化两叶合成。

## 接口面
分组节点，不直接交付；两叶各自的交接点见 [`T-AGT-017.1`](T-AGT-017.1-工具检索与延迟加载.md) 与 [`T-AGT-017.2`](T-AGT-017.2-大结果外部化.md) 的「接口面」。
- 输入（消费的前置接口）：`T-INT-010`（done）的工具目录投影与循环装配件；`T-AGT-004.1` / `T-AGT-004.2`（done）的工作上下文与循环驱动；`T-AGT-008.2`（done）的内置原语注册面。
- 输出（本任务交付的公共 API / 落盘位置）：由两叶合成——`st_agent.l3.runtime.tool_search` 与 `WorkContext` 惰性字段（`.1`）；`sk_result_read` 原语与 `TruncatedResult` 回读指针（`.2`）。

## 可关闭的遗留
无（2026-10-30 逐册核过 [遗留问题](../遗留问题/README.md) 六册未闭区，无「归属＝本任务」或「解封条件＝本任务 `done`」的条目）。

## 假设与前提
无（分组节点不直接交付，假设随两叶登记）。

## 涉及契约
- [05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md) —— 上下文管理

## 参考
- D-090 冻结范围（本组为解冻项）

## 备注
**解冻须显式**：D-090 曾把工具检索与外部化列为「本期不做」，本组是新决策解冻的结果，不是静默违反。
