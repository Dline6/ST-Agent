---
id: T-AGT-033.1
parent: T-AGT-033
title: 预算信封与计量口径
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M11
depends_on: [T-AGT-009.3, T-L0-003]
status: done
decisions: [D-090, D-142, D-166]
verify: D-166；范围套件 4386 passed / 4 例测试期望滞后（已修正并定向复验）/ 2 例本机 test_shell 环境性 ERROR；改动区复跑 655 passed；live 3 passed；verify_docs --strict PASS。详见执行日志对应条目；commit `00f11a5`。
---

# T-AGT-033.1 · 预算信封与计量口径

## 目标

每次运行只算一个 `ContextEnvelope`：以端点真实窗口 `W` 为唯一硬边界，扣除输出预留与工具载荷后得到可用预算；统一字符/token 口径（中文用保守比例或 provider tokenizer），端点上限检查把 `tools` 计入；会话层与工作层共用同一份计量并都产出 `dropped`；记忆配额按实际命中动态分配，不再固定 50%。不再向用户暴露“建议不超过 32000”这类与端点窗口无关的数字。

## 验收标准（Given-When-Then）

- GWT-1：Given 端点 `max_context_tokens=128000`，Then 运行时派生 `ContextEnvelope`（窗口 × 安全比例 − 输出预留 − `tokens(tools)`）；用户界面不再出现与窗口脱节的固定“建议上限”。
- GWT-2：Given 中文 prompt，Then token 估算使用保守比例或 provider tokenizer；端点上限检查**同时计入 `tools`**；超限时返回显式 `context_overflow`（带续跑入口），不落到 `llm_failed`。
- GWT-3：Given 会话层 `assemble_context` 因预算舍弃消息，Then 被舍项进入 `dropped` 并一路带到 `agent_run` 留痕；不再出现“会话层丢了、报告显示空”的双层不一致。
- GWT-4：Given 记忆为空或命中少于配额，Then 未用配额回收给历史/步骤；不再固定 50% 浪费（对应记忆机制复盘第 ⑨ 条）。**切片自身的计量/截断归 [`T-MEM-001.5`](../../T-MEM-001.5-切片计量与截断策略.md)**；本叶只管配额分配与回收。
- GWT-5：Given 端点能力未知，Then 用保守 fallback 并显式标注；删除 `4000` / `16000` 两个漂移缺省，改由同一函数派生。
- GWT-6：Given 两次相同输入与相同端点，Then 信封与分配逐字节一致（时钟/随机不参与）。

## 接口面

- **输入**：
  - `EndpointCapability.max_context_tokens` / `estimate_tokens`（[`l0/llm/models.py`](../../../../src/st_agent/l0/llm/models.py)）——端点窗口与中文保守计量；
  - `NativeToolCallProtocol` 新增 `context_window_tokens()`（[`l3/runtime/protocol.py`](../../../../src/st_agent/l3/runtime/protocol.py)）——端点未登记即 `None`，走保守 fallback；
  - `LlmClient.invoke` 的上下文上限检查（[`l0/llm/client.py`](../../../../src/st_agent/l0/llm/client.py)）——把 `tools` 的 JSON 也计入；
  - `assemble_context`（[`l3/chat/session.py`](../../../../src/st_agent/l3/chat/session.py)）· `assemble_work_context` / `_fit_budget`（[`l3/runtime/context.py`](../../../../src/st_agent/l3/runtime/context.py)）· `LoopBounds.context_budget_tokens()`（[`l3/runtime/bounds.py`](../../../../src/st_agent/l3/runtime/bounds.py)）· `AgentRunRecord.context_dropped`（[`l3/runtime/report.py`](../../../../src/st_agent/l3/runtime/report.py)）。
- **输出**：
  - 新 [`l3/runtime/envelope.py`](../../../../src/st_agent/l3/runtime/envelope.py)：`ContextEnvelope`（窗口 / 安全比例 / 输出预留 / 工具与系统提示词 token / 可用 token / 字符预算 / 目录配额 / 来源与说明）与 `derive_context_envelope()`；
  - `SessionContext.dropped` / `main_task` / `follow_up`（会话层取舍与主任务锚点）；
  - `LoopOutcome` / `AgentRunRecord` 新增 `context_window_tokens` / `context_available_tokens` / `context_envelope_note`；
  - 新终止原因 `context_overflow`（可经既有 `resume` 的 `retry` 续跑）；`LlmFailure.kind` 新增 `context_overflow`；
  - `investigate.context_budget` 改为**可选 token 封顶（0＝自动派生）**，删除 `4000` / `16000` 两个漂移缺省。

## 可关闭的遗留

无（逐册复核 L0 / L1 / L2 / L3 / L5 / L6 未闭区：无归属＝本任务、或无解封条件＝本任务 `done` 的条目）。

## 假设与前提

- **A1**：端点窗口是唯一可靠硬边界；提供方 tokenizer 不可得时用“中文 ≥1 字符/token”的保守比例，宁可提前压缩也不把超限请求发出去。
- **A2**：`ContextEnvelope` 是运行期派生值，不作为用户可编辑配置；用户只配置“总 token/费用/时间”硬上限与“单次调用最长多久”。
- **A3**：会话层 `dropped` 字段为新增可选字段，旧会话/旧留痕按空处理，向后兼容。
- **A4**：记忆切片自身的计量（是否含 `edges`）与截断策略归 [`T-MEM-001.5`](../../T-MEM-001.5-切片计量与截断策略.md)；本叶不重复实现。

## 涉及契约（链接到具体小节）

- [01-平台共享契约](../../../../docs/技术架构-v2/01-平台共享契约.md) §3 用量归属 · §7 配置项 · §8 时区口径
- [02-L0-本地优先基座](../../../../docs/技术架构-v2/02-L0-本地优先基座.md) §4 LLM 调用面
- [05-L3-对话主入口](../../../../docs/技术架构-v2/05-L3-对话主入口.md) §10 规模预算与超限取舍

## 备注

证据：`estimate_tokens` 为 4 字符/token（`models.py:80`）；`client.py:196` 只检查 `prompt`，未计 `tools`；`SessionContext` 无 `dropped`；`assemble_context` 固定 50% 记忆配额（`session.py:539`）；`DEFAULT_CONTEXT_BUDGET=4000` 与 `DEFAULT_WORK_CONTEXT_BUDGET=16000` 两个缺省已漂移。
