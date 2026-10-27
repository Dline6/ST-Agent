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
status: todo
decisions: [D-137]
verify:
---

# T-AGT-016.1 · run_command（命令执行原语）

## 目标
实现受限命令执行：超时、输出上限、限定工作目录。

## 验收标准（Given-When-Then）
- GWT-1：Given 已批准 `exec_command`，When 执行，Then 返回输出并按上限截断。
- GWT-2：Given 未声明或未批准，Then 拦截。
- GWT-3：Given 超时，Then 终止进程并显式标注。

## 接口面
<④ 对齐时实填（`doing` 起不可留占位），见 [工作流](../工作流.md) 第 ④ 步>
- 输入（消费的前置接口）：
- 输出（本任务交付的公共 API / 落盘位置）：

## 可关闭的遗留
<④ 对齐时实填：扫 [遗留问题](../遗留问题/README.md) 各层册的未闭区，逐条列「归属＝本任务」或「解封条件＝本任务 `done`」的条目（册内 id + 处置）；无命中写 `无`。>

## 假设与前提
<④ 对齐时实填；每条 `A<n>` 含 前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`>

## 涉及契约
- [01 §10](../../docs/技术架构-v2/01-平台共享契约.md) —— `exec_command` 无 scope 权限

## 参考
- `l1/sandbox/sandbox.py` 的 `check_command` / `run_command`（闸门已有、执行器未接）

## 备注
**不做**交互式 shell（Codex 的 `write_stdin`）——本期只做一次性执行。
