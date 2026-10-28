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
status: done
decisions: [D-137, D-141]
verify: 两叶同批完成——local_sql / data_status 接入工具面；范围套件 2343 passed、新增 17 例、verify_docs --strict PASS；见执行日志 [T-AGT-010.1 / T-AGT-010.2]
---

# T-AGT-010 · 本地数据原语

> **分组节点**：本节点不直接交付，交付由 [`T-AGT-010.1`](T-AGT-010.1-local_sql只读查询原语.md) / [`T-AGT-010.2`](T-AGT-010.2-data_status数据现状原语.md) 承接；父状态与父 `depends_on` 由 `render_ledger.py render` 按 children 派生。

## 目标
让模型能直接查本地库的任意只读问题，并先判断数据新旧。

## 验收标准（Given-When-Then）
- 分组节点不单独验收——由 `local_sql` 与 `data_status` 两叶合成。

## 接口面
分组节点，两叶的接口面即本组交接面（细节见 [`T-AGT-010.1`](T-AGT-010.1-local_sql只读查询原语.md) / [`T-AGT-010.2`](T-AGT-010.2-data_status数据现状原语.md)）。
- 输入（消费的前置接口）：
  - `T-AGT-008.2` —— `st_agent.l1.builtin.BuiltinPrimitive` / `install_builtins`
  - `T-AGT-008.3` —— `ToolEntry` 双通道声明与 `SkillRunner.run(inputs=…)`
  - L1 组合根注入的 `market_query`（`build_l1_runtime`；生产为 `MarketDb`，集成 rig 同型）
  - L0 `MarketDb.query` / `sync_state` / `sync_coverage` / `_AS_OF_SQL`
- 输出（本任务交付的公共 API / 落盘位置）：
  - `st_agent.l1.builtin.BUILTIN_PRIMITIVES`：`sk_local_sql_v1.0` / `sk_data_status_v1.0`
  - `MarketDb.query(..., *, max_rows=None, timeout_ms=None)`（缺省行为不变）
  - `MarketDb.status_snapshot(*, dataset=None, max_codes=50) -> dict`
  - 留痕沿用 `execution_log/skill-run/<id>.json`，无新分区

## 可关闭的遗留
无——各层册未闭区逐册核过，无「归属＝本任务」或「解封条件＝本任务 `done`」条目（`verify_docs.py --strict` 检查 8 = 0）。

## 假设与前提
无（分组节点不直接交付；两叶的 A1–A5 见各自任务文件）

## 涉及契约
- [02 §5](../../docs/技术架构-v2/02-L0-本地优先基座.md) —— 本地数据面
- [05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md) —— 工具面

## 参考
- `l0/market/db.py:441`（只读白名单已存在）
- `l0/sync_runner.py`（`overview`）

## 备注
**本组几乎全是接线**：只读 SQL 面与数据现状面都已存在，缺的是工具入口与风险分级。
