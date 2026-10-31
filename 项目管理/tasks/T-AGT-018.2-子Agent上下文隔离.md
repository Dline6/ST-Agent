---
id: T-AGT-018.2
parent: T-AGT-018
title: 子 Agent（上下文隔离）
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P2
milestone: ""
depends_on: [T-AGT-018.1]
status: todo
decisions: [D-137, D-150]
verify:
---

# T-AGT-018.2 · 子 Agent（上下文隔离）

> **悬空任务（2026-10-31，[D-150](../决策日志.md)）**：本叶已**移出 M10**、`milestone` 置空，待负责人择时另立里程碑。悬空任务**不入就绪集**（[`render_ledger.compute_status`](../tools/render_ledger.py)），故不会被下个会话当作可开工项领走；M10 关卡 [`T-INT-011`](T-INT-011-M10集成关卡长任务运行时.md) 的 `depends_on` 与 GWT 亦已不再包含本叶。

## 目标
探索型任务在隔离子上下文里跑，只回传结论与引用。

## 验收标准（Given-When-Then）
- GWT-1：Given 探索型任务，When 派生子 Agent，Then 其上下文不进主窗，仅回传结论与引用。
- GWT-2：Given 子 Agent 失败，Then 主循环可按规则改选。
- GWT-3：Given 预算，Then 子 Agent 的调用计入总上界。

## 接口面
<④ 对齐时实填（`doing` 起不可留占位），见 [工作流](../工作流.md) 第 ④ 步>
- 输入（消费的前置接口）：
- 输出（本任务交付的公共 API / 落盘位置）：

## 可关闭的遗留
<④ 对齐时实填：扫 [遗留问题](../遗留问题/README.md) 各层册的未闭区，逐条列「归属＝本任务」或「解封条件＝本任务 `done`」的条目（册内 id + 处置）；无命中写 `无`。>

## 假设与前提
<④ 对齐时实填；每条 `A<n>` 含 前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`>

## 涉及契约
- [05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md) —— 子 Agent 与预算

## 参考
- Codex 的 `spawn_agent` 一族

## 备注
本期只做**一层**子 Agent，不做 Agent 图 / 角色 / 消息板。
