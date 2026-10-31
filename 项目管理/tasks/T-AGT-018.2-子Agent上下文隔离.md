---
id: T-AGT-018.2
parent: T-AGT-018
title: 子 Agent（上下文隔离）
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P2
milestone: M10
depends_on: [T-AGT-018.1]
status: done
decisions: [D-137, D-150, D-151]
verify: "全量 4930 passed / 16 deselected / 2 errors（两例 test_shell 环境性）；新增 tests/l3/test_agent_subagent.py 8 例、tests/l6/test_authorization.py 分级用例；见执行日志 [T-AGT-018.2]"
---

# T-AGT-018.2 · 子 Agent（上下文隔离）

> **范围回挂（2026-10-31，[D-151](../决策日志.md)）**：本叶曾按 [D-150](../决策日志.md) 移出 M10 暂悬空；
> 负责人 2026-10-31 指示「连 018.2」一并交付，故 `milestone` 回到 M10、并入 [`T-INT-011`](T-INT-011-M10集成关卡长任务运行时.md)
> 的依赖与 GWT-4。与 [`T-AGT-018.1`](T-AGT-018.1-并行工具调用.md) 同批推进。

## 目标
探索型任务在隔离子上下文里跑，只回传结论与引用。

## 验收标准（Given-When-Then）
- GWT-1：Given 探索型任务，When 派生子 Agent，Then 其上下文不进主窗，仅回传结论与引用。
- GWT-2：Given 子 Agent 失败，Then 主循环可按规则改选。
- GWT-3：Given 预算，Then 子 Agent 的调用计入总上界。

## 接口面
- 输入（消费的前置接口）：
  - [`T-AGT-018.1`](T-AGT-018.1-并行工具调用.md) 的循环底座——同一 `run_agent_loop`；派生是**控制面动作**，按调用顺序执行，不进并行批（避免嵌套并发）。
  - `l3/runtime/loop.py::run_agent_loop`——递归调用自身：工具目录剔除 `sk_spawn_agent`、双上界取父循环**剩余预算**、`allow_sub_agents=False`、`parent_trace_id=父链 trace_id`。
  - `l1/builtin/spawn.py::SPAWN_AGENT`（新）——内置原语 `sk_spawn_agent`（参数 `task`），经统一注册面进工具目录；L3 拦截执行，直接调用返回显式不可用。
  - `l3/runtime/context.py::WorkContext.subagent_note`——子 Agent 结论 + 引用进主上下文的**唯一入口**。
  - `l3/runtime/report.py::conclude_agent_run`——子循环留痕；`AgentRunRecord` 新增 `parent_trace_id` / `executed_steps` / `sub_agents`。
- 输出（本任务交付的公共 API / 落盘位置）：
  - `run_agent_loop(..., allow_sub_agents=True, nested_recorder=None, parent_trace_id="", prior_sub_agents=())`——`prior_sub_agents` 供续跑从旧留痕重建子 Agent 摘要。
  - `LoopOutcome.sub_agents: tuple[SubAgentRun, ...]` / `executed_steps` / `parent_trace_id`；`SubAgentRun` / `NestedRunRef` / `NestedRecorder`（`l3/runtime/loop.py`）。
  - `AgentRunRecord.parent_trace_id` / `sub_agents` / `executed_steps`（落 `execution_log` 的 `agent_run/` 与 `skill-run/`，沿用既有分区）。
  - 组合根 `composition/agent.py::_AgentInvestigator` 注入默认 `nested_recorder`（`conclude_agent_run` + `agent_log_ref_of`）。
  - 风险分级 `skill.sk_spawn_agent → autonomous-ok`（[08 §5](../../docs/技术架构-v2/08-L6-反思演进.md)）。
  - 测试 `tests/l3/test_agent_subagent.py`。

## 可关闭的遗留
无（六册未闭区逐册核过，无「归属＝本任务」或「解封条件＝本任务 `done`」的条目；检查 8 = 0）。

## 假设与前提
- **A1**（子上下文隔离口径）——子循环只含子任务 + 同一工具目录（剔除 `sk_spawn_agent`）+ 同一记忆切片；不含主循环历史、已执行步骤与计划。若错：隔离目标被破坏。验证：用例断言子上下文 `steps == ()`、`history == ()`、目录无 spawn。
- **A2**（只允许一层）——子循环 `allow_sub_agents=False` 且目录剔除；若仍被调用即 `unknown_tool` 显式终止。若错：递归派生无上界。验证：一层用例。
- **A3**（预算计入父上界）——子 Agent 的步数（含派生本身 1 步）与 LLM 调用次数计入父循环的 `executed_steps` / `llm_calls`；子循环双上界取父剩余预算。若错：子 Agent 绕过总上界。验证：`max_steps=3` 用例断言子循环在剩余 2 步触界、父 `executed_steps == 3`。
- **A4**（子链独立且可审计）——子循环另起 `trace_id`，步骤不并入主链；子运行另落 `AgentRunRecord` 并以 `parent_trace_id` 指回主链。若错：主窗被污染或子动作不可追溯。验证：用例断言父 `trace.steps == ()`、子记录 `parent_trace_id` 正确、子 `skill_run` 记录存在。
- **A5**（失败不改写主循环）——子循环非 `finished` 时，主循环收到显式失败说明并继续（可改选）。若错：一次子任务失败拖停整轮。验证：`needs_confirmation` 用例断言父循环仍 `finished`。
- **A6**（分级 `autonomous-ok`）——派生自身不改盘 / 不出网 / 不起命令，子步逐步过同一闸门。若错：出厂档下派生不可达或放宽子步授权。验证：`tests/l6/test_authorization.py` 分级与顺序用例。

## 涉及契约
- [05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md) —— 子 Agent 与上下文隔离
- [01 §4](../../docs/技术架构-v2/01-平台共享契约.md) —— 子链与父链的 Trace 归属
- [08 §5](../../docs/技术架构-v2/08-L6-反思演进.md) —— 风险分级清单

## 参考
- Codex 的 `spawn_agent` 一族

## 备注
本期只做**一层**子 Agent，不做 Agent 图 / 角色 / 消息板。
