---
id: T-INT-009
parent: null
title: M8 集成关卡 · 自主可跑
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M8
depends_on: [T-AGT-008.1, T-AGT-008.2, T-AGT-008.3, T-AGT-009.1, T-AGT-009.2, T-AGT-009.3, T-AGT-010.1, T-AGT-010.2, T-AGT-011.1, T-AGT-011.2, T-AGT-011.3, T-AGT-012.1, T-AGT-012.2]
status: todo
decisions: [D-137]
verify:
---

# T-INT-009 · M8 集成关卡 · 自主可跑

> **里程碑收口关卡**：`depends_on` 须覆盖本里程碑全部叶子（[检查 7](../tools/verify_docs.py)），由 `render_ledger.py render` 派生后核对。

## 目标
端到端验证：出厂配置下，循环能自主走通一条**它此前答不出**的本地数据问题。

## 验收标准（Given-When-Then）
- GWT-1：Given 出厂配置与已同步数据，When 用户提出「分析最近 ST 股票有哪些热门板块」，Then 循环自主完成：读上下文 → 判数据新旧 → SQL 聚合 → 出证据包，**读操作 0 次确认**。
- GWT-2：Given 数据陈旧或缺失，Then 显式标注并给出下一步，**不假装完成**。
- GWT-3：Given 某步被判需确认，Then 交还并可一键续跑。
- GWT-4：Given 全链路，Then Trace 含每一步的 SQL 原文、参数与 `as_of`。

## 接口面
<④ 对齐时实填（`doing` 起不可留占位），见 [工作流](../工作流.md) 第 ④ 步>
- 输入（消费的前置接口）：
- 输出（本任务交付的公共 API / 落盘位置）：

## 可关闭的遗留
<④ 对齐时实填：扫 [遗留问题](../遗留问题/README.md) 各层册的未闭区，逐条列「归属＝本任务」或「解封条件＝本任务 `done`」的条目（册内 id + 处置）；无命中写 `无`。>

## 假设与前提
<④ 对齐时实填；每条 `A<n>` 含 前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`>

## 涉及契约
- [05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md) —— 循环全链路

## 参考
- 调研报告 §13.6
- 本里程碑 13 个叶子

## 备注
关卡 `depends_on` 由 `render_ledger.py render` 派生后须覆盖 M8 全部叶子（[检查 7](../tools/verify_docs.py)）。
