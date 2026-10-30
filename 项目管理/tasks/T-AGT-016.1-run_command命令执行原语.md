---
id: T-AGT-016.1
parent: T-AGT-016
title: run_command（命令执行原语）
story: ../../docs/PRD-v2-Agent/story-05-local-first.md
arch: ../../docs/技术架构-v2/01-平台共享契约.md
arch_link: "[01 §10](../../docs/技术架构-v2/01-平台共享契约.md)"
priority: P1
milestone: M9
depends_on: [T-AGT-008.3]
status: done
decisions: [D-137, D-148]
verify: 一次性 shell / 超时终止 / 输出上限 / 每次执行留痕；经 exec_command 声明与批准闸门；见执行日志 [T-AGT-016.1]（commit 7dca980）
---

# T-AGT-016.1 · run_command（命令执行原语）

## 目标
实现受限命令执行：超时、输出上限、限定工作目录。

## 验收标准（Given-When-Then）
- GWT-1：Given 已批准 `exec_command`，When 执行，Then 返回输出并按上限截断。
- GWT-2：Given 未声明或未批准，Then 拦截。
- GWT-3：Given 超时，Then 终止进程并显式标注。

## 接口面
- 输入（消费的前置接口）：
  - `T-L1-001.3` / `T-L1-004.3`（done）：`SandboxSession.check_command()` 与 `run_command(command, runner)`——声明 / 批准闸门；原语**只经**它执行，不另造第二条路径。
  - `T-AGT-008.2` / `.3`（done）：`BuiltinPrimitive` 种子、`install_builtins` 的 `requires` 注入；执行期 `ctx.sandbox` 由 L1 执行上下文提供。
  - `T-AGT-014` 批（done）：`FileWorkspace` 与 `workspace` 分区——本原语**不写**该分区（见 A1），仅沿用其「声明即范围」口径。
- 输出（本任务交付的公共 API / 落盘位置）：
  - `src/st_agent/l0/exec/command.py`：`run_command(command, *, timeout_s, max_output_chars, runner=None) -> CommandResult`；`CommandResult` 含 `stdout` / `stderr` / `exit_code` / `timed_out` / `duration_ms` / `truncated` / `cwd`。
  - `src/st_agent/l0/exec/runner.py`：`CommandRunner(store=None)`——每次调用造一次性工作目录、执行、按上限截断输出、超时终止、写 `execution_log/exec-command/<run_id>.json`。
  - `src/st_agent/l1/builtin/exec.py`：`RUN_COMMAND`（base `sk_run_command`，权限 `exec_command`，`requires=("commands",)`）。
  - `l1/runtime.py`：装配 `CommandRunner` 并注入执行上下文键 `commands`。

## 可关闭的遗留
无（开工查六册未闭区，无命中）。

## 假设与前提
- **A1**（不写 `workspace`）——命令的工作目录是一次性临时目录，执行后清理；`workspace` 分区的文件面不对命令进程开放（加密态亦不可直接读写）。若错：命令产物无法跨调用复用。**验证**：用例断言 `Store.list_files("workspace")` 不变、临时目录已清理。
- **A2**（一次性 shell、无 stdin）——`shell=True` 一次性执行；不接受交互输入。若错：需要交互的命令不可用（本期明确不做 `write_stdin`）。**验证**：参数面只暴露 `command` / `timeout_s` / `max_output_chars`。
- **A3**（超时终止）——超时后终止进程、返回 `timed_out=True` 与非零退出码，不把超时当正常输出。若错：长跑命令占住循环。**验证**：`time.sleep` 超时用例。
- **A4**（输出上限）——stdout / stderr 分别按 `max_output_chars` 截断，附 `truncated` 与原始总长。若错：巨型输出撑爆上下文。**验证**：大输出用例。
- **A5**（执行留痕）——每次执行落一条 `execution_log/exec-command/<id>.json`（命令截断 2000 字符、cwd、退出码、耗时、标志、trace）。若错：无审计可查。**验证**：留痕用例。

## 涉及契约
- [01 §10](../../docs/技术架构-v2/01-平台共享契约.md) —— `exec_command` 无 scope 权限

## 参考
- `l1/sandbox/sandbox.py` 的 `check_command` / `run_command`（闸门已有、执行器未接）

## 备注
**不做**交互式 shell（Codex 的 `write_stdin`）——本期只做一次性执行。
