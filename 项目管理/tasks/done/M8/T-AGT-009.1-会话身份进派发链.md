---
id: T-AGT-009.1
parent: T-AGT-009
title: 会话身份进派发链
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §4](../../../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M8
depends_on: []
status: done
decisions: [D-137, D-142]
verify: "范围 1279 passed；全量 4622 passed（2 例已知环境性 ERROR）；见执行日志 [T-AGT-009.1]"
---

# T-AGT-009.1 · 会话身份进派发链

## 目标
派发链携带会话身份，使循环能回读本会话历史。

## 验收标准（Given-When-Then）
- GWT-1：Given 一次已确认的意图，When 派发，Then 派发载荷含 `session_id`。
- GWT-2：Given 未提供 `session_id`，When 派发，Then 走显式 fallback（无历史的单次查证）并在产出中标注，**不臆造会话**。
- GWT-3：Given 存量调用方不传该参数，Then 行为逐字节不变。

## 接口面
- 输入（消费的前置接口）：
  - `DialogFacade.confirm_and_dispatch(session_id, …)`（`composition/facades/chat.py`）——会话标识的唯一来源，该门面已持 `SessionStore`
  - `DispatchBus.dispatch`（`l3/dispatch/bus.py`）与 `_dispatch_investigate`
  - 循环端口鸭子面 `investigate(confirmation, *, values, now)`（`composition/agent.py`）
- 输出（本任务交付的公共 API / 落盘位置）：
  - `DispatchBus.dispatch(..., session_id: str | None = None)` 与 `DispatchOutcome.session_id: str | None = None`
  - 循环端口签名增 `session_id=`；`InvestigationOutcome.session_id` 与 `context_note`（无会话时的显式标注）
  - `run_agent_loop(..., session_id: str | None = None)` 透传至 `AgentRunRecord.session_id`（留痕可核）

## 可关闭的遗留
无（同父任务：各册未闭区无归属本任务或解封条件指向本任务的条目）

## 假设与前提
- A1：`session_id` 是本地 `sess_` 标识（`l3/chat/session.py` 既定），不新增契约 ID 类、不触发 01 §1 变更。若错：01 §1 需增 ID 类并波及其他消费面。验证：GWT-1 与实际派发载荷断言。
- A2：`DispatchOutcome` 无对外序列化契约（仅进程内消费），增带默认值的可选字段不破坏存量调用方；GWT-3 以既有派发用例零改动通过为判据。若错：需同批改序列化 / 导出面。验证：`tests/l3` 既有派发用例。
- A3：未传 `session_id` 时不由总线创建会话（不臆造），循环按「无历史单次查证」照常运行并显式标注。若错：与 GWT-2 冲突、语义漂移。验证：显式 fallback 用例。

## 涉及契约
- [05 §4](../../../../docs/技术架构-v2/05-L3-对话主入口.md) —— `DispatchBus.dispatch` 的派发签名
- [05 §3.2](../../../../docs/技术架构-v2/05-L3-对话主入口.md) —— 确认卡与派发载荷的关系

## 参考
- `l3/intent/protocol.py`（`IntentConfirmation` 现无 `session_id`）
- `composition/facades/chat.py`（门面持有 `session_id`）

## 备注
**实现陷阱**：`IntentConfirmation` 是冻结模型，加字段会波及确认卡的构造与比较；优先考虑在派发签名上加关键字而非改模型。
