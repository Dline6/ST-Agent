---
id: T-AGT-014
parent: null
title: 文件读写原语
story: ../../docs/PRD-v2-Agent/story-05-local-first.md
arch: ../../docs/技术架构-v2/01-平台共享契约.md
arch_link: "[01 §10](../../docs/技术架构-v2/01-平台共享契约.md)"
priority: P1
milestone: M9
depends_on: [T-AGT-008.1, T-AGT-008.3]
status: done
decisions: [D-137]
verify: 三叶合成验收：文件原语可读写 workspace、写类需确认、越界/未批准拦截；见执行日志 [T-AGT-014.1]–[.3]（commit 69b6d3b）
---

# T-AGT-014 · 文件读写原语

> **分组节点**：本节点不直接交付，交付由 [`T-AGT-014.1`](T-AGT-014.1-契约local_write与工作区分区.md) / [`T-AGT-014.2`](T-AGT-014.2-文件读原语.md) / [`T-AGT-014.3`](T-AGT-014.3-文件写原语.md) 承接；父状态与父 `depends_on` 由 `render_ledger.py render` 按 children 派生。

## 目标
让 Agent 能读用户给的文件、也能把产出落成文件交付。

## 验收标准（Given-When-Then）
- 分组节点不单独验收——由契约 / 读 / 写三叶合成。

## 接口面
- 输入（消费的前置接口）：
  - `T-AGT-008.1` / `T-AGT-008.3`——`source="builtin"` 与内置原语注册面、实参双通道（`BUILTIN_PRIMITIVES` / `install_builtins` / `ToolEntry`）。
  - `T-AGT-013.*`——同形原语先例（权限声明、`unavailable` 点名、组合根注入）。
  - L0 `Store` 的分区读写面（`put` / `get` / `list_files`）与存储根（`Store.root`）。
  - [09 §6](../../docs/技术架构-v2/09-生态与分享.md) 的导入收件目录（`<存储根>/inbox`）。
- 输出（本任务交付的公共 API / 落盘位置）：
  - 01 §10 第四类权限 `local_write:<path-scope>`；02 §2.1 第八个逻辑分区 `workspace`（落盘 `<存储根>/workspace/`）并进默认备份集。
  - `BUILTIN_PRIMITIVES` 增 `sk_file_read` / `sk_file_glob` / `sk_file_grep` / `sk_file_write`；L0 `FileWorkspace`（工作区 + 收件目录的读写面）。
  - 内置声明的批准初态落 `config/skill-permissions/<base>.json`。

## 可关闭的遗留
无（六册未闭区逐册核过，无「归属＝本任务」或「解封条件＝本任务 `done`」的条目；检查 8 = 0）。

## 假设与前提
- **A1** 文件范围＝`workspace` 分区 + 导入收件目录（而非任意路径）。若错的影响：读取面需改根模型，返工集中在 `FileWorkspace` 与四条原语的声明。验证方式：`.2` 的越界用例与 `test_file_workspace` 的路径边界用例。
- **A2** 内置原语在安装期「声明即批准」，用户拒绝态保留。若错的影响：文件原语在无审批 UI 时不可用，须补审批面或改走设置开关。验证方式：`test_install_seeds_first_party_approvals_and_keeps_rejections`。
- **A3** `workspace` 并入默认备份集；旧归档不含该分区时按空处理、格式版本不升。若错的影响：备份兼容面。验证方式：`tests/l0/test_backup.py` 的往返用例。

## 涉及契约
- [01 §10](../../docs/技术架构-v2/01-平台共享契约.md) —— 权限模型
- [02 §2.1](../../docs/技术架构-v2/02-L0-本地优先基座.md) —— 存储分区

## 参考
- 调研报告 §13.3 的 N3

## 备注
**本组牵动两处契约**：权限模型只有三类动作（无 `local_write`），且「写到哪」在架构里没有定义。
