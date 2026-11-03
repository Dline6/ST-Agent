---
id: T-AGT-033.6
parent: T-AGT-033
title: Agent 自调申请与进展检查
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P2
milestone:
depends_on: [T-AGT-033.4, T-AGT-033.5]
status: done
decisions: [D-090, D-154, D-166]
verify: D-166；范围套件 4386 passed / 4 例测试期望滞后（已修正并定向复验）/ 2 例本机 test_shell 环境性 ERROR；改动区复跑 655 passed；live 3 passed；verify_docs --strict PASS。详见执行日志对应条目。
---

# T-AGT-033.6 · Agent 自调申请与进展检查

## 目标

在硬信封内给 Agent 自调空间：模型可通过一个控制动作申请更多步数/时间/上下文，运行时在信封内直接批、超出才交还用户；`sk_result_read` / `sk_tool_search` 的分片与展开数量由模型自选、运行时钳制；同时做**进展检查**——连续重复读同一段、没有新证据时压缩或终止，而不是无限给预算。

## 验收标准（Given-When-Then）

- GWT-1：Given 模型申请更多步数/时间，Then 运行时在总 token/时间/费用信封内直接批准并记事件；超出信封则返回显式拒绝或交还卡，不静默忽略。
- GWT-2：Given 模型对 `sk_result_read` 指定 `max_chars` / `sk_tool_search` 指定 `limit`，Then 运行时按当前可用分片/目录配额钳制，并说明实际生效值。
- GWT-3：Given 连续 N 轮重复读同一 `skill_run_id` 的同一 offset 且无新证据，Then 进展检查触发：先压缩/提示“已读”，仍重复则终止并交还，不继续烧预算。
- GWT-4：Given 总资源边界（token/时间/费用），Then 模型无法通过任何动作自行抬高；只能申请，由运行时/用户决定。
- GWT-5：Given 申请与批准/拒绝，Then 进运行事件与 `agent_run` 留痕，可审计。

## 接口面

- **输入**：
  - `run_agent_loop` 的控制动作拦截（[`loop.py`](../../src/st_agent/l3/runtime/loop.py)）与信封余量；
  - `WorkContext` 的步骤 / 预算（[`context.py`](../../src/st_agent/l3/runtime/context.py)）；
  - `sk_result_read`（[`results.py`](../../src/st_agent/l1/builtin/results.py)）· `sk_tool_search`（[`tool_search.py`](../../src/st_agent/l3/runtime/tool_search.py)）；
  - 运行事件（[`events.py`](../../src/st_agent/l3/runtime/events.py)）。
- **输出**：
  - 新内置原语 `sk_budget_request`（[`l1/builtin/budget_request.py`](../../src/st_agent/l1/builtin/budget_request.py)），出厂清单登记 `autonomous-ok`；
  - 循环在信封内直接批准、超出显式拒绝；`BudgetRequestRecord` 进 `LoopOutcome` / `AgentRunRecord`，并新增运行事件 `budget_request`；
  - `WorkContext.budget_note` / `progress_note` 回灌给模型；
  - 进展检查：同一 `skill_run_id` 同一 `offset` 连续 3 次读取无新证据 → 终止原因 `no_progress`（先给「已读」提示）。

## 可关闭的遗留

无（逐册复核 L0 / L1 / L2 / L3 / L5 / L6 未闭区：无归属＝本任务、或无解封条件＝本任务 `done` 的条目）。

## 假设与前提

- **A1**：自调申请是控制动作（不产 `skill_run` / Trace 步），与计划/提问同型；若错，需走 L1 注册面。
- **A2**：总资源边界由 [`T-AGT-033.4`](T-AGT-033.4-长任务步骤压缩与预算联动.md) 定义；本叶只做“在边界内申请”。
- **A3**：进展检查用确定性判据（相同工具+参数+offset 重复次数），不做语义判断；若错，需引入轻量模型判定。

## 涉及契约（链接到具体小节）

- [01-平台共享契约](../../docs/技术架构-v2/01-平台共享契约.md) §4 Trace 步骤口径 · §7 配置项
- [05-L3-对话主入口](../../docs/技术架构-v2/05-L3-对话主入口.md) §10 上界与终止

## 备注

证据：`agr_ec73…` 18 轮里 20 次 `sk_result_read`，多次重复读 offset 0；`max_steps` / `max_llm_calls` 设到 1000，没有“进展检查”兜底。
