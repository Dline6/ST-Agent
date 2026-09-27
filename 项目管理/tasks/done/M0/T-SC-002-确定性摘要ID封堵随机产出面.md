---
id: T-SC-002
parent: null
title: 确定性摘要 ID 封堵随机产出面（generate 拒绝）
story: ../../docs/PRD-v2-Agent/10-platform-capabilities.md
arch: ../../docs/技术架构-v2/01-平台共享契约.md
arch_link: "[01 §1](../../../../docs/技术架构-v2/01-平台共享契约.md)"
priority: P0
milestone: M0
depends_on: [T-SC-001.1, T-L0-011]
status: done
decisions: [D-034]
verify: pytest 1090 passed / 2 deselected / 0 failed · verify_docs --strict 检查 1–8 全 0 · 红-绿已验（去掉 AnnouncementId 的 generated_by → 4 类参数表用例 FAIL，复原即绿）· 产出链路零回归（L0 信息面四域 + L1 官方 Pack 全绿）· 决策 D-034 · 日志 [T-SC-002]
---

# T-SC-002 · 确定性摘要 ID 封堵随机产出面（generate 拒绝）

## 目标

01 §1 已把 `announcement_id`（业务键摘要）与 `dataset_snapshot_id`（水位摘要）定义为**确定性摘要**，但契约类型上**没有对应的拒绝**——`AnnouncementId.generate()` / `DatasetSnapshotId.generate()` 至今会返回**随机 uuid4**，即存在一条与契约定义相悖的产出面（幂等/可复核性一旦走它即失效）。本任务复用既有的 `_generated_by` 机制（`StockId` / `FlowId` 已用）把这两类也封住，并把「不由本机随机生成」写进 01 §1，使文档与代码同源可对。

## 验收标准（Given-When-Then）

- **GWT-1 两类拒绝**：Given `AnnouncementId` / `DatasetSnapshotId`，When 调 `generate()`，Then 抛 `ContractViolation` 且消息点名产生方（L0 数据缓存）——ID 只能经 `of()` 由业务键摘要 / 水位摘要构造。
- **GWT-2 不误伤其余**：Given 其余 12 类契约 ID，When 逐一 `generate()`，Then 行为与现状一致——本机产生的仍返回全局唯一新 ID；全库共 **4 类**拒绝（`stock_id` / `flow_id` / 本批两类）。
- **GWT-3 产出链路零回归**：Given L0 的 ID 产出面（信息面 `digest_id("ann"/"qa", …)`、`MarketDb.snapshot_id()`）与 L1 的 `snapshot_ref`，When 跑全量用例，Then 全绿——**无任何调用方依赖这两类的 `generate()`**（2026-09-27 已 grep 核实：`src/` 零命中）。
- **GWT-4 文档与代码同源**：Given 01 §1 表，When 读这两行，Then 明写「**不由本机随机生成**」（与 `_generated_by` 的实现口径一致）；契约测试的两处 parametrize 名单同步收缩，并新增拒绝用例。

## 接口面

- 输入（消费的前置接口）：
  - T-SC-001.1 [`identifiers.py`](../../../../src/st_agent/contracts/identifiers.py)：`PlatformId.generate()`（`_generated_by` 非空 → 抛 `ContractViolation`，值即拒绝说明）、`_make_id_type(kind, prefix, *, generated_by=…)`、`ID_ALIASES` / `ID_REGISTRY`；[`errors.py`](../../../../src/st_agent/contracts/errors.py) `ContractViolation`
  - T-L0-010.1 `digest_id`（`announcement_id` 的真实产出路径）· T-L0-011 `MarketDb.snapshot_id()`（`dataset_snapshot_id` 的真实产出路径）——**本任务不改它们**，仅确认与之不冲突
