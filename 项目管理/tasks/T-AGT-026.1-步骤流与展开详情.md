---
id: T-AGT-026.1
parent: T-AGT-026
title: 步骤流与展开详情
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/PRD-v2-Agent/14-desktop-form.md
arch_link: "[14 §3](../../docs/PRD-v2-Agent/14-desktop-form.md)"
priority: P1
milestone: M11
depends_on: [T-AGT-023.2]
status: done
decisions: [D-154, D-158]
verify: 见执行日志 [T-AGT-026.1]（范围套件 2202 passed / 2 例既有环境性 ERROR；定向 249 项；真浏览器走查；verify_docs --strict PASS；commit 7f29443）
---

# T-AGT-026.1 · 步骤流与展开详情

## 目标
每个动作一行（工具 / 目标 / 状态 / 耗时），可展开参数、结果摘要与留痕指针；子 Agent 嵌套展示。

## 验收标准（Given-When-Then）
- GWT-1：Given 运行中，Then 每个动作一行且状态随事件更新。
- GWT-2：Given 结果被截断，Then 标「已截断」并可看留痕指针。
- GWT-3：Given 子 Agent，Then 嵌套展示其步骤与结论。

## 接口面

- **输入**：
  - `T-AGT-022.2` 的 `RunEvent`（`seq` / `event` / `agent_run_id` / `at` / `payload`）与 `T-AGT-023.1` 的 `GET /api/chat/run/<id>/events`（SSE）；
  - `run_agent_loop(..., emit=...)`（[`l3/runtime/loop.py`](../../src/st_agent/l3/runtime/loop.py)）的 `step_started` / `step_finished` / `sub_agent` 事件；
  - `T-AGT-023.2` 的前端接入（[`run_events.js`](../../src/st_agent/ui/web/js/run_events.js) 的 `watchRun`、[`pages/chat.js`](../../src/st_agent/ui/web/js/pages/chat.js) 的 `applyRunEvent` / `renderMessage`）；
  - `WorkStep`（[`l3/runtime/context.py`](../../src/st_agent/l3/runtime/context.py)）的 `arguments` / `skill_run_id` / `log_ref` / `truncated` 语义与截断阈值 `max_result_chars`；`ToolCall.call_id`（`st_agent.l0.llm`）。
- **输出**：
  - **事件载荷扩字段（不新增事件类型）**：`step_started` 增 `call_id` / `arguments`；`step_finished` 增 `call_id`、`truncated`（按 `max_result_chars` 实时判定）与 `summary`（结果摘要，≤200 字）；`sub_agent` 增 `trace_id` / `reason` / `answer` / `evidence` 与 `step_list`（子步骤的 tool / status / skill_run_id）；
  - **判定同源**：[`context.py`](../../src/st_agent/l3/runtime/context.py) 导出 `result_will_truncate(envelope, max_result_chars)` 与 `summarize_result(envelope)`，`_apply_truncation` 与本任务同用一份判定；终轮留痕（`AgentRunStepRef.truncated`）按同一判定如实标注；
  - **前端运行面板**：步骤行（工具 / 目标 / 状态 / 耗时）+ 展开详情（参数 / 结果摘要 / 「已截断」标记 / 留痕指针）+ 子 Agent 嵌套块；样式落 [`app.css`](../../src/st_agent/ui/web/css/app.css) 既有令牌；
  - 无新增落盘、不改 Trace 步骤、不改 01 §4 的记录形态。

## 可关闭的遗留

无。开工逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区：L0 余 `C3` / `C5` / `D1`–`D3` 与 L5 `A1` 皆「人决」，L1 `E` 段待人立项、L2 / L3 / L6 均 0 条，无归属 / 解封条件是本任务或本批的条目。

## 假设与前提

- **A1**：耗时由同一调用 `step_started` / `step_finished` 的 `at` 差算，服务端**不新增** `duration` 字段。
  - 若错的影响：耗时缺失或与 Trace 的 `duration` 口径分叉（Trace 口径不受影响）。
  - 验证方式：运行面板走查可见耗时；用例断言事件不带 `duration` 而 `call_id` 可对位。
- **A2**：步骤行对位用 `ToolCall.call_id`（模型给出的调用标识），并行批同轮同名工具不串行错配。
  - 若错的影响：并行批的 `step_finished` 可能更新错行。
  - 验证方式：事件用例断言两条同名调用的 `call_id` 各自闭合；前端按 `call_id` 查找未闭合行。
- **A3**：结果摘要＝非 `ok` 取 `envelope.reason`、`ok` 取序列化载荷头部（≤200 字）；完整结果仍只在 `execution_log`，摘要**不替代**留痕指针。
  - 若错的影响：摘要过长撑大事件日志，或被误当完整结果。
  - 验证方式：用例断言摘要长度上限与截断形态；展开详情同时给出 `log_ref`。
- **A4**：截断标记按 `max_result_chars` **实时判定**、**不改写**循环内 `WorkStep` 原件（既有用例 `test_truncated_result_keeps_readable_pointer` 钉住原件不变）；同一判定同时用于事件与留痕，终轮证据表的「结果已截断」列自此如实。
  - 若错的影响：运行面板与终轮证据表对「是否截断」各说各话。
  - 验证方式：事件用例断言超阈值结果为 `truncated=True`；留痕用例断言 `AgentRunStepRef.truncated` 同步为真。
- **A5**：契约先行（铁律 8）——[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md) 补「步骤流字段」段；[01 §4](../../docs/技术架构-v2/01-平台共享契约.md) **不改**（运行事件的记录形态与「只载客观字段」红线不变，扩的是载荷内的客观字段）。
  - 若错的影响：契约与实现分叉，或需在 01 登记新条目。
  - 验证方式：`verify_docs.py --strict` + 人工复核 §10 段落与实现一致。
