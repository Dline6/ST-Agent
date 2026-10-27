---
id: T-AGT-010
parent: null
title: 本地数据原语
story: ../../docs/PRD-v2-Agent/story-05-local-first.md
arch: ../../docs/技术架构-v2/02-L0-本地优先基座.md
arch_link: "[02 §5](../../docs/技术架构-v2/02-L0-本地优先基座.md)"
priority: P0
milestone: M8
depends_on: [T-AGT-008.3]
status: todo
decisions: [D-137]
verify:
---

# T-AGT-010 · 本地数据原语

> **分组节点**：本节点不直接交付，交付由 [`T-AGT-010.1`](T-AGT-010.1-local_sql只读查询原语.md) / [`T-AGT-010.2`](T-AGT-010.2-data_status数据现状原语.md) 承接；父状态与父 `depends_on` 由 `render_ledger.py render` 按 children 派生。

## 目标
让模型能直接查本地库的任意只读问题，并先判断数据新旧。

## 验收标准（Given-When-Then）
- 分组节点不单独验收——由 `local_sql` 与 `data_status` 两叶合成。

## 接口面
<④ 对齐时实填（`doing` 起不可留占位），见 [工作流](../工作流.md) 第 ④ 步>
- 输入（消费的前置接口）：
- 输出（本任务交付的公共 API / 落盘位置）：

## 可关闭的遗留
<④ 对齐时实填：扫 [遗留问题](../遗留问题/README.md) 各层册的未闭区，逐条列「归属＝本任务」或「解封条件＝本任务 `done`」的条目（册内 id + 处置）；无命中写 `无`。>

## 假设与前提
<④ 对齐时实填；每条 `A<n>` 含 前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`>

## 涉及契约
- [02 §5](../../docs/技术架构-v2/02-L0-本地优先基座.md) —— 本地数据面
- [05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md) —— 工具面

## 参考
- `l0/market/db.py:441`（只读白名单已存在）
- `l0/sync_runner.py`（`overview`）

## 备注
**本组几乎全是接线**：只读 SQL 面与数据现状面都已存在，缺的是工具入口与风险分级。
