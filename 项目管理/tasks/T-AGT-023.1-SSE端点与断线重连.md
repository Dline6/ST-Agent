---
id: T-AGT-023.1
parent: T-AGT-023
title: SSE 端点与断线重连
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M11
depends_on: [T-AGT-022.2]
status: done
decisions: [D-154, D-155]
verify: SSE 端点 + 后台派发实测；见 [执行日志](../执行日志.md) `[T-AGT-023.1]`
---

# T-AGT-023.1 · SSE 端点与断线重连

## 目标
提供 `GET /api/chat/run/<id>/events`：先补发已发生事件，再续推新事件；支持 `Last-Event-ID` 与心跳。

## 验收标准（Given-When-Then）
- GWT-1：Given 活动 run，When 连接端点，Then 收到已发生事件并按序续推新事件。
- GWT-2：Given 请求带 `Last-Event-ID`，Then 从该序号之后补发。
- GWT-3：Given 无活动 run 或 id 不存在，Then 显式空态 / 错误，不挂空连接。

## 接口面
**输入**
- `T-AGT-022.1`：`ActiveRunRegistry.get` / `.active_ids`（经 `_AgentInvestigator.runs`）；`new_agent_run_id()`。
- `T-AGT-022.2`：`RunEventLog` 与 `read_run_events(store, id, *, after_seq=)`；落盘 `execution_log/agent-run-events/<id>.jsonl`。
- 现有派发链：`DialogFacade._turn` → `DispatchBus.dispatch` / `.resume` → `_AgentInvestigator.investigate` / `.resume`。

**输出**
- `GET /api/chat/run/<id>/events`（SSE）：帧 `id: <seq>` / `event: <11 类之一>` / `data: <RunEvent JSON>`；心跳 `: ping`；结束帧 `event: stream_end`（`reason` = `finished` | `inactive`）。未知 id 且无事件日志 → 404 显式错误，不挂空连接。
- `_AgentInvestigator` 的只读运行面：`.events(agent_run_id, after_seq=0)` / `.is_active(agent_run_id)` / `.wait_active(agent_run_id, timeout)` / `.new_run_id()`（表现层经鸭子端口消费，不直开存储）。
- `DialogFacade.turn` 的 `action=dispatch|resume` 增 `background: true` + `agent_run_id`：立即返回 running 载荷；工作线程跑完整轮并落助手轮。
- `serve(..., agent_runs=...)` 与 `ui_ports["agent_runs"]`（M5 注入循环端口；旧根 `None` ⇒ 端点 `unavailable`）。
- 守卫：仅 SSE 路径接受 `?t=<启动令牌>`（EventSource 不能带自定义头），其余 `/api/*` 仍只认 `X-ST-Token`。

## 可关闭的遗留
无（逐册核过 L0 / L1 / L2 / L3 / L5 / L6 未闭区；唯一未闭 `L5-A1` 需真实渠道凭据，与本任务无关）。

## 假设与前提
- **A1**（后台 run 沿用 `T-AGT-022.1` 的注册表与会话互斥）——若错的影响：同会话可能并发 run、SSE 寻址重复；验证：用例断言同会话第二笔派发回 `validation_failed`。
- **A2**（仅 `action=dispatch|resume` 且 `background=true` 走后台；默认 `post` 与不带该键的调用保持同步）——若错的影响：既有 chat.post / needs_confirmation 用例成批受波及；验证：既有套件全绿，新用例断言 running 载荷。
- **A3**（SSE 以 HTTP/1.0 close 定界、无 `Content-Length` 流式写出，Chromium / WebView2 的 EventSource 可增量读取）——若错的影响：前端需退化轮询；验证：真实壳窗口走查看到事件逐条到达。
- **A4**（`run_finished` 即结束信号；无它且 run 不在注册表时按崩溃回放后以 `stream_end(inactive)` 关闭）——若错的影响：SSE 挂空连接；验证：用例覆盖 finished / inactive / unknown 三态。
- **A5**（助手轮最终落盘在后台线程内完成，前端在 `run_finished` 后回读会话渲染）——若错的影响：最终结果不显示或重复；验证：端到端用例断言会话末条为最终回复。
