---
id: T-AGT-014.3
parent: T-AGT-014
title: 文件写原语
story: ../../docs/PRD-v2-Agent/story-05-local-first.md
arch: ../../docs/技术架构-v2/01-平台共享契约.md
arch_link: "[01 §10](../../../../docs/技术架构-v2/01-平台共享契约.md)"
priority: P1
milestone: M9
depends_on: [T-AGT-014.2]
status: done
decisions: [D-137]
verify: 写入 workspace + 未批准拦截 + 并发串行全绿；见执行日志 [T-AGT-014.3]（commit 69b6d3b）
---

# T-AGT-014.3 · 文件写原语

## 目标
提供 `file_write`，落工作区分区，进备份范围。

## 验收标准（Given-When-Then）
- GWT-1：Given 工作区内路径，When 写，Then 落盘且产品能找到该文件。
- GWT-2：Given 未声明或未批准 `local_write`，Then 拦截。
- GWT-3：Given 并发写同一文件，Then 串行化或显式拒绝。

## 接口面
- 输入（消费的前置接口）：
  - `T-AGT-014.2`——`FileWorkspace` 与读原语。
  - `T-AGT-014.1`——`local_write:<workspace/**>` 与 `workspace` 分区。
  - L6 出厂风险清单 `DEFAULT_RISK_GRADING`（`l6/authorization.py`）。
  - L3 循环的 `approved_permissions` 入参（`l3/runtime/loop.py`）与组合根 `_AgentInvestigator`。
- 输出（本任务交付的公共 API / 落盘位置）：
  - `sk_file_write`（声明 `local_write:<workspace/**>`）；落盘 `workspace` 分区（`Store.put`，02 §2.4 分区锁串行化）。
  - `install_builtins(..., permissions=SkillPermissionBook)` 的内置声明批准初态；`resolve_approved` 支持的三种批准供给形态。

## 可关闭的遗留
无（六册未闭区逐册核过，无归属＝本任务的条目）。

## 假设与前提
- **A1** 写只落 `workspace`，收件目录与任意路径拒写。若错的影响：写穿出工作区。验证方式：`test_builtin_files` 的 inbox / 绝对路径用例。
- **A2** 未声明或未批准 `local_write` 时拦截且不写盘。若错的影响：越权写。验证方式：未批准写用例 + `TestLocalWriteBoundary`。
- **A3** 并发写同一文件由 `Store` 分区锁串行化（不丢清单条目）。若错的影响：并发写损坏分区。验证方式：并发写用例。
- **A4** 内置原语安装期批准，拒绝态保留；用户对写类动作的逐次确认由 L6 分级（`collaborative-required`）承担。若错的影响：写操作被静默自动执行或永远不可用。验证方式：批准播种用例 + L6 分类用例。

## 涉及契约
- [01 §10](../../../../docs/技术架构-v2/01-平台共享契约.md) —— `local_write`
- [02 §2.1](../../../../docs/技术架构-v2/02-L0-本地优先基座.md) —— 工作区分区

## 参考
- `l1/sandbox/sandbox.py` 需新增 `write_file` 核对

## 备注
**最高风险的一叶**：写盘会改变用户环境。默认须显式开启，且写入范围限定在工作区分区。
