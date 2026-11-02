---
id: T-AGT-027.2
parent: T-AGT-027
title: 前端运行中输入
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/PRD-v2-Agent/14-desktop-form.md
arch_link: "[14 §3](../../docs/PRD-v2-Agent/14-desktop-form.md)"
priority: P2
milestone: M11
depends_on: [T-AGT-027.1, T-AGT-023.2]
status: done
decisions: [D-154, D-163]
verify: "见执行日志 [T-AGT-027.2]；端点 / 真装配用例 8 passed；范围套件 2268 passed / 2 环境性 ERROR；verify_docs --strict PASS；dev 面两态走查"
---

# T-AGT-027.2 · 前端运行中输入

## 目标
运行中输入框可用，提交即插话；有明确「已送达运行中任务」提示。

## 验收标准（Given-When-Then）
- GWT-1：Given 运行中，Then 输入框可用且提交为插话。
- GWT-2：Given 提交成功，Then 标注已送达且不打断当前步骤。

## 接口面

**输入**（逐条点名上游交付件与落点）

- `T-AGT-027.1`：`POST /api/chat/run/<id>/steer` 的回执（`delivered` / `session_id`）、`steer` 运行事件、`ChatMessage.steer` 读面。
- `T-AGT-023.2`：前端事件接入 [`run_events.js`](../../src/st_agent/ui/web/js/run_events.js) 的 `RUN_EVENT_TYPES` 与 `watchRun`。
- `T-AGT-024.2`：运行端点族的动态段解析口径（[`server.py`](../../src/st_agent/ui/server.py) 的 `_run_path_id`）。
- `T-UI-032`：对话宿主 [`chat.js`](../../src/st_agent/ui/web/js/pages/chat.js) 的 `mountChat` / `send` / `activeRun` 状态。

**输出**（本叶交付的公共 API 与落盘位置）

- [`ui/surfaces/chat.py`](../../src/st_agent/ui/surfaces/chat.py) 的 `api_chat_run_steer`；[`ui/server.py`](../../src/st_agent/ui/server.py) 的 `/api/chat/run/<id>/steer` 动态写端点。
- [`composition/facades/chat_runs.py`](../../src/st_agent/composition/facades/chat_runs.py)（`RunControlMixin`）的 `steer_run`：落 user 消息（`steer=True`）→ 投递到活动 run；运行已结束即回「已作为普通消息保留」。
- 前端：运行中输入框保持可用、提交走插话、显示「已送达运行中任务」/「运行已结束，已保留为普通消息」标记；`steer` 事件与历史回放的同一标记。

## 可关闭的遗留

无（同上：逐册核过未闭区，无归属本任务的条目）。

## 假设与前提

- **A1**（可输入条件）：只要有活动 run（`activeRun`），输入框在 `sending` 期间仍可用；`/` 快捷指令等无 SSE 的同步轮保持禁用（避免并发提交）。若错：需为同步轮另开插话通道。
- **A2**（提交语义）：运行中提交**恒为插话**，不排队成新任务；开新任务仍须先停止。若错：需在输入面加「插话 / 新任务」二选一。
- **A3**（送达标注）：提交成功以端点回执的 `delivered` 与随后 `steer` 事件为准；会话回放后按 `ChatMessage.steer` 显示「运行中插入」。若错：标记会与运行实际是否消费不一致。
- **A4**（端点语义）：运行已结束时端点仍回 `ok`（`delivered=false`）并把消息留作普通用户消息，不报错。若错：竞态消息会被前端当作失败并重试。
