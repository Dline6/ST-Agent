---
id: T-AGT-016
parent: null
title: 命令执行
story: ../../docs/PRD-v2-Agent/story-05-local-first.md
arch: ../../docs/技术架构-v2/01-平台共享契约.md
arch_link: "[01 §10](../../docs/技术架构-v2/01-平台共享契约.md)"
priority: P1
milestone: M9
depends_on: [T-AGT-008.3]
status: done
decisions: [D-137, D-148]
verify: 两叶合成：受限命令执行 + 默认关闭的本地命令开关；见执行日志 [T-AGT-016.1] / [T-AGT-016.2]（commit 待回填）
---

# T-AGT-016 · 命令执行

> **分组节点**：本节点不直接交付，交付由 [`T-AGT-016.1`](T-AGT-016.1-run_command命令执行原语.md) / [`T-AGT-016.2`](T-AGT-016.2-命令能力声明与开启.md) 承接；父状态与父 `depends_on` 由 `render_ledger.py render` 按 children 派生。

## 目标
让 Agent 在本机执行受限命令——「系统没预设也能干」的终极手段。

## 验收标准（Given-When-Then）
- 分组节点不单独验收——由执行器与能力声明两叶合成。

## 接口面
- 输入（消费的前置接口）：
  - `T-AGT-008.2` / `.3`（done）：内置原语注册面与参数通道——`l1/builtin.BuiltinPrimitive`、`install_builtins` 的 `requires` 注入。
  - `T-L1-001.3` / `T-L1-004.3`（done）：`SkillSandbox.check_command` 与 `SandboxSession.run_command`——命令出口的声明 / 批准闸门已就绪，只缺执行器。
  - `T-AGT-014` 批（done）：`workspace` 分区与 `FileWorkspace`——「声明即范围、不另造路径判据」的既有口径。
  - `T-AGT-015.3`（done）：设置区视图 + 配置族 + 统一落值面——开关类配置的接入先例。
- 输出（本任务交付的公共 API / 落盘位置）：
  - `st_agent.l0.exec.command.run_command` 与 `CommandResult`（超时 / 输出上限 / 一次性工作目录）。
  - `st_agent.l1.builtin.exec.RUN_COMMAND`（`sk_run_command`）注册进 `BUILTIN_PRIMITIVES`。
  - `01 §7` 条目 `local-exec/enabled`（缺省 `false`）及 `st_agent.l0.exec.exec_config` 读写面。
  - 能力声明 `local-exec`（`02 §7`）。
  - 设置区「本地命令」视图（`/api/settings/command`）。
  - 每次执行留痕 `execution_log/exec-command/<id>.json`。

## 可关闭的遗留
无（开工查 [遗留问题](../遗留问题/README.md) 六册未闭区，无「归属＝本任务」或「解封条件＝T-AGT-016 done」的条目；检查 8 = 0）。

## 假设与前提
- **A1**（执行产物不落 `workspace` 分区）——命令在一次性本机临时目录中执行，执行后清理；需要留存的产物由 Agent 经 `sk_file_write` 落盘。若错：多步命令之间无法共享工作目录里的中间文件（后续任务可另行放开）。**验证**：执行用例断言临时目录已清理、`Store.list_files("workspace")` 不变。
- **A2**（一次性 shell 语义、无交互 stdin）——Windows 走 `cmd /c`、POSIX 走 `/bin/sh -c`；超时终止直接子进程并显式标注 `timed_out`，不承诺清理孙进程（进程级隔离未实现，沿用 `02 §6` 已知边界）。若错：Windows 上孙进程可能残留。**验证**：超时用例（`time.sleep` + 1 秒上限）断言 `timed_out=True` 且进程已终止。
- **A3**（开关缺省关）——`local-exec/enabled` 缺省 `false`；未开启时原语 `unavailable` 且**不执行任何命令**。若错：默认安全边界失效。**验证**：关闭态用例断言注入的 runner 零调用、返回 `unavailable`。
- **A4**（能力离线档取 `full`）——命令是本机能力、断网可用；设置面另显「默认关闭」。若按 GWT-1 字面登记 `none`，会把本机能力误标为「必须联网」。若错：与 GWT-1 的字面表述不一致（见 ④ 对齐记录）。**验证**：`offline_report()` 断言 `local-exec` 在 `full` 组；④ 由负责人确认。
- **A5**（留痕口径）——每次执行记命令文本（截断 2000 字符）、工作目录、退出码、耗时、超时 / 截断标志与关联 trace；落 `execution_log`（不进备份、按留存策略清理）。若错：审计信息不足，或本地日志含用户命令中的敏感串（仅本机）。**验证**：留痕用例读 `execution_log/exec-command/`，断言字段齐全且超长命令被截断。

## 涉及契约
- [01 §10](../../docs/技术架构-v2/01-平台共享契约.md) —— `exec_command`

## 参考
- 调研报告 §13.1
- `l1/sandbox/sandbox.py:302/314`

## 备注
**最高风险的一组**：默认禁用，显式开启，且每次执行留痕。
