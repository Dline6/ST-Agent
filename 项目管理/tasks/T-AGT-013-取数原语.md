---
id: T-AGT-013
parent: null
title: 取数原语
story: ../../docs/PRD-v2-Agent/story-05-local-first.md
arch: ../../docs/技术架构-v2/02-L0-本地优先基座.md
arch_link: "[02 §5](../../docs/技术架构-v2/02-L0-本地优先基座.md)"
priority: P1
milestone: M9
depends_on: [T-AGT-010.2, T-AGT-008.3]
status: todo
decisions: [D-137]
verify:
---

# T-AGT-013 · 取数原语

> **分组节点**：本节点不直接交付，交付由 [`T-AGT-013.1`](T-AGT-013.1-data_sync同步触发原语.md) / [`T-AGT-013.2`](T-AGT-013.2-data_fetch按域取数原语.md) / [`T-AGT-013.3`](T-AGT-013.3-文档取数工具化.md) 承接；父状态与父 `depends_on` 由 `render_ledger.py render` 按 children 派生。

## 目标
让循环能触发同步、按域补数、读文档正文——本地没有的，自己补。

## 验收标准（Given-When-Then）
- 分组节点不单独验收——由 `data_sync` / `data_fetch` / 文档取数三叶合成。

## 接口面
<④ 对齐时实填（`doing` 起不可留占位），见 [工作流](../工作流.md) 第 ④ 步>
- 输入（消费的前置接口）：
- 输出（本任务交付的公共 API / 落盘位置）：

## 可关闭的遗留
<④ 对齐时实填：扫 [遗留问题](../遗留问题/README.md) 各层册的未闭区，逐条列「归属＝本任务」或「解封条件＝本任务 `done`」的条目（册内 id + 处置）；无命中写 `无`。>

## 假设与前提
<④ 对齐时实填；每条 `A<n>` 含 前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`>

## 涉及契约
- [02 §5](../../docs/技术架构-v2/02-L0-本地优先基座.md) —— 同步与取数
- [02 §2.1](../../docs/技术架构-v2/02-L0-本地优先基座.md) —— `data_cache` 分区

## 参考
- `l0/sync_runner.py`
- `l0/info/fetch.py`
- `l0/info/docs.py`

## 备注
三件底层都已在，缺的是工具化与风险分级。
