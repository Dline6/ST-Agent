---
id: T-AGT-010.2
parent: T-AGT-010
title: data_status（数据现状原语）
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

# T-AGT-010.2 · data_status（数据现状原语）

## 目标
让模型知道「有哪些表、多少行、多旧、缺哪个标的」，从而决定去同步还是如实告知。

## 验收标准（Given-When-Then）
- GWT-1：Given 已同步的库，When 调用，Then 返回逐任务 / 逐表行数与最后成功时间。
- GWT-2：Given 未同步或部分同步，Then 显式区分「已知空集」与「数据缺失」。
- GWT-3：Given 覆盖账本，Then 返回缺口标的。

## 接口面
<④ 对齐时实填（`doing` 起不可留占位），见 [工作流](../工作流.md) 第 ④ 步>
- 输入（消费的前置接口）：
- 输出（本任务交付的公共 API / 落盘位置）：

## 可关闭的遗留
<④ 对齐时实填：扫 [遗留问题](../遗留问题/README.md) 各层册的未闭区，逐条列「归属＝本任务」或「解封条件＝本任务 `done`」的条目（册内 id + 处置）；无命中写 `无`。>

## 假设与前提
<④ 对齐时实填；每条 `A<n>` 含 前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`>

## 涉及契约
- [02 §5](../../docs/技术架构-v2/02-L0-本地优先基座.md) —— 数据现状与新鲜度
- [05 同步策略与新鲜度契约](../../docs/数据库设计-BaoStock数据层/05-同步策略与新鲜度契约.md) —— `as_of` 口径

## 参考
- `l0/sync_runner.py` 的 `overview()`
- `l0/market/db.py` 的 `freshness()` / `as_of()`

## 备注
没有本叶，模型无法判断「该不该去联网」——它是 M9 取数原语的前置。
