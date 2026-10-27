---
id: T-INT-010
parent: null
title: M9 集成关卡 · 开放能力面
story: ../../docs/PRD-v2-Agent/story-05-local-first.md
arch: ../../docs/技术架构-v2/02-L0-本地优先基座.md
arch_link: "[02 §5](../../docs/技术架构-v2/02-L0-本地优先基座.md)"
priority: P1
milestone: M9
depends_on: [T-AGT-013.1, T-AGT-013.2, T-AGT-013.3, T-AGT-014.1, T-AGT-014.2, T-AGT-014.3, T-AGT-015.1, T-AGT-015.2, T-AGT-015.3, T-AGT-016.1, T-AGT-016.2]
status: todo
decisions: [D-137]
verify:
---

# T-INT-010 · M9 集成关卡 · 开放能力面

> **里程碑收口关卡**：`depends_on` 须覆盖本里程碑全部叶子（[检查 7](../tools/verify_docs.py)），由 `render_ledger.py render` 派生后核对。

## 目标
端到端验证：本地没有的数据，循环能自己补上，并如实标注来源。

## 验收标准（Given-When-Then）
- GWT-1：Given 本地缺某只 ST 股的最新公告与龙虎榜，When 用户提问，Then 循环经 `data_fetch` 或 `web_search` 取数、落缓存并回答。
- GWT-2：Given 出网关闭或未配置，Then 显式 `unavailable` 并给原因，不假装答过。
- GWT-3：Given 写入文件，Then 落在工作区分区且用户能找到。
- GWT-4：Given 全链路，Then 每条外网数据都带来源与时间，且出网有审计。

## 接口面
<④ 对齐时实填（`doing` 起不可留占位），见 [工作流](../工作流.md) 第 ④ 步>
- 输入（消费的前置接口）：
- 输出（本任务交付的公共 API / 落盘位置）：

## 可关闭的遗留
<④ 对齐时实填：扫 [遗留问题](../遗留问题/README.md) 各层册的未闭区，逐条列「归属＝本任务」或「解封条件＝本任务 `done`」的条目（册内 id + 处置）；无命中写 `无`。>

## 假设与前提
<④ 对齐时实填；每条 `A<n>` 含 前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`>

## 涉及契约
- [02 §5 / §6 / §7](../../docs/技术架构-v2/02-L0-本地优先基座.md) —— 取数、出网与能力声明

## 参考
- 本里程碑 11 个叶子

## 备注
关卡 `depends_on` 须覆盖 M9 全部叶子（[检查 7](../tools/verify_docs.py)）。
