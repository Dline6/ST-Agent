---
id: T-AGT-014.1
parent: T-AGT-014
title: 契约：local_write 与工作区分区
story: ../../docs/PRD-v2-Agent/story-05-local-first.md
arch: ../../docs/技术架构-v2/01-平台共享契约.md
arch_link: "[01 §10](../../docs/技术架构-v2/01-平台共享契约.md)"
priority: P1
milestone: M9
depends_on: [T-AGT-008.1]
status: done
decisions: [D-137]
verify: 四动作解析 / 八分区 / 五分区备份全绿；见执行日志 [T-AGT-014.1]（commit 69b6d3b）
---

# T-AGT-014.1 · 契约：local_write 与工作区分区

## 目标
权限动作增 `local_write:<path-scope>`；存储分区增**用户工作区 / 导出目录**。

## 验收标准（Given-When-Then）
- GWT-1：Given [01 §10](../../docs/技术架构-v2/01-平台共享契约.md)，When 增 `local_write`，Then 权限解析、沙箱核对、批准流三处同口径。
- GWT-2：Given [02 §2.1](../../docs/技术架构-v2/02-L0-本地优先基座.md)，When 增工作区分区，Then 独立导出 / 独立清空成立，备份口径显式。
- GWT-3：Given 存量三类权限，Then 行为逐字节不变。

## 接口面
- 输入（消费的前置接口）：
  - [01 §10](../../docs/技术架构-v2/01-平台共享契约.md) 的 `parse_permission` / `validate_permissions`（`contracts/registry_types.py`）。
  - `SkillSandbox` 的会话核对面（`l1/sandbox/sandbox.py`）与批准账本 `SkillPermissionBook`。
  - [02 §2.1](../../docs/技术架构-v2/02-L0-本地优先基座.md) 的 `PARTITIONS` 封闭注册表与 [02 §8.1](../../docs/技术架构-v2/02-L0-本地优先基座.md) 的 `BACKUP_PARTITIONS`。
- 输出（本任务交付的公共 API / 落盘位置）：
  - `PermissionAction` 增 `local_write`；`PERMISSION_KINDS` 增对应中性说明；`SkillSandbox` 增 `check_write` / `write_file`。
  - `Partition("workspace", ...)`（第 8 个）；`BACKUP_PARTITIONS` 增 `workspace`；落盘 `<存储根>/workspace/`，随存储加密 / 备份 / 清空。

## 可关闭的遗留
无（六册未闭区逐册核过，无归属＝本任务的条目）。

## 假设与前提
- **A1** 存量三类权限行为逐字节不变（正则只扩一个动作分支）。若错的影响：既有 Skill / MCP 的权限解析回归。验证方式：`tests/contracts/test_capability_registry.py` 的四动作解析 + 既有套件。
- **A2** 新分区可独立导出 / 清空，且备份默认包含它。若错的影响：备份恢复与存储页分区列表。验证方式：`tests/l0/test_storage.py` / `test_backup.py` / 设置页集成用例。
- **A3** `local_write` 与 `local_read` 共用同一套路径作用域语义（`path_in_scope`）。若错的影响：写侧判界与读侧不一致。验证方式：`tests/l1/test_sandbox.py::TestLocalWriteBoundary`。

## 涉及契约
- [01 §10](../../docs/技术架构-v2/01-平台共享契约.md) —— 四类权限动作
- [02 §2.1](../../docs/技术架构-v2/02-L0-本地优先基座.md) —— 第八个逻辑分区

## 参考
- `contracts/registry_types.py:163/166`（三动作 + 硬编码正则）

## 备注
**D-6 拍定**：写入落**新分区**（不走 `data_cache` 子目录、不落系统用户目录）——前者会与缓存语义混淆，后者绕开加密与一键备份。
