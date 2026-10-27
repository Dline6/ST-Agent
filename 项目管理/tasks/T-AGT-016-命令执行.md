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
status: todo
decisions: [D-137]
verify:
---

# T-AGT-016 · 命令执行

> **分组节点**：本节点不直接交付，交付由 [`T-AGT-016.1`](T-AGT-016.1-run_command命令执行原语.md) / [`T-AGT-016.2`](T-AGT-016.2-命令能力声明与开启.md) 承接；父状态与父 `depends_on` 由 `render_ledger.py render` 按 children 派生。

## 目标
让 Agent 在本机执行受限命令——「系统没预设也能干」的终极手段。

## 验收标准（Given-When-Then）
- 分组节点不单独验收——由执行器与能力声明两叶合成。

## 接口面
<④ 对齐时实填（`doing` 起不可留占位），见 [工作流](../工作流.md) 第 ④ 步>
- 输入（消费的前置接口）：
- 输出（本任务交付的公共 API / 落盘位置）：

## 可关闭的遗留
<④ 对齐时实填：扫 [遗留问题](../遗留问题/README.md) 各层册的未闭区，逐条列「归属＝本任务」或「解封条件＝本任务 `done`」的条目（册内 id + 处置）；无命中写 `无`。>

## 假设与前提
<④ 对齐时实填；每条 `A<n>` 含 前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`>

## 涉及契约
- [01 §10](../../docs/技术架构-v2/01-平台共享契约.md) —— `exec_command`

## 参考
- 调研报告 §13.1
- `l1/sandbox/sandbox.py:302/314`

## 备注
**最高风险的一组**：默认禁用，显式开启，且每次执行留痕。
