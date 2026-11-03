---
id: T-AGT-024.2
parent: T-AGT-024
title: 停止端点与停止按钮
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P1
milestone: M11
depends_on: [T-AGT-024.1, T-AGT-023.1]
status: done
decisions: [D-154]
verify: 见执行日志 [T-AGT-024.2]（范围套件 2187 passed / 2 errors；定向 81 passed；真浏览器走查：停止后证据表 1 行 + 终止行，stop 200；commit 5ad0e67）
---

# T-AGT-024.2 · 停止端点与停止按钮

## 目标
`POST /api/chat/run/<id>/stop` 与前端停止按钮；停止后展示已执行步骤与写操作。

## 验收标准（Given-When-Then）
- GWT-1：Given 运行中，When 点停止，Then 调用停止端点并展示已执行步骤。
- GWT-2：Given 无活动 run，Then 停止为 no-op 并提示。
- GWT-3：Given 停止完成，Then 不再有新事件。

## 接口面
- **输入**：
  - [`T-AGT-024.1`](T-AGT-024.1-取消透传与cancelled终止.md) 的 `ActiveRun.cancel_event` / `request_cancel()`（`src/st_agent/l3/runtime/runs.py`）与循环的 `cancelled` 终止。
  - [`T-AGT-023.1`](T-AGT-023.1-SSE端点与断线重连.md) 的 `ChatSurface.api_chat_run_events` 与 `ui/server.py` 的动态段解析（`_sse_run_id`）；组合根 `_AgentInvestigator.runs` / `is_active`。
  - [`T-AGT-023.2`](T-AGT-023.2-前端事件接入.md) 的前端运行态（`chat.js` 的 `watchRunMessage` / `run_events.js`）与 `api.js` 的 `postJson`。
- **输出**：
  - 契约先行：`00 §1.1` 写明 `POST /api/chat/run/<id>/stop`，`05 §10` 补实现进度（铁律 8）。
  - `_AgentInvestigator.stop(agent_run_id) -> bool`、门面 `ChatFacade.stop_run(agent_run_id) -> bool`、`ChatSurface.api_chat_run_stop(agent_run_id) -> dict`。
  - `ui/server.py` 的动态 `POST` 分支（路径集仍可枚举、令牌守卫照旧）。
  - 响应：在册 → `ok`（`agent_run_id` + `stopping`）；不在册 → `empty` + 显式提示（诚实 no-op）。
  - 前端运行态「停止」按钮与停止中标注；最终轮仍走既有「`run_finished` 后回读会话」路径渲染（不另造停止专用渲染）。

## 可关闭的遗留
无（④ 对齐时逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区：前四册与 L6 册 0 条未闭；L5 册唯一未闭 `A1` 与人决凭据相关。检查 8 兜底。）

## 假设与前提
- **A1** 停止是**协作式**：端点只置标记，实际终止在下一个边界，故响应语义是「已请求停止」而非「已停止」。验证：用例断言置位后 `run_finished` 到达且 `termination=cancelled`。
- **A2** 无活动 run 时端点回 **no-op**（`empty` 信封 + 提示）而非错误码——重复点击 / 已结束不构成失败（GWT-2）。验证：单测与路由用例。
- **A3** 停止后**不再有新事件**：SSE 在 `run_finished` 后 `stream_end(finished)`，前端关闭连接（既有口径）。验证：事件流用例断言末条 `run_finished(cancelled)` 之后无新帧。
- **A4** 前端沿用既有回读路径渲染最终轮（不另造渲染）。验证：浏览器 `--dev` 面走查。
