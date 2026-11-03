---
id: T-AGT-026.3
parent: T-AGT-026
title: 交还卡
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/PRD-v2-Agent/14-desktop-form.md
arch_link: "[14 §3](../../../../docs/PRD-v2-Agent/14-desktop-form.md)"
priority: P1
milestone: M11
depends_on: [T-AGT-026.1]
status: done
decisions: [D-154, D-158]
verify: 见执行日志 [T-AGT-026.3]（同批范围套件 2202 passed / 2 例既有环境性 ERROR；交还/拒绝/允许/问项四场景真浏览器走查；verify_docs --strict PASS；commit 7f29443）
---

# T-AGT-026.3 · 交还卡

## 目标
`gate_pending` 出确认卡、`ask_user` 出问项卡；确认后续跑同一 run。

## 验收标准（Given-When-Then）
- GWT-1：Given `gate_pending`，Then 出确认卡（允许 / 拒绝）。
- GWT-2：Given `ask_user`，Then 出问项卡（一问一参、可跳过）。
- GWT-3：Given 用户确认，Then 续跑同一 run 而非新建。

## 接口面

- **输入**：
  - `gate_pending`（`tool` / `skill_id` / `verdict` / `reason`）与 `ask_user`（`questions` / `directions` / `note`）事件（[`l3/runtime/loop.py`](../../../../src/st_agent/l3/runtime/loop.py)），经 `T-AGT-023.1` 的 SSE 到达前端；
  - 终轮 `agent_continuation` 载荷（[`composition/facades/chat.py`](../../../../src/st_agent/composition/facades/chat.py) 的 `_continuation_payload`；`T-AGT-011.3` 的线面）：`agent_run_id` / `kind` / `plan` / `pending` / `questions` / `directions` / `ask_note` / `stale`；
  - 既有续跑入口 `POST /api/chat` 的 `action=resume`（`decision ∈ {approve, answer, rerun, cancel}`，`T-AGT-019` 起的既有面）；
  - 留痕 `AgentRunRecord` 的 `reason` 与 `steps`（`AgentRunStepRef` 的 `tool_name` / `skill_id` / `status` / `skill_run_id` / `log_ref` / `truncated`）。
- **输出**：
  - `_continuation_payload` 增 `reason`（交还说明，取 `record.reason`）与 `evidence`（已收集步骤的引用清单），**不复制结果载荷**；
  - 前端交还卡：**已收集证据**段 + **未执行的那一步**（工具 / 参数）+ **未执行原因** + 「允许并继续」/「拒绝」；问项卡一问一参、留空即跳过 +「提交答案并继续」/「拒绝」；`stale` 时只给「重跑」；
  - 确认走既有 `resume`：以交还 run 的 `agent_run_id` 为依据续跑，续跑产**新** `agent_run_id` 并以 `resumed_from` 指回旧链（05 §10 / D-090 既有口径）；
  - 无新增端点、无新增决策值、不改 Trace。

## 可关闭的遗留

无。开工逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区：L0 余 `C3` / `C5` / `D1`–`D3` 与 L5 `A1` 皆「人决」，L1 `E` 段待人立项、L2 / L3 / L6 均 0 条，无归属 / 解封条件是本任务或本批的条目。

## 假设与前提

- **A1**：GWT-3 的「续跑同一 run 而非新建」按**既有续跑口径**落地——以交还 run 的 `agent_run_id` 为依据调既有 `resume`，续跑产**新** `agent_run_id` 并以 `resumed_from` 指回旧链（05 §10 / [D-090](../../../决策日志.md)），**不**新起一个无关对话任务。若按字面「同一个 `agent_run_id`」理解，则与既有契约和留痕模型冲突，本批不取。
  - 若错的影响：需改 05 §10 与留痕模型（新链 / `resumed_from` 的整套口径），波及 `T-AGT-019` 已交付面。
  - 验证方式：续跑用例断言 `action=resume`、`agent_run_id` 为交还 run、新留痕的 `resumed_from` 指回旧链。
- **A2**：交还卡出现在**终轮**——`gate_pending` / `ask_user` 使循环立即终止、终轮落会话；运行态只显式提示「等待确认 / 等待补充」，不在事件态提供无载荷按钮。
  - 若错的影响：卡片按钮在续跑载荷到达前不可用，或需在事件态另造一套交互。
  - 验证方式：走查观察运行态提示 → 终轮出卡；用例断言终轮载荷带 `agent_continuation`。
- **A3**：交还载荷新增 `reason` 与 `evidence` 取留痕的客观字段（`record.reason` / `record.steps` 引用），**不复制**结果载荷、不造第二真相源；卡片上的证据只做指向（`log_ref` 可下钻）。
  - 若错的影响：留痕与卡片各持一份数据，或卡片需再发一次请求取证据。
  - 验证方式：用例断言载荷字段取自留痕且不含 `data`；走查可见证据段。
- **A4**：「拒绝」沿用既有 `decision=cancel`（放弃续跑、已收集证据保留），不新增端点 / 决策值。
  - 若错的影响：出现第三个续跑入口或新决策语义。
  - 验证方式：走查点「拒绝」后回到放弃续跑的既有回执。
