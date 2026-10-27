---
id: T-AGT-015.1
parent: T-AGT-015
title: web_search（联网搜索原语）
story: ../../docs/PRD-v2-Agent/story-05-local-first.md
arch: ../../docs/技术架构-v2/02-L0-本地优先基座.md
arch_link: "[02 §6](../../docs/技术架构-v2/02-L0-本地优先基座.md)"
priority: P1
milestone: M9
depends_on: [T-AGT-008.3]
status: todo
decisions: [D-137]
verify:
---

# T-AGT-015.1 · web_search（联网搜索原语）

## 目标
接入第三方搜索，返回带来源与时间的结果。

## 验收标准（Given-When-Then）
- GWT-1：Given 已配置搜索提供方，When 调用，Then 返回结果并标注来源与时间。
- GWT-2：Given 未配置，Then `unavailable` 并给配置指引，**不静默降级**。
- GWT-3：Given 调用，Then 经 `EgressGateway` 留痕（审计只含字节数与目的说明，不含查询正文）。

## 接口面
<④ 对齐时实填（`doing` 起不可留占位），见 [工作流](../工作流.md) 第 ④ 步>
- 输入（消费的前置接口）：
- 输出（本任务交付的公共 API / 落盘位置）：

## 可关闭的遗留
<④ 对齐时实填：扫 [遗留问题](../遗留问题/README.md) 各层册的未闭区，逐条列「归属＝本任务」或「解封条件＝本任务 `done`」的条目（册内 id + 处置）；无命中写 `无`。>

## 假设与前提
<④ 对齐时实填；每条 `A<n>` 含 前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`>

## 涉及契约
- [01 §7](../../docs/技术架构-v2/01-平台共享契约.md) —— `search.provider` / `search.key`
- [02 §7](../../docs/技术架构-v2/02-L0-本地优先基座.md) —— `net-search` 能力声明

## 参考
- `l0/net/gateway.py`（唯一出网口）

## 备注
**数据主权**：搜索会把用户问题发给第三方。默认关、首次显式告知（由 [`T-AGT-015.3`] 承接）。
