---
id: T-AGT-024.1
parent: T-AGT-024
title: 取消透传与 cancelled 终止
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M11
depends_on: [T-AGT-022.1]
status: done
decisions: [D-154]
verify: 见执行日志 [T-AGT-024.1]（范围套件 2187 passed / 2 errors；定向 56 passed；commit 5ad0e67）
---

# T-AGT-024.1 · 取消透传与 cancelled 终止

## 目标
把 `threading.Event` 透传到循环：LLM 调用可中断、动作边界检查；终止原因新增 `cancelled`，已执行写操作如实列出且不回滚。

## 验收标准（Given-When-Then）
- GWT-1：Given 运行中置取消，Then 当前工具跑完后终止，`termination=cancelled`。
- GWT-2：Given LLM 调用进行中置取消，Then 调用被中断。
- GWT-3：Given 已执行写操作，Then 产出如实列出且不回滚。

## 接口面
- **输入**：
  - [`T-AGT-022.1`](T-AGT-022.1-开跑铸号与活动run注册表.md) 的 `ActiveRun` / `ActiveRunRegistry`（`src/st_agent/l3/runtime/runs.py`；`register()` 返回控制句柄）——本叶在句柄上扩展取消事件，**不另造注册表**。
  - [`T-AGT-022.2`](T-AGT-022.2-事件模型与JSONL追加日志.md) 的 `RunEventLog` / `run_finished`（`src/st_agent/l3/runtime/events.py`）——终止原因随既有事件外显，本叶**不改事件模型**。
  - [`T-AGT-023.1`](T-AGT-023.1-SSE端点与断线重连.md) 的 `_AgentInvestigator.investigate` / `.resume`（`src/st_agent/composition/agent.py`）与 `resume_agent_run`（`src/st_agent/l3/runtime/continuation.py`）——本叶把控制句柄的事件透传给循环。
  - L0 `LlmClient.invoke(..., cancel: threading.Event)`（`src/st_agent/l0/llm/client.py`）——既有逐块取消检查，本叶只接线、不改出网面。
- **输出**：
  - `run_agent_loop(..., cancel=None)` / `resume_agent_run(..., cancel=None)` / `ToolCallProtocol.invoke_turn(..., cancel=None)`（L3 内端口；`NativeToolCallProtocol` 把同一事件交给 L0 `invoke`）。
  - `TerminationReason` 新增 `"cancelled"`；`AGENT_TERMINATION_LABELS["cancelled"]`；`build_agent_output` 的六态映射（有步 → `ok` + 终止标注，无步 → `empty` + 原因）。
  - `ActiveRun.cancel_event`（`threading.Event`）+ `request_cancel()`；`cancel_requested` 保留为只读属性。
  - **不交付**：停止端点、前端按钮、门面的停止入口（归 [`T-AGT-024.2`](T-AGT-024.2-停止端点与停止按钮.md)）。

## 可关闭的遗留
无（④ 对齐时逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区：前四册与 L6 册当前 0 条未闭；L5 册唯一未闭 `A1` 是真实渠道凭据的**人决**项，与本批无关。检查 8 兜底。）

## 假设与前提
- **A1** 取消只在 **LLM 调用边界与动作边界**生效，**不强杀正在执行的工具**（跑完再停）。若错 → GWT-1 不成立，需进程级中断，返工面在 L1 执行面与沙箱；验证：用例让执行面在跑动中置取消，断言该步仍在 `steps` 且 `termination=cancelled`。
- **A2** L0 既有的逐块 `cancel` 检查足以中断在途 LLM 调用（无 chunk 到达时，中断延迟等于等下一块的时间）。若错 → GWT-2 的时延不可控，需传输层 abort（不在本叶）；验证：Fake 协议在轮内置位取消，断言循环回 `cancelled` 而非 `llm_failed`。
- **A3** 取消标记由**注册表句柄单一承载**（进程内），后台线程与停止端点共享同一对象。若错 → 停止端点取消不到正在跑的循环，返工面 `.2`；验证：组合根用例断言 investigate 内的循环收到同一事件。
- **A4** 未传 `cancel` 的存量调用方**逐字节不变**。验证：既有 `tests/l3` / `tests/integration` 套件全绿。
