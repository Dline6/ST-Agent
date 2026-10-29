---
id: T-AGT-013.2
parent: T-AGT-013
title: data_fetch（按域取数原语）
story: ../../docs/PRD-v2-Agent/story-05-local-first.md
arch: ../../docs/技术架构-v2/02-L0-本地优先基座.md
arch_link: "[02 §5](../../docs/技术架构-v2/02-L0-本地优先基座.md)"
priority: P1
milestone: M9
depends_on: [T-AGT-008.3]
status: done
decisions: [D-137, D-145]
verify: "全量 4723 passed / 16 deselected / 2 errors（两例 test_shell 环境性）；新增 tests/l0/test_sync_runner.py 3 例、tests/l1/test_builtin_m9.py::TestDataFetch 6 例；见 执行日志 [T-AGT-013.2]"
---

# T-AGT-013.2 · data_fetch（按域取数原语）

## 目标
让循环对**已登记的数据域**按参数直取，落缓存并更新 `as_of`。

## 验收标准（Given-When-Then）
- GWT-1：Given 本地缺某域数据，When 调用 `data_fetch(domain, params)`，Then 经既有 adapter 取数并落缓存，更新 `as_of`。
- GWT-2：Given 域未登记 adapter，Then `unavailable` 并点名。
- GWT-3：Given 出网，Then 经 `EgressGateway` 留痕。

## 接口面
- 输入（消费的前置接口）：
  - `T-AGT-008.2` / `T-AGT-008.3`：`install_builtins(context=…)` 与参数双通道。
  - `T-AGT-010.2`：`data_status` 的覆盖 / 缺口读面是选择取数域与标的的依据。
  - L0 既有 adapter：`DataSyncRunner` 的 `_make_market` / `_make_info`（`BaoStockSync` / `InfoSync`）——**本任务新增** `DataSyncRunner.fetch_items(items, custom_text=…)` 同步执行入口，复用同一套预检、前置自动补齐、落库与水位回写。
  - L0 `MarketDb` 的 `sync_state` 与 `last_updated_at()`（`as_of` / 最后更新时间来源）；出网经 `EgressGateway`（既有引擎内部已走网关，本任务不新增出网路径）。
  - `T-AGT-013.1` 共用的数据集参数映射与 `L1Runtime.bind_data_sync` 端口。
- 输出（本任务交付的公共 API / 落盘位置）：
  - 新原语 `sk_data_fetch`（`sk_data_fetch_v1.0`，`source="builtin"`）：参数 `dataset`、`scope`、`codes`（对象输入）、`start` / `end`、`quarters`、`force_refresh`；产出逐任务 `results`、`rows`、`watermark`、`as_of`。
  - `DataSyncRunner.fetch_items(...)`（同步、单飞；与后台批次共用「同一时刻只允许一个批次」的锁）。
  - 风险规则 `skill.sk_data_fetch → collaborative-required`（出网/写缓存须确认）。

## 可关闭的遗留
无（同 `T-AGT-013.1` 的逐册核对结论：无归属或解封条件指向本任务者）。

## 假设与前提
- **A1（域词汇）**：`dataset` 取已登记数据集 id；未登记 → `unavailable` 并点名，不猜测相近域。若错 ⇒ 模型选不到域或误取；验证：单测断言未知域 `unavailable` 且原因含该 id。
- **A2（出网与落库）**：取数只经既有 adapter（字段映射、防封、落库、水位都在引擎内），原语不直连网络 / 文件；每次抓取仍是一条 `EgressGateway` 审计。若错 ⇒ 出网绕网关；验证：以真实网关 + 记录型 sender 的替身用例断言审计记录，且代码审计无新 socket / 文件读取。
- **A3（同步阻塞）**：`fetch_items` 在循环线程内同步执行；逐码数据集缺省 `watchlist` 以限定规模，显式 `all_a` 才全市场。若错 ⇒ 单步过长或范围过大；验证：单测覆盖缺省范围解析与全市场显式选择。
- **A4（端口未接）**：更早里程碑的运行时未绑 `DataSyncRunner` → 原语 `unavailable` 并点名未接线，不做假取数。验证：L1 单测以未绑定端口构造运行时。

## 涉及契约
- [02 §5](../../docs/技术架构-v2/02-L0-本地优先基座.md) —— 数据域与取数
- [02 §6](../../docs/技术架构-v2/02-L0-本地优先基座.md) —— 出网审计

## 参考
- `l0/info/fetch.py` / `l0/market/fetch.py`

## 备注
**按域取数**优于裸 HTTP：字段、防封策略、落库口径都在既有 adapter 里，模型只需选域与参数。
