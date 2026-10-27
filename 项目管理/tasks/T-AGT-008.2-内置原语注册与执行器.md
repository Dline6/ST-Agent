---
id: T-AGT-008.2
parent: T-AGT-008
title: 内置原语注册与执行器
story: ../../docs/PRD-v2-Agent/story-02-skills-runtime.md
arch: ../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md
arch_link: "[03 §1](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)"
priority: P0
milestone: M8
depends_on: [T-AGT-008.1]
status: todo
decisions: [D-137]
verify:
---

# T-AGT-008.2 · 内置原语注册与执行器

## 目标
L1 在同一注册面登记内置原语并分派执行；权限、沙箱、留痕沿用既有流水线。

## 验收标准（Given-When-Then）
- GWT-1：Given 一条 builtin 描述体，When 经 `SkillRunner` 执行，Then 参数 / 输入 / 输出核验、权限逐项核对、`skill_run` 步与 `execution_log` 与 Skill 同口径。
- GWT-2：Given 未注册执行器，When 执行，Then 显式失败并点名（不静默空跑）。
- GWT-3：Given 原语越界（未声明或未批准的权限），Then 沙箱拦截且**不执行原动作**。

## 接口面
<④ 对齐时实填（`doing` 起不可留占位），见 [工作流](../工作流.md) 第 ④ 步>
- 输入（消费的前置接口）：
- 输出（本任务交付的公共 API / 落盘位置）：

## 可关闭的遗留
<④ 对齐时实填：扫 [遗留问题](../遗留问题/README.md) 各层册的未闭区，逐条列「归属＝本任务」或「解封条件＝本任务 `done`」的条目（册内 id + 处置）；无命中写 `无`。>

## 假设与前提
<④ 对齐时实填；每条 `A<n>` 含 前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`>

## 涉及契约
- [03 §1](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md) —— 执行器注册与分派
- [01 §10](../../docs/技术架构-v2/01-平台共享契约.md) —— 权限声明的逐项核对

## 参考
- `l1/runner/runner.py`（`register_executor`）
- `l1/sandbox/sandbox.py`（三类出口）

## 备注
官方 Pack 目前一律 `permissions=()`——原语**不得**沿用这条，凡碰文件 / 网络 / 命令者必须显式声明并经批准。
