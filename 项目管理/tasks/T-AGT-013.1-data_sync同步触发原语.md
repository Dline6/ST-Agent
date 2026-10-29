---
id: T-AGT-013.1
parent: T-AGT-013
title: data_sync（同步触发原语）
story: ../../docs/PRD-v2-Agent/story-05-local-first.md
arch: ../../docs/技术架构-v2/02-L0-本地优先基座.md
arch_link: "[02 §5](../../docs/技术架构-v2/02-L0-本地优先基座.md)"
priority: P1
milestone: M9
depends_on: [T-AGT-010.2]
status: done
decisions: [D-137, D-145]
verify: "全量 4723 passed / 16 deselected / 2 errors（两例 test_shell 环境性）；新增 tests/l1/test_builtin_m9.py::TestDataSync、tests/integration/test_m5_autonomy.py 绑定用例；见 执行日志 [T-AGT-013.1]"
---

# T-AGT-013.1 · data_sync（同步触发原语）

## 目标
让循环按需触发同步，并在产出里如实标注「同步中 / 已完成 / 失败」。

## 验收标准（Given-When-Then）
- GWT-1：Given 数据陈旧，When 调用 `data_sync`，Then 触发同步并返回运行态；等待策略可指定。
- GWT-2：Given 写类动作，Then 授权闸门恒需确认。
- GWT-3：Given 已有运行中的批次，When 再次触发，Then 显式拒绝而非并发。

## 接口面
- 输入（消费的前置接口）：
  - `T-AGT-008.2` / `T-AGT-008.3`：`st_agent.l1.builtin.install_builtins(context=…)` 的注入点与 `BuiltinPrimitive`（标量走 `parameters`、对象走 `input_schema`）。
  - `T-AGT-012.1`：运行期授权判据 `RuntimeAuthorization.decide("skill.<base>")`；本原语**不登记**为 `autonomous-ok`，由 `skill.` 通则判必须协作。
  - `T-AGT-010.2`：`data_status` 产出的逐任务行（`task_key` / 数据域 / 最后成功时间）是模型选择同步目标的依据。
  - L0 `st_agent.l0.sync_runner.DataSyncRunner.start_items(items, custom_text=…)` / `job()` / `cancel()`；批次状态与结果读面不变。
  - `T-AGT-013.2` 共用的数据集参数映射（`dataset` / `scope` / `codes` / 时间 / `force_refresh`）。
  - M5 组合根 `build_m5_runtime` 造出的 `DataSyncRunner` **单例**；经 `L1Runtime.bind_data_sync(runner)` 注入。
- 输出（本任务交付的公共 API / 落盘位置）：
  - 新原语 `sk_data_sync`（`sk_data_sync_v1.0`，`source="builtin"`）：参数 `dataset`、`scope`、`codes`（对象输入）、`start` / `end`、`quarters`、`force_refresh`、`wait_seconds`；产出 `job_id` / `state` / `order` / `auto_added` / `estimate` / `results` / `as_of`。
  - `L1Runtime.bind_data_sync(runner)` 鸭子端口（未绑定 → 原语 `unavailable`，不臆造运行态）。
  - 留痕沿用 L1 `skill_run` 与 L0 `sync_state`；不新增落盘格式。

## 可关闭的遗留
无（逐册核过未闭区：L0 余 `C3`/`C5`/`D1–D3` 均人决、L5 余 `A1` 属人，其余册 0 条；无归属或解封条件指向本任务者）。

## 假设与前提
- **A1（域词汇）**：同步目标以数据集 id 表达（[`DATASET_IDS`](../../src/st_agent/l0/market/datasets.py) 的 32 项）；逐码数据集缺省 `scope="watchlist"`，显式 `all_a` 才全市场。若错 ⇒ 模型与工具词汇不一致、可能误触发全市场长跑；验证：单测断言未知数据集 → `unavailable` 点名，逐码缺省解析为关注池。
- **A2（风险类）**：同步是写动作，必须协作——缺省清单不登记 `skill.sk_data_sync` 为可自主；验证：L6 用例断言 `classify("skill.sk_data_sync") == "collaborative-required"`，L3 闸门在 `autonomous` 档回 `needs-confirmation`。
- **A3（等待策略）**：缺省不等待，返回 `running` + `job_id`；`wait_seconds > 0` 时最多等该秒数，超时仍如实回运行态（不伪装完成）。若错 ⇒ 循环多一轮或阻塞过久；验证：单测覆盖不等待、等待至完成、等待超时三态。

## 涉及契约
- [02 §5](../../docs/技术架构-v2/02-L0-本地优先基座.md) —— 同步批次
- [05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md) —— 写类动作的风险类

## 参考
- `l0/sync_runner.py` 的 `start_items` / `job` / `cancel`

## 备注
**它是写操作**：风险级不得低于 `collaborative-required`，且不得因「只是同步」而被放宽。
