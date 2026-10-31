---
id: T-AGT-009.2
parent: T-AGT-009
title: 历史与记忆注入
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M8
depends_on: [T-AGT-009.1]
status: done
decisions: [D-137, D-142]
verify: "范围 1279 passed；全量 4622 passed（2 例已知环境性 ERROR）；见执行日志 [T-AGT-009.2]"
---

# T-AGT-009.2 · 历史与记忆注入

## 目标
把 `assemble_context` 的两个产出（近端消息 + 记忆切片）挂进循环工作上下文。

## 验收标准（Given-When-Then）
- GWT-1：Given 一个多轮会话，When 用户追问「那沪市呢」，Then 循环上下文含上一轮任务与结果。
- GWT-2：Given 用户有持仓 / 关注记忆，When 循环装配，Then 记忆切片进入上下文且受预算约束。
- GWT-3：Given 无历史且无记忆，Then 上下文仍可装配，空态**显式**而非静默。

## 接口面
- 输入（消费的前置接口）：
  - `T-AGT-009.1` 交付的 `session_id` 透传（`DispatchBus.dispatch` → 循环端口）
  - `SessionStore` 与 `assemble_context`（`l3/chat/session.py`）
  - `MemoryReader.query(SliceQuery)`（`l2/memory/reader.py`）
  - `run_agent_loop` / `assemble_work_context`（`l3/runtime/loop.py` / `context.py`）
- 输出（本任务交付的公共 API / 落盘位置）：
  - `WorkContext.history: tuple[ChatMessage, ...]` 与 `WorkContext.memory: tuple[MemorySlice, ...]`（缺省空元组）
  - `assemble_work_context(..., history=(), memory=())` 与 `run_agent_loop(..., history=(), memory=())`
  - 组合根 `_AgentInvestigator` 增 `sessions` / `reader` 注入；`build_m4_runtime` 的装配缝把 L2 句柄传入
  - `WorkContext.render_prompt` 增历史 / 记忆两段，空态**显式成行**

## 可关闭的遗留
无（同父任务）

## 假设与前提
- A1：只并入 `assemble_context` 的两个产出（`messages` / `memory.slices`），不把 `SessionContext` 当工作上下文（调研报告 §8.4 已钉死）。若错：上下文语义混淆、预算双算。验证：字段断言 + 既有会话装配用例不变。
- A2：无历史且无记忆时 `render_prompt` **显式给出空态行**（不静默省略）。若错：模型无法区分「空」与「未注入」。验证：GWT-3 用例。
- A3：循环本体不读存储——历史与记忆由组合根预取后注入，`run_agent_loop` 保持纯函数可测。若错：循环与 L0 / L2 存储耦合、单测需建真库。验证：循环既有 Fake 用例零改动。
- A4：L3 消费 L2 的 `MemorySlice` 属向下依赖（`l3 > l2`），不违反铁律 7。若错：`tests/test_layering.py` 先红。验证：跨层套件。

## 涉及契约
- [05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md) —— 循环工作上下文的字段面
- [03 §1](../../../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md) —— SkillContext 是否增记忆句柄（与 G10 同题）

## 参考
- `l3/runtime/context.py`（`WorkContext`）
- `l3/chat/session.py`（`assemble_context`）
- `l2/memory/reader.py`

## 备注
**不得**把 `assemble_context` 当「循环工作上下文」本身——两者内容与消费者不同（调研报告 §8.4 已钉死这条）。本叶只把它的**两个产出**作为新字段并入。
