---
id: T-AGT-025
parent: null
title: 工具调用自愈
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P1
milestone: M11
depends_on: [T-AGT-020]
status: done
decisions: [D-154, D-157]
verify: 见执行日志 [T-AGT-025]（范围套件 1426 passed；定向 93 passed；verify_docs --strict PASS；commit e7ed13b）
---

# T-AGT-025 · 工具调用自愈

## 目标
模型请求不存在的工具时，从硬终止改为回灌「目录无此工具 + 当前目录」允许重选；仅重复违规或到上界才终止。

## 验收标准（Given-When-Then）
- GWT-1：Given 模型请求不存在的工具，Then 回灌错误与当前目录，循环继续。
- GWT-2：Given 连续多次未知工具，Then 终止并如实标注。
- GWT-3：Given 正常工具调用，Then 行为逐字节不变。

## 接口面

- **输入**：
  - `run_agent_loop(tools=...)` 的工具目录（`SkillRegistry.tool_catalog()` 的 `ToolEntry` 序列，`T-AGT-004.2` 交付）；
  - `ToolCallProtocol.invoke_turn(context, ...)` 返回的 `turn.tool_calls`（`st_agent.l0.llm.ToolCall`）；
  - 工作上下文装配 `assemble_work_context(...)`（`l3/runtime/context.py`）——本任务为它新增**未知工具回灌**段；
  - 运行事件回调 `emit`（`T-AGT-022.2` 的 11 类事件，本任务**不新增**事件类型）。
- **输出**：
  - `run_agent_loop` 的未知工具分支：**不再硬终止**——回灌错误说明与当前目录指示后继续下一轮；仅**连续第 2 轮**仍出现未知工具（或到达既有上界）才终止，终止原因沿用既有 `TerminationReason.unknown_tool`（不新增枚举值）；
  - `WorkContext.unknown_tool_note`（新增字段，缺省 `""`）与 `assemble_work_context(..., unknown_tool_note="")`（新增可选参，缺省时装配输出**逐字节不变**）；
  - 无新增落盘、无新分区、无新 Trace 步骤 / `skill_run`（未知工具本就未执行）。
- **下游**：运行面板（`T-AGT-026.1`）可据 `model_turn` 轮次自然呈现重试，本任务不要求 UI 改动。

## 可关闭的遗留

无。开工逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区：L0 / L1 / L2 / L3 / L6 均 0 条；L5 仅 `A1`（真实渠道凭据，人决），与本任务无关。

## 假设与前提

- **A1**：回灌后允许模型**纠正一次**——**连续第 2 轮**仍出现未知工具即终止（「重复违规」的落地取值）。
  - 若错的影响：终止过早（多给一轮更稳）或过晚（模型在同一错误上打转）。
  - 验证方式：GWT-1（第 1 轮回灌后继续并完成）与 GWT-2（第 2 轮终止）成对断言。
- **A2**：同轮混合（未知工具 + 已知工具）时**整轮不执行**、只回灌——与既有「任一被拒即整批不执行」同口径，避免半程执行。
  - 若错的影响：同轮执行语义与闸门口径分叉。
  - 验证方式：混合批用例断言已知调用本任务内未执行（无 `skill_run`），下一轮可正常推进。
- **A3**：回灌**不占步数**，但消耗一次既有 LLM 调用计数（每轮本就要问一次模型）。
  - 若错的影响：步数 / LLM 上界的语义偏移。
  - 验证方式：用例断言 `executed_steps` 不变、`llm_calls` 随轮次递增。
- **A4**：回灌文案只点名未知工具名 + 指向本轮工具目录（原生工具通道经 `tools` 已下发；惰性态经摘要索引），不复述整份目录。
  - 若错的影响：模型看不到可选能力而反复试错。
  - 验证方式：GWT-1 断言 `unknown_tool_note` 含未知名与目录指示，且非惰性态的目录段照旧完整。
- **A5**：契约先行——改 [05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 增补段落；[01 §4](../../../../docs/技术架构-v2/01-平台共享契约.md) / [§7](../../../../docs/技术架构-v2/01-平台共享契约.md) **不改**（不新增事件类型、无新配置项）。
  - 若错的影响：契约与实现分叉，或需在 01 登记新条目。
  - 验证方式：`verify_docs.py --strict` + 人工复核 §10 段落与实现一致。
