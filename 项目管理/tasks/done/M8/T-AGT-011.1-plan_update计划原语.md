---
id: T-AGT-011.1
parent: T-AGT-011
title: plan_update（计划原语）
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M8
depends_on: [T-AGT-008.3]
status: done
decisions: [D-137, D-144]
verify: 2026-10-29 `[T-AGT-011.1]` · 范围套件 3276 passed / 2 errors（`test_shell` 本机环境性）· `verify_docs --strict` PASS · 未触联网面
---

# T-AGT-011.1 · plan_update（计划原语）

## 目标
模型可显式维护步骤计划，并在执行流里可渲染。

## 验收标准（Given-When-Then）
- GWT-1：Given 一个多步任务，When 模型调用 `plan_update`，Then 计划进入工作上下文并可被执行流渲染。
- GWT-2：Given 计划更新，Then **不产生** Trace 步（决策不入 Trace），但记入 `agent_run` 报告。
- GWT-3：Given 空计划，Then 显式清空，不静默忽略。

## 接口面
- 输入（消费的前置接口）：
  - `T-AGT-008.2` / `T-AGT-008.3`：`install_builtins`（[`src/st_agent/l1/builtin/install.py`](../../../../src/st_agent/l1/builtin/install.py)）把内置原语种进同一 `SkillRegistry`；`SkillRegistry.tool_catalog()` 的 `ToolEntry`（`name` / `description` / `parameters` / 对象键）即模型可见面与实参拆分依据（[`tool_catalog.py`](../../../../src/st_agent/l1/skills/tool_catalog.py)）。
  - `T-AGT-009.3`：工作上下文装配 `assemble_work_context` / `WorkContext`（[`src/st_agent/l3/runtime/context.py`](../../../../src/st_agent/l3/runtime/context.py)）——计划作为新增的装配输入与渲染段。
  - `T-AGT-004.2` / `T-AGT-004.3`：`run_agent_loop` / `LoopOutcome`（[`src/st_agent/l3/runtime/loop.py`](../../../../src/st_agent/l3/runtime/loop.py)）与 `AgentRunRecord` / `conclude_agent_run`（[`src/st_agent/l3/runtime/report.py`](../../../../src/st_agent/l3/runtime/report.py)）。
  - `T-AGT-012.1`：运行期授权清单 `DEFAULT_RISK_GRADING`（[`src/st_agent/l6/authorization.py`](../../../../src/st_agent/l6/authorization.py)）与 `ActionGate`（[`src/st_agent/l3/runtime/gate.py`](../../../../src/st_agent/l3/runtime/gate.py)）。
- 输出（本任务交付的公共 API / 落盘位置）：
  - 内置原语 `sk_plan_update`：声明体 `src/st_agent/l1/builtin/plan_update.py`，经 `BUILTIN_PRIMITIVES` 登记；`input_schema` 的 `plan`（`[{title, status}]`）走对象输入通道。
  - `PlanStep` 模型与 `WorkContext.plan` / `plan_updated` / `plan_error` 及「当前计划」渲染段（`l3/runtime/context.py`）。
  - `run_agent_loop` 新增 `plan` / `plan_updated` 入参；`LoopOutcome.plan` / `plan_updated` / `plan_error`（`l3/runtime/loop.py`）。
  - `AgentRunRecord.plan` / `plan_updated`（`l3/runtime/report.py`）；`describe_agent_run` 的 `plan` / `plan_updated` 槽（`l3/render/describe.py`）。
  - 契约：01 §7 风险清单增 `skill.sk_plan_update = autonomous-ok`；05 §10 增计划原语的决策语义（不产 Trace 步）。

## 可关闭的遗留
无（逐册核过 [L0](../../../遗留问题/L0-遗留问题.md) / [L1](../../../遗留问题/L1-遗留问题.md) / [L2](../../../遗留问题/L2-遗留问题.md) / [L3](../../../遗留问题/L3-遗留问题.md) / [L5](../../../遗留问题/L5-遗留问题.md) / [L6](../../../遗留问题/L6-遗留问题.md) 未闭区，无「归属＝本任务」或「解封条件＝本任务 `done`」的条目）。

## 假设与前提
- **A1**：计划属**决策**，不进 Trace、也不产 `skill_run` 留痕；只进工作上下文 + `agent_run` 留痕 + 描述槽。若错：逐步的计划历史不在审计面，需另立留痕（返工落 `.report` 与 UI）。验证：`tests/l3/test_agent_plan.py` 断言计划更新前后 `Trace.steps` 与 `skill_run` 记录数不变。
- **A2**：`sk_plan_update` 是纯控制动作（无文件 / 网络 / 数据副作用），出厂清单标 `autonomous-ok`；否则出厂档下被闸门判 `needs-confirmation`，功能不可用。若错：用户需逐步批准计划更新，M8 的「出厂可跑」不成立。验证：`tests/l3/test_agent_gate.py` 用例 + 契约 01 §7。
- **A3**：计划状态词取 `pending` / `active` / `done`，步数不设上限（受工作上下文预算约束）。若错：需另立枚举或上限，属实现口径可改。验证：模型与渲染两处共用同一 `Literal`，`tests/l3/test_agent_plan.py` 钉住。
- **A4**：非法计划（非数组 / 步缺 `title` / 状态不在枚举）**不终止循环**，写入 `plan_error` 供下一轮模型自我纠正；到上界仍未纠正即按既有上界终止。若错：模型可能反复重试，但受 LLM 调用上界约束，不会跑飞。验证：坏形态用例断言下一轮上下文含错误原因且循环继续。

## 涉及契约
- [05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md) —— 计划字段与渲染
- [01 §4](../../../../docs/技术架构-v2/01-平台共享契约.md) —— 决策不入 Trace 的口径

## 参考
- Codex 的 `update_plan` / Claude Code 的 `TodoWrite`

## 备注
做成**工具**而非循环内字段：可过闸门、可留痕、可渲染，且与其余动作同一形态。
