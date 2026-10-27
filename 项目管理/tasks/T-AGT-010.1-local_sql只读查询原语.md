---
id: T-AGT-010.1
parent: T-AGT-010
title: local_sql（只读查询原语）
story: ../../docs/PRD-v2-Agent/story-05-local-first.md
arch: ../../docs/技术架构-v2/02-L0-本地优先基座.md
arch_link: "[02 §5](../../docs/技术架构-v2/02-L0-本地优先基座.md)"
priority: P0
milestone: M8
depends_on: [T-AGT-008.3]
status: todo
decisions: [D-137]
verify:
---

# T-AGT-010.1 · local_sql（只读查询原语）

## 目标
暴露只读 SQL 原语，复用 `MarketDb.query` 的白名单与只读连接。

## 验收标准（Given-When-Then）
- GWT-1：Given 一条 `SELECT`，When 调用，Then 返回列与行，并把 **SQL 原文、绑定参数、`as_of`、行数**记入 Trace。
- GWT-2：Given `INSERT` / `UPDATE` / DDL / 多语句，Then `validation_failed` 且**不执行**。
- GWT-3：Given 超出行数或执行时限上限，Then 显式标注截断并给出继续查询的方式。
- GWT-4：Given 库缺失，Then `unavailable` 并标注首次同步未完成。

## 接口面
<④ 对齐时实填（`doing` 起不可留占位），见 [工作流](../工作流.md) 第 ④ 步>
- 输入（消费的前置接口）：
- 输出（本任务交付的公共 API / 落盘位置）：

## 可关闭的遗留
<④ 对齐时实填：扫 [遗留问题](../遗留问题/README.md) 各层册的未闭区，逐条列「归属＝本任务」或「解封条件＝本任务 `done`」的条目（册内 id + 处置）；无命中写 `无`。>

## 假设与前提
<④ 对齐时实填；每条 `A<n>` 含 前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`>

## 涉及契约
- [02 §5](../../docs/技术架构-v2/02-L0-本地优先基座.md) —— 只读查询面
- [01 §4](../../docs/技术架构-v2/01-平台共享契约.md) —— SQL 原文进 Trace

## 参考
- `l0/market/db.py` 的 `query(sql, params)`

## 备注
**可解释硬要求**（铁律 5）：SQL 原文与参数必须进 Trace，否则「查了库」不可复核。行数与时限上限属实现口径，但**必须显式**，不得静默截断。
