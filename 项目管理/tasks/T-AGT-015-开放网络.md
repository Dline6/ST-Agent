---
id: T-AGT-015
parent: null
title: 开放网络
story: ../../docs/PRD-v2-Agent/story-05-local-first.md
arch: ../../docs/技术架构-v2/02-L0-本地优先基座.md
arch_link: "[02 §6](../../docs/技术架构-v2/02-L0-本地优先基座.md)"
priority: P1
milestone: M9
depends_on: [T-AGT-008.3]
status: todo
decisions: [D-137]
verify:
---

# T-AGT-015 · 开放网络

> **分组节点**：本节点不直接交付，交付由 [`T-AGT-015.1`](T-AGT-015.1-web_search联网搜索原语.md) / [`T-AGT-015.2`](T-AGT-015.2-web_fetch网页抓取原语.md) / [`T-AGT-015.3`](T-AGT-015.3-首次告知与出网开关.md) 承接；父状态与父 `depends_on` 由 `render_ledger.py render` 按 children 派生。

## 目标
让 Agent 能搜、能抓，且每次出网可审、可关。

## 验收标准（Given-When-Then）
- 分组节点不单独验收——由搜索 / 抓取 / 告知与开关三叶合成。

## 接口面
<④ 对齐时实填（`doing` 起不可留占位），见 [工作流](../工作流.md) 第 ④ 步>
- 输入（消费的前置接口）：
- 输出（本任务交付的公共 API / 落盘位置）：

## 可关闭的遗留
<④ 对齐时实填：扫 [遗留问题](../遗留问题/README.md) 各层册的未闭区，逐条列「归属＝本任务」或「解封条件＝本任务 `done`」的条目（册内 id + 处置）；无命中写 `无`。>

## 假设与前提
<④ 对齐时实填；每条 `A<n>` 含 前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`>

## 涉及契约
- [02 §6](../../docs/技术架构-v2/02-L0-本地优先基座.md) —— 出网审计
- [02 §7](../../docs/技术架构-v2/02-L0-本地优先基座.md) —— 离线能力声明

## 参考
- 调研报告 §13.1

## 备注
`live-fetch` 能力声明已存在（`none` 级）；搜索是**新**能力，须同批声明。
