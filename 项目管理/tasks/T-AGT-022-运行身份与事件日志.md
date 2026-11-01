---
id: T-AGT-022
parent: null
title: 运行身份与事件日志
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M11
depends_on: [T-AGT-020]
status: done
decisions: [D-154]
verify:
---

# T-AGT-022 · 运行身份与事件日志

> **分组节点**：本节点不直接交付，交付由 [`T-AGT-022.1`](T-AGT-022.1-开跑铸号与活动run注册表.md) / [`T-AGT-022.2`](T-AGT-022.2-事件模型与JSONL追加日志.md) 承接；父状态与父 `depends_on` 由 `render_ledger.py render` 按 children 派生。

## 目标
循环开跑即有稳定标识，并产出可按序重放的事件流。

## 验收标准（Given-When-Then）
- GWT-1：由 `.1` / `.2` 合成，父节点不单独验收。

## 接口面

（子叶的并集；分组节点不单独交付）
- **`T-AGT-022.1`**：`ActiveRun` / `ActiveRunRegistry` / `RunAlreadyActiveError`；`_AgentInvestigator.investigate / .resume` 开跑前铸号并登记、结束移除；`conclude_agent_run` / `resume_agent_run` 的 `agent_run_id` 入参。无新增落盘。
- **`T-AGT-022.2`**：`RunEventType` / `RUN_EVENT_TYPES` / `RunEvent` / `RunEventLog` / `event_log_ref_of` / `read_run_events`；`run_agent_loop(..., emit=)`。落盘 `execution_log/agent-run-events/<agent_run_id>.jsonl`。

## 可关闭的遗留
无（2026-11-01 逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区，未命中；检查 8 兜底）。

## 假设与前提

（子叶的并集）
- **A1**（注册表进程内瞬态、不落盘；会话级互斥仅在有 `session_id` 时生效；本叶仍同步执行；控制句柄本期只承载身份与取消标记）——详见 [`T-AGT-022.1`](T-AGT-022.1-开跑铸号与活动run注册表.md)。
- **A2**（事件不含模型中间推理；JSONL 经 `Store.get`+`put` 读改写；续跑确认步不在事件流内；子 Agent 内部事件不并入父流；`text_delta` 现为结束轮整段一条）——详见 [`T-AGT-022.2`](T-AGT-022.2-事件模型与JSONL追加日志.md)。
