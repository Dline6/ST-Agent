---
id: T-AGT-015.2
parent: T-AGT-015
title: web_fetch（网页抓取原语）
story: ../../docs/PRD-v2-Agent/story-05-local-first.md
arch: ../../docs/技术架构-v2/02-L0-本地优先基座.md
arch_link: "[02 §6](../../docs/技术架构-v2/02-L0-本地优先基座.md)"
priority: P1
milestone: M9
depends_on: [T-AGT-015.1]
status: todo
decisions: [D-137]
verify:
---

# T-AGT-015.2 · web_fetch（网页抓取原语）

## 目标
允许域名单内的 GET，并把正文抽成文本。

## 验收标准（Given-When-Then）
- GWT-1：Given 域名单内 URL，When 抓取，Then 返回正文文本并标注来源。
- GWT-2：Given 名单外主机，Then 拦截。
- GWT-3：Given 非文本 / 二进制，Then 显式不支持，不返回乱码。

## 接口面
<④ 对齐时实填（`doing` 起不可留占位），见 [工作流](../工作流.md) 第 ④ 步>
- 输入（消费的前置接口）：
- 输出（本任务交付的公共 API / 落盘位置）：

## 可关闭的遗留
<④ 对齐时实填：扫 [遗留问题](../遗留问题/README.md) 各层册的未闭区，逐条列「归属＝本任务」或「解封条件＝本任务 `done`」的条目（册内 id + 处置）；无命中写 `无`。>

## 假设与前提
<④ 对齐时实填；每条 `A<n>` 含 前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`>

## 涉及契约
- [02 §6](../../docs/技术架构-v2/02-L0-本地优先基座.md) —— 出网审计与域名单

## 参考
- `l1/sandbox/scopes.py`（`net_access` 主机模式匹配）

## 备注
网页正文是**不可信数据**：进入上下文前须标注来源，且不得被当作指令执行。
