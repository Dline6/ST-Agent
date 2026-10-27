---
id: T-AGT-009.1
parent: T-AGT-009
title: 会话身份进派发链
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §4](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M8
depends_on: []
status: todo
decisions: [D-137]
verify:
---

# T-AGT-009.1 · 会话身份进派发链

## 目标
派发链携带会话身份，使循环能回读本会话历史。

## 验收标准（Given-When-Then）
- GWT-1：Given 一次已确认的意图，When 派发，Then 派发载荷含 `session_id`。
- GWT-2：Given 未提供 `session_id`，When 派发，Then 走显式 fallback（无历史的单次查证）并在产出中标注，**不臆造会话**。
- GWT-3：Given 存量调用方不传该参数，Then 行为逐字节不变。

## 接口面
<④ 对齐时实填（`doing` 起不可留占位），见 [工作流](../工作流.md) 第 ④ 步>
- 输入（消费的前置接口）：
- 输出（本任务交付的公共 API / 落盘位置）：

## 可关闭的遗留
<④ 对齐时实填：扫 [遗留问题](../遗留问题/README.md) 各层册的未闭区，逐条列「归属＝本任务」或「解封条件＝本任务 `done`」的条目（册内 id + 处置）；无命中写 `无`。>

## 假设与前提
<④ 对齐时实填；每条 `A<n>` 含 前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`>

## 涉及契约
- [05 §4](../../docs/技术架构-v2/05-L3-对话主入口.md) —— `DispatchBus.dispatch` 的派发签名
- [05 §3.2](../../docs/技术架构-v2/05-L3-对话主入口.md) —— 确认卡与派发载荷的关系

## 参考
- `l3/intent/protocol.py`（`IntentConfirmation` 现无 `session_id`）
- `composition/facades/chat.py`（门面持有 `session_id`）

## 备注
**实现陷阱**：`IntentConfirmation` 是冻结模型，加字段会波及确认卡的构造与比较；优先考虑在派发签名上加关键字而非改模型。
