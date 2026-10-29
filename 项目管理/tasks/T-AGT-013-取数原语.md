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
status: done
decisions: [D-137, D-145]
verify:
---

# T-AGT-013 · 取数原语

> **分组节点**：本节点不直接交付，交付由 [`T-AGT-013.1`](T-AGT-013.1-data_sync同步触发原语.md) / [`T-AGT-013.2`](T-AGT-013.2-data_fetch按域取数原语.md) / [`T-AGT-013.3`](T-AGT-013.3-文档取数工具化.md) 承接；父状态与父 `depends_on` 由 `render_ledger.py render` 按 children 派生。

## 目标
让循环能触发同步、按域补数、读文档正文——本地没有的，自己补。

## 验收标准（Given-When-Then）
- 分组节点不单独验收——由 `data_sync` / `data_fetch` / 文档取数三叶合成。

## 接口面
- 输入（消费的前置接口）：本分组节点不直接消费；三叶分别消费 L0 `DataSyncRunner` / `LocalDocSource`、L1 原语安装面（`T-AGT-008.*`）、运行期授权（`T-AGT-012.1`）与 `T-AGT-010.2` 的数据现状读面。
- 输出（本任务交付的公共 API / 落盘位置）：由 [`T-AGT-013.1`](T-AGT-013.1-data_sync同步触发原语.md)（`sk_data_sync`）、[`T-AGT-013.2`](T-AGT-013.2-data_fetch按域取数原语.md)（`sk_data_fetch` + `DataSyncRunner.fetch_items`）、[`T-AGT-013.3`](T-AGT-013.3-文档取数工具化.md)（`sk_docs` + `LocalDocSource.consistency`）承接；无本节点自有落盘。

## 可关闭的遗留
无（逐册核过未闭区；无归属或解封条件指向本分组节点者）。

## 假设与前提
无（分组节点不直接交付，假设随三叶分列于各自任务文件）。

## 涉及契约
- [02 §5](../../docs/技术架构-v2/02-L0-本地优先基座.md) —— 同步与取数
- [02 §2.1](../../docs/技术架构-v2/02-L0-本地优先基座.md) —— `data_cache` 分区

## 参考
- `l0/sync_runner.py`
- `l0/info/fetch.py`
- `l0/info/docs.py`

## 备注
三件底层都已在，缺的是工具化与风险分级。
