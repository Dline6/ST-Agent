---
id: T-AGT-023.2
parent: T-AGT-023
title: 前端事件接入
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/PRD-v2-Agent/14-desktop-form.md
arch_link: "[14 §3](../../docs/PRD-v2-Agent/14-desktop-form.md)"
priority: P0
milestone: M11
depends_on: [T-AGT-023.1]
status: done
decisions: [D-154, D-155]
verify: e4d9022；前端 EventSource 接入 + 运行态/回读实测；见 [执行日志](../执行日志.md) `[T-AGT-023.2]`
---

# T-AGT-023.2 · 前端事件接入

## 目标
前端建立 SSE 连接、按事件更新运行态，并在断线时自动重连。

## 验收标准（Given-When-Then）
- GWT-1：Given 运行开始，Then 前端建立连接并进入运行态。
- GWT-2：Given 连接中断，Then 自动重连并从最后序号补发。
- GWT-3：Given 运行结束，Then 关闭连接且不重复渲染。

## 接口面
**输入**
- `T-AGT-023.1` 的 `GET /api/chat/run/<id>/events`（SSE，`?t=` 令牌）与派发返回的 `agent_run_id` / `running` 载荷。
- `ui/web/js/api.js` 持有的启动令牌；`chat.js` 现有的派发 / 续跑等待轮与 `GET /api/chat/session?id=` 回读面。

**输出**
- `ui/web/js/run_events.js`：`watchRun(runId, {onEvent, onEnd, onError})`——EventSource 连接、按事件名分发、断线自动重连（浏览器带 `Last-Event-ID`）、`stream_end` 后关闭。
- `ui/web/js/api.js`：`streamUrl(path)`（把令牌以查询串带入 SSE 地址，不落存储）。
- `ui/web/js/pages/chat.js`：派发 / 续跑带 `background: true`；运行态消息随事件更新；结束后回读会话渲染最终轮，且不重复渲染。

## 可关闭的遗留
无（逐册核过 L0 / L1 / L2 / L3 / L5 / L6 未闭区，无归属本任务或解封条件为本任务 `done` 的条目）。

## 假设与前提
- **A1**（WebView2 / Chromium 支持 EventSource）——若错的影响：需改为轮询降级；本批不做降级，显式记录为已知边界。验证：真实壳窗口走查。
- **A2**（EventSource 自动重连带 `Last-Event-ID`，服务端按序号续传）——若错的影响：断线后事件重复或缺失；验证：用例 + 断线重连走查。
- **A3**（结束后以 `GET /api/chat/session?id=` 回读为唯一渲染来源）——若错的影响：最终轮重复渲染或丢失；验证：端到端用例断言消息数不重复。
- **A4**（运行态只显示事件里的客观字段：tool / status / plan，不自造文案）——若错的影响：违反铁律 2；验证：走查文案 + 既有中性门。
