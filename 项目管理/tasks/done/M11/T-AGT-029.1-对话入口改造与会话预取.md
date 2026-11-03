---
id: T-AGT-029.1
parent: T-AGT-029
title: 对话入口改造与会话预取
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §3.1](../../../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M11
depends_on: [T-AGT-020, T-AGT-022.1]
status: done
decisions: [D-154, D-156]
verify: 见执行日志 [T-AGT-029.1]（范围套件 2192 passed / 2 errors〔两例既有环境性 test_shell〕；定向 6 passed + 改写用例 45 passed；真浏览器：默认输入 → 正文「证据已收集完毕」+ 2 行证据表，SSE 200，控制台 0 错误；commit 1be347e）
---

# T-AGT-029.1 · 对话入口改造与会话预取

## 目标
`/api/chat` 的默认路径改为启动一次 agent run；会话历史与记忆在进入循环前预取。

## 验收标准（Given-When-Then）
- GWT-1：Given 任意自然语言，Then 进入循环而非先分类。
- GWT-2：Given 会话已有历史，Then 循环上下文包含历史与记忆。
- GWT-3：Given 模型直接回答，Then 产出模型正文。

## 接口面
**输入**（逐条点名上游与落点）：
- `T-AGT-020` 交付的 [05 §3.1](../../../../docs/技术架构-v2/05-L3-对话主入口.md) / [§3.2](../../../../docs/技术架构-v2/05-L3-对话主入口.md)：意图只保留 `/` 快捷指令短路与循环内子协议，默认请求进循环。
- `T-AGT-022.1` 的 `ActiveRunRegistry` / `new_agent_run_id`：开跑铸号与同会话互斥。
- `T-AGT-023.1` 的 `GET /api/chat/run/<id>/events` 与 running 载荷：默认入口沿用同一事件通道。
- `T-AGT-024.1` / `.2` 的 `cancel_event` 与停止端点：默认入口的运行同样可停止。
- 既有预取面：`SessionStore.assemble_context(session_id, reader, topic=task)`（经 `_AgentInvestigator._context_inputs` 取 `messages` 与 `memory.slices`）。

**输出**（本任务交付的公共 API 与落盘位置）：
- `DialogFacade.start_turn(text, *, session_id=None, agent_run_id=None) -> DispatchOutcome`：默认入口承接口，内部经 `DispatchBus.dispatch` 的 `investigate` 去向跑循环。
- `DialogFacade.ensure_session(session_id=None) -> str`：后台开跑前先定会话（running 载荷须带 `session_id`）。
- `POST /api/chat` 默认分支（`text` 不以 `/` 开头）：起一次 run；`background:true` 回 running（`agent_run_id` + `session_id`），不带则同步回最终轮。落盘沿用既有 `chat_history` 与 `execution_log`（`agent-run/`、`run-events/`），不新开分区。

## 可关闭的遗留
无（逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区，无归属本任务或解封条件为本任务的条目；检查 8 = 0）。

## 假设与前提
- **A1**：未装配循环端口的旧根上，默认自然语言走 fail-closed `unavailable`（不回落既有意图确认卡路径）。**若错**：M1/M2/M4 组合根的 HTTP 入口用例需另改口径；**验证**：范围套件与集成套件的入口用例。
- **A2**：默认入口的 `background:true` 借用 `T-AGT-023.1` 的 running + SSE 路径；不带 `background` 的同步调用仍返回最终轮，载荷形态不变。**若错**：前端事件跟随与回读时序需另造；**验证**：同步 / 后台两条入口用例。
- **A3**：模型的直接回答经既有 `slots.answer`（`text_kinds.answer = generated`）下发，前端把它作为回复正文显示；不改描述件形态、不另造第二个文本通道。**若错**：`T-AGT-026.1`（步骤流）会与正文重复；**验证**：描述件契约用例 + 前端纯函数断言 + 真浏览器走查。
