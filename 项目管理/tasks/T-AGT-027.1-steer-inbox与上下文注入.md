---
id: T-AGT-027.1
parent: T-AGT-027
title: steer inbox 与上下文注入
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P2
milestone: M11
depends_on: [T-AGT-022.1]
status: done
decisions: [D-154, D-163]
verify: "见执行日志 [T-AGT-027.1]；范围套件 2268 passed / 2 环境性 ERROR；verify_docs --strict PASS；真 HTTP 集成用例覆盖黄金路径；commit b4e1fb4"
---

# T-AGT-027.1 · steer inbox 与上下文注入

## 目标
步骤边界排空 inbox，把插话作为带「运行中插入」标记的 user 消息并入下一轮上下文；与「模型给出结束」竞态时转为下一条普通消息。

## 验收标准（Given-When-Then）
- GWT-1：Given 运行中插话，Then 下一步上下文含该消息并带标记。
- GWT-2：Given 插话与结束竞态，Then 转为下一条普通消息。
- GWT-3：Given 多条插话，Then 按到达顺序全部保留。

## 接口面

**输入**（逐条点名上游交付件与落点）

- `T-AGT-022.1` 活动 run 控制句柄与注册表：[`runs.py`](../../src/st_agent/l3/runtime/runs.py) 的 `ActiveRun` / `ActiveRunRegistry`——本叶在 `ActiveRun` 上挂 `steer_inbox`（`SteerInbox`），组合根按 `agent_run_id` 取用。
- `T-AGT-004.2` 循环与上下文：[`loop.py`](../../src/st_agent/l3/runtime/loop.py) 的 `run_agent_loop`（新增 `steer_inbox` 入参）；[`context.py`](../../src/st_agent/l3/runtime/context.py) 的 `assemble_work_context`（新增 `steer_messages` 入参）与 `WorkContext`（新增 `steers` 字段）。
- `T-AGT-004.3` 留痕：[`report.py`](../../src/st_agent/l3/runtime/report.py) 的 `AgentRunRecord`（新增 `steered` 字段）。
- `T-AGT-022.2` 运行事件：[`events.py`](../../src/st_agent/l3/runtime/events.py) 的 `RUN_EVENT_TYPES`（新增 `steer`）。
- `T-L3-008` 会话：[`session.py`](../../src/st_agent/l3/chat/session.py) 的 `SessionStore.append`（新增 `steer` 参数）与 `ChatMessage`（新增 `steer` 标记）。

**输出**（本叶交付的公共 API 与落盘位置）

- 新增 `src/st_agent/l3/runtime/steer.py`（新）：`SteerMessage`（文本 / 接收时刻 / 是否投递 / 排空时已执行步数）与线程安全 `SteerInbox`（`add` / `drain` / `pending`）。
- `run_agent_loop(..., steer_inbox=...)`：步骤边界排空；`LoopOutcome.steered`；新增 `steer` 运行事件；`WorkContext.steers` 的「运行中插入」渲染段。
- `AgentRunRecord.steered`：留痕显式标注（投递与否、排空时点）。
- `ChatMessage.steer`：用户轮标记（旧记录缺省 `false`，向后兼容）。

## 可关闭的遗留

无（④ 对齐时逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区，无归属本任务或解封条件为本任务的条目；L0 `C3` / `C5` / `D1`–`D3` 与 L5 `A1` 均为人决 / 人配凭据，不属代码面欠账）。

## 假设与前提

- **A1**（落盘时点）：插话在**端点受理时**即作为 user 消息落会话（`steer=True`），排空只决定它是否进入**本次**运行的模型上下文。若错（应收紧为「排空时才落盘」）：前端 ack 与历史回放形态需改，且竞态消息会丢失——与本叶「不丢」验收相悖。
- **A2**（竞态去向）：与「模型给出结束」竞态时**不自动开新任务**，消息保留为普通用户消息（对齐 `T-AGT-027.2` 的「插话不排队成新任务」）。若错：需在入口追加自动承接路径。
- **A3**（边界粒度）：只在**步骤边界**（每轮 LLM 调用前）排空，一次排空全部并按到达顺序注入；不打断在途工具。若错：需细化到单步之间的排空接口。
- **A4**（预算取舍）：插话作为工作上下文的一段参与预算，取舍顺序为**先舍记忆、再舍历史、最后才舍插话**（自旧向新），被舍项记入 `dropped`（`steer:<接收时刻>`）。若错：应改为恒保留并标 `over_budget`。
- **A5**（事件形态）：新增第 12 类运行事件 `steer`（契约先行），载荷只含文本、接收时刻、投递与否与排空时点；不新增 Trace 步骤类型。若错：需退回「只进 `agent_run` 留痕」的形态。
- **A6**（会话标记）：`steer` 作为 `ChatMessage` 的布尔字段落盘，旧记录缺省 `false`。若错：需另设会话外旁路索引（会引入第二事实源）。
