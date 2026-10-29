---
id: T-AGT-011.3
parent: T-AGT-011
title: 待确认与待输入步骤的一键续跑
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M8
depends_on: [T-AGT-011.2, T-AGT-012.1]
status: done
decisions: [D-137, D-144]
verify: 2026-10-29 `[T-AGT-011.3]` · 范围套件 3276 passed / 2 errors（`test_shell` 本机环境性）· `verify_docs --strict` PASS · 界面未走查（真路径留给 `T-INT-009`）· 未触联网面
---

# T-AGT-011.3 · 待确认与待输入步骤的一键续跑

## 目标
把「终止交还」从死胡同变成可续跑：已收集的证据不重跑。

## 验收标准（Given-When-Then）
- GWT-1：Given 循环因 `needs-confirmation` 终止，When 用户确认，Then 从该步继续而非重跑全程。
- GWT-2：Given 因 `ask_user` 终止，When 用户作答，Then 带答案续跑。
- GWT-3：Given 续跑时依据已过期，Then 显式提示并允许重跑。

## 接口面
- 输入（消费的前置接口）：
  - `T-AGT-011.2`：`needs_user_input` 终止与结构化问项（`AskQuestion`、`LoopOutcome.questions`）。
  - `T-AGT-011.1`：`PlanStep` / `LoopOutcome.plan`（续跑时计划随上下文承接）。
  - `T-AGT-004.3`：`AgentRunRecord` / `load_agent_run` / `conclude_agent_run`（[`l3/runtime/report.py`](../../src/st_agent/l3/runtime/report.py)）——续跑的依据与留痕落点。
  - `T-AGT-005.2`：`LoopOutcome.pending`（待确认步）与 `ActionGate`（[`l3/runtime/loop.py`](../../src/st_agent/l3/runtime/loop.py) / [`gate.py`](../../src/st_agent/l3/runtime/gate.py)）。
  - `T-AGT-006` / `T-AGT-007`：`investigate` 去向端口（[`l3/dispatch/bus.py`](../../src/st_agent/l3/dispatch/bus.py)）与组合根 `_AgentInvestigator`（[`composition/agent.py`](../../src/st_agent/composition/agent.py)）。
  - `T-L3-008`：助手轮落盘（[`composition/facades/chat.py`](../../src/st_agent/composition/facades/chat.py) 的 `record_reply`）——续跑结果照常进会话历史。
- 输出（本任务交付的公共 API / 落盘位置）：
  - 新模块 `src/st_agent/l3/runtime/continuation.py`：`CONTINUATION_TTL_SECONDS`、`load_continuation`、`resume_agent_run`（续跑装配与过期判定）。
  - `AgentRunRecord.pending` / `resumed_from`（`l3/runtime/report.py`）；`describe_agent_run` 的 `resumed_from` 槽（`l3/render/describe.py`）。
  - `DispatchBus.resume(...)`（鸭子端口：端口有 `resume` 即用，无则 `unavailable` + 点名本任务）；`_AgentInvestigator.resume(...)`。
  - 对话门面 `action="resume"`（`composition/facades/chat.py`）与 `/api/chat` 载荷 `agent_continuation`（`ui/surfaces/chat.py`）；表现层续跑块（`ui/web/js/pages/chat.js`）。
  - 契约：05 §10 续跑语义（轻形态：新循环承接前序证据，不做挂起/恢复）。

## 可关闭的遗留
无（逐册核过六层遗留册未闭区，无「归属＝本任务」或「解封条件＝本任务 `done`」的条目）。

## 假设与前提
- **A1**：续跑以「**新一轮循环 + 承接前序证据**」实现，不给循环加挂起 / 恢复状态机（[D-090](../../docs/技术架构-v2/05-L3-对话主入口.md) ⑦ 冻结范围；[D-137](../决策日志.md) D-4 轻形态）。若错：要异步与状态持久化，属重造循环。验证：`tests/l3/test_agent_resume.py` 断言续跑不重放前序 `skill_run`。
- **A2**：前序步骤由 `execution_log/skill-run/<id>.json` 的 `SkillRun.envelope` 重建，**不把载荷复制进** `agent_run` 留痕（不造第二真相源）。若错：留痕体量翻倍且两处可能漂移。验证：续跑用例比对前序 `skill_run_id` 集合与重建步骤一致。
- **A3**：续跑的上界只计**本次新执行**的步数与 LLM 调用（前序不计），否则长循环续跑处处触界。若错：续跑一开跑就 `step_limit`。验证：前序 7 步 + 上限 8 的用例仍能执行新步。
- **A4**：过期判据取留痕写入时刻起 **30 分钟**（实现口径、可注入）；过期时显式提示并允许 `rerun`（原任务重跑、不带前序）。若错：旧证据被误当新证据。验证：时钟注入用例断言 `stale=True` 与重跑路径。
- **A5**：续跑是**新** `agent_run_id`，以 `resumed_from` 指回旧留痕；Trace 也是新链（旧链仍按旧 `trace_id` 展开）。若错：两条链共用一个 id 会混淆 `explain` 去向。验证：续跑留痕的 `trace_id` 与旧留痕不同、`resumed_from` 指回旧 id。
- **A6**：续跑入口经 `DispatchBus` 的鸭子端口注入，L3 不 import 组合根；端口未实现 `resume` 即 `unavailable` + 点名本任务。若错：表现层拿到未接线的静默失败。验证：未注入端口的用例断言 `unavailable` 且原因含 `T-AGT-011.3`。

## 涉及契约
- [05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md) —— 交还与续跑
- [05 §4](../../docs/技术架构-v2/05-L3-对话主入口.md) —— 派发回执

## 参考
- `l3/runtime/report.py`（`pending` 已携待执行的那一步）

## 备注
`report` 里已经有 `pending`（待执行的那一步），续跑主要是**回执 + UI**，不是重造循环。