- 输出（本任务交付的公共 API / 落盘位置）：
  - `AnnouncementId` / `DatasetSnapshotId` 增 `generated_by`：`generate()` 抛 `ContractViolation`；`of(raw)` 构造路径**不变**
  - [01 §1](../../../../docs/技术架构-v2/01-平台共享契约.md) 两行补「不由本机随机生成」措辞（表结构、14 类清单、`ID_REGISTRY` 均不变）
  - [`tests/contracts/test_identifiers_result_envelope.py`](../../../../tests/contracts/test_identifiers_result_envelope.py)：两处 parametrize 名单收缩 + 新增拒绝用例
  - **落盘零变更**（纯契约类型行为 + 文档）

## 可关闭的遗留

- **无**——本项在 [执行日志](../../../执行日志.md) `[T-L0-011]` 以「观察（未立项）」留痕，**未进任何遗留册**。按册规开工查：L0 册未闭区（`F1`–`F4`）与 [L1 册](../../../遗留问题/L1-遗留问题.md) 未闭区条目，归属与解封条件**均非本任务**。

## 假设与前提

- **A1** 全仓无调用方依赖这两类的 `generate()` / 若错（存在调用方）：须先改调用方，或该调用方本身就是待修的缺陷 / 验证：`grep -rn "\.generate()" src tests` 逐条核对（2026-09-27 已核：`src/` 仅 `TraceId` / `SkillRunId` / `ChangeId` / `TrialId` 四处，与本批两类无关）。
- **A2** 既有 `_generated_by` 机制可直接复用，无需新机制 / 若错（须区分「本机随机产生」「外部产生」以外的第三态）：须扩 `PlatformId.generate()` 的拒绝模型 / 验证：`test_not_locally_generated_types_reject_generate`（4 类**参数表**——`StockId` / `FlowId` 两条既有断言并入此表，同形）+ `test_generation_partition_covers_all_kinds`（本机产生 10 + 拒绝 4 = §1 的 14 类）。
- **A3** 收紧**不构成跨层 breaking**——新增的拒绝语义只影响不存在的调用方 / 若错（某层正依赖随机产出）：属契约级变更，须按工作流 ⑦ 先改 01 → 各层文档 → 代码 / 验证：`git diff --name-only` 无 `src/st_agent/l0|l1/` 改动。
- **A4** 文档侧只补措辞、不动表结构与 14 类清单 / 若错（须增删类型）：须同步 `ID_REGISTRY` 与 `ID_KINDS` 断言 / 验证：GWT-4 + `test_registry_covers_contract_table`（14 类）不变。

## 涉及契约（链接到具体小节）

- [01-平台共享契约 §1](../../../../docs/技术架构-v2/01-平台共享契约.md) 标识体系 —— `announcement_id` / `dataset_snapshot_id` 两行补「不由本机随机生成」（与 `stock_id` 的既有处置同形；`stock_id` 行亦称「交易所代码规范映射」，其拒绝措辞在实现侧 `generated_by`）

## 参考

- Story（What）：[10-platform-capabilities](../../../../docs/PRD-v2-Agent/10-platform-capabilities.md)
- 触发：[执行日志](../../../执行日志.md) `[T-L0-011]` 遗留②「观察（未立项）」——2026-09-27 由负责人定案收紧
- 前序任务：[`T-SC-001.1`](T-SC-001.1-标识体系与ResultEnvelope.md)（标识体系落地）· [`T-L0-011`](T-L0-011-数据快照ID有界化与消费方直取.md)（`snapshot_id()` 契约化）
- 决策：[`D-033`](../../../决策日志.md)（数据快照 ID 契约化）· [`D-030`](../../../决策日志.md) / [`D-031`](../../../决策日志.md)（确定性摘要口径的来源）

## 备注

选型（拒绝的范围与措辞）记 [D-034](../../../决策日志.md)。里程碑取 M0，故须把本任务补进 [`T-INT-001`](T-INT-001-M0集成关卡骨架打通冒烟.md) 的 `depends_on`（检查 7 把关）。实现细节不写此处，留给代码 / commit / 执行日志。
