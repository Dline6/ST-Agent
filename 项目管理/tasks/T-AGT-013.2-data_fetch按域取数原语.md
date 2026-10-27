---
id: T-AGT-013.2
parent: T-AGT-013
title: data_fetch（按域取数原语）
story: ../../docs/PRD-v2-Agent/story-05-local-first.md
arch: ../../docs/技术架构-v2/02-L0-本地优先基座.md
arch_link: "[02 §5](../../docs/技术架构-v2/02-L0-本地优先基座.md)"
priority: P1
milestone: M9
depends_on: [T-AGT-008.3]
status: todo
decisions: [D-137]
verify:
---

# T-AGT-013.2 · data_fetch（按域取数原语）

## 目标
让循环对**已登记的数据域**按参数直取，落缓存并更新 `as_of`。

## 验收标准（Given-When-Then）
- GWT-1：Given 本地缺某域数据，When 调用 `data_fetch(domain, params)`，Then 经既有 adapter 取数并落缓存，更新 `as_of`。
- GWT-2：Given 域未登记 adapter，Then `unavailable` 并点名。
- GWT-3：Given 出网，Then 经 `EgressGateway` 留痕。

## 接口面
<④ 对齐时实填（`doing` 起不可留占位），见 [工作流](../工作流.md) 第 ④ 步>
- 输入（消费的前置接口）：
- 输出（本任务交付的公共 API / 落盘位置）：

## 可关闭的遗留
<④ 对齐时实填：扫 [遗留问题](../遗留问题/README.md) 各层册的未闭区，逐条列「归属＝本任务」或「解封条件＝本任务 `done`」的条目（册内 id + 处置）；无命中写 `无`。>

## 假设与前提
<④ 对齐时实填；每条 `A<n>` 含 前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`>

## 涉及契约
- [02 §5](../../docs/技术架构-v2/02-L0-本地优先基座.md) —— 数据域与取数
- [02 §6](../../docs/技术架构-v2/02-L0-本地优先基座.md) —— 出网审计

## 参考
- `l0/info/fetch.py` / `l0/market/fetch.py`

## 备注
**按域取数**优于裸 HTTP：字段、防封策略、落库口径都在既有 adapter 里，模型只需选域与参数。
