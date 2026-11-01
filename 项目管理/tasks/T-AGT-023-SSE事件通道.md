---
id: T-AGT-023
parent: null
title: SSE 事件通道
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M11
depends_on: [T-AGT-022.2]
status: done
decisions: [D-154, D-155]
verify:
---

# T-AGT-023 · SSE 事件通道

> **分组节点**：本节点不直接交付，交付由 [`T-AGT-023.1`](T-AGT-023.1-SSE端点与断线重连.md) / [`T-AGT-023.2`](T-AGT-023.2-前端事件接入.md) 承接；父状态与父 `depends_on` 由 `render_ledger.py render` 按 children 派生。

## 目标
把运行事件实时送到前端，并支持断线重连。

## 验收标准（Given-When-Then）
- GWT-1：由 `.1` / `.2` 合成，父节点不单独验收。

## 接口面
（子叶的并集）`GET /api/chat/run/<id>/events` SSE（回放 + 续推 + `Last-Event-ID` + 心跳 + `stream_end`）；`_AgentInvestigator` 只读运行面；`DialogFacade.turn` 的 `action=dispatch|resume` 后台模式（`background: true` → 立即返回 `agent_run_id`）；`serve(..., agent_runs=...)` / `ui_ports["agent_runs"]`；前端 `run_events.js` 的 `watchRun` 与 `chat.js` 运行态。细节见 `.1` / `.2`。

## 可关闭的遗留
无（两叶子叶并集；逐册核过 L0 / L1 / L2 / L3 / L5 / L6 未闭区，唯一未闭 `L5-A1` 与本组无关）。

## 假设与前提
（子叶的并集）后台 run 沿用 `T-AGT-022.1` 注册表与会话互斥；后台模式只对 `dispatch` / `resume` 生效、默认 `post` 保持同步；SSE 按 HTTP/1.0 close 定界流式；`run_finished` 即结束信号；助手轮最终落盘在后台线程完成、前端结束后回读会话；EventSource 自动重连带 `Last-Event-ID`。逐条影响与验证见 `.1` A1–A5 / `.2` A1–A4。
