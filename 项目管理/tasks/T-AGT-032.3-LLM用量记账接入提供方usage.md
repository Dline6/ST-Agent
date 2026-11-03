---
id: T-AGT-032.3
parent: T-AGT-032
title: LLM 用量记账接入提供方 usage
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/02-L0-本地优先基座.md
arch_link: "[02 §4](../../docs/技术架构-v2/02-L0-本地优先基座.md)"
priority: P2
milestone:
depends_on: [T-L0-003]
status: done
decisions: [D-090, D-167]
verify:
---

# T-AGT-032.3 · LLM 用量记账接入提供方 usage

## 目标

`LlmUsageRecord` 不再只按可见文本估算 completion tokens：读取 OpenAI 兼容流末尾的 `usage`（含 reasoning tokens，若提供方给出），无 `usage` 时回退估算并标注来源；失败调用也如实记录已产生的推理/可见 token 与真实耗时。

## 验收标准（Given-When-Then）

- GWT-1：Given 提供方在流末返回 `usage`，Then 用量记录使用提供方数值，并在记录中标注 `source=provider`。
- GWT-2：Given 提供方未返回 `usage`，Then 回退到现有估算，并标注 `source=estimate`；不得把估算值伪装成提供方值。
- GWT-3：Given 推理模型产生 `reasoning_content`，Then 该增量计入“活跃判定”和可得的 reasoning token 统计，不进入最终答案文本。
- GWT-4：Given 调用超时/中断，Then 记录实际耗时、已产生 token（若可得）与失败阶段，供成本与稳定性分析。

## 接口面

- **输入**：SSE 解析（[`http_transport.py`](../../src/st_agent/l0/llm/http_transport.py)）· `_stream` 的用量写入（[`client.py`](../../src/st_agent/l0/llm/client.py)）· `LlmUsageRecord`（[`models.py`](../../src/st_agent/l0/llm/models.py)）。
- **输出**：请求体新增 `stream_options.include_usage=true`；流式项新增 `ProviderUsage`（提供方流末 `usage`）与 `ReasoningDelta`（`delta.reasoning_content` / `delta.reasoning`）；`LlmUsageRecord` 新增 `usage_source`（`provider` / `estimate`）与 `reasoning_tokens`；推理增量只计活动与 token，不进答案文本；失败调用同样记录实际耗时、已产生 token 与失败阶段。

## 可关闭的遗留

无（L0 / L1 / L3 遗留册未闭区均无归属本任务或解封于本任务 `done` 的条目）。

## 假设与前提

- **A1**：OpenAI 兼容端点的 `usage` 形态可用（`prompt_tokens` / `completion_tokens` / `completion_tokens_details.reasoning_tokens`）。若错的影响：需按提供方适配器扩展，不猜字段。验证方式：真实 SSE 行用例与 live 子集。
- **A2**：`LlmUsageRecord` 新增字段保持向后兼容（默认 `estimate` / 0）。若错的影响：需迁移旧记录或分版本读取。验证方式：旧记录解析与既有用例全绿。
- **A3**：推理内容默认不落盘、不回放，只记计数。若错的影响：需在隐私设置增加开关。验证方式：盘上明文扫描与用量记录字段断言。

## 涉及契约（链接到具体小节）

- [01-平台共享契约](../../docs/技术架构-v2/01-平台共享契约.md) §3 用量归属
- [02-L0-本地优先基座](../../docs/技术架构-v2/02-L0-本地优先基座.md) §4 LLM 调用面

## 备注

证据：5 次超时记录的 `completion_tokens` 均为 0；`_stream_items` 只读 `delta.content` / `tool_calls`，不读 `usage` / `reasoning_content`。
