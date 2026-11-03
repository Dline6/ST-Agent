---
id: T-AGT-031.2
parent: T-AGT-031
title: LLM 超时语义与可配置
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/02-L0-本地优先基座.md
arch_link: "[02 §4](../../docs/技术架构-v2/02-L0-本地优先基座.md)"
priority: P0
milestone: M11
depends_on: [T-AGT-004.2, T-L0-003]
status: done
decisions: [D-090, D-154, D-165]
verify: 见执行日志 [T-AGT-031.2]；范围套件 3019 passed / 2 环境性 errors；live 3 passed；verify_docs PASS
---

# T-AGT-031.2 · LLM 超时语义与可配置

## 目标

把 LLM 调用超时从“端点缺省 60s、循环不传、UI 不可改、只在 chunk 到达时按总耗时判死”改为：**空闲超时与总时限分离、可由 L3 穿透配置、在途可及时中止、审计记录真实耗时**。推理模型的思考阶段不得被误判为无响应。

## 验收标准（Given-When-Then）

- GWT-1：Given 提供方持续发送心跳或推理增量但 90s 内没有正文/工具调用，Then 不因“总耗时 > 60s”误杀；空闲超时按“连续无任何活动”判定。
- GWT-2：Given 用户/配置把 LLM 超时改为 N 秒，Then 下一轮调用生效，且运行事件/用量记录可看到生效值；Given 未配置，Then 使用按端点/模型类型给出的安全缺省（显著高于 60s 或采用空闲超时）。
- GWT-3：Given 调用被超时中止，Then 实际耗时、生效超时值、失败阶段（连接/首字节/流中）进入用量与审计；`duration_ms` 不得再出现“报 60s 实际 105s”的语义错位。
- GWT-4：Given 取消事件或超时同时发生，Then 以取消优先，不把用户主动停止记成超时。

## 接口面

- **输入**：
  - 端点记录：`LlmEndpoint.default_timeout_ms`（[`registry.py`](../../src/st_agent/l0/llm/registry.py)）——非循环调用（意图理解等）的回落值。
  - L3 配置：`LoopBounds`（[`bounds.py`](../../src/st_agent/l3/runtime/bounds.py)）新增 `investigate.llm_timeout_ms`（总时限）与 `investigate.llm_idle_timeout_ms`（空闲时限），经既有 L1 配置门面读写。
  - 调用链：[`LlmClient.invoke(timeout_ms=, idle_timeout_ms=)`](../../src/st_agent/l0/llm/client.py) → [`EgressGateway.stream`](../../src/st_agent/l0/net/gateway.py) → [`openai_compat_sender_factory`](../../src/st_agent/l0/llm/http_transport.py) 的按次 sender。
  - 失败来源：[`l0/net/errors.py`](../../src/st_agent/l0/net/errors.py) 的 `EgressTimeoutError` 与 HTTP 错误、`l0/llm/client.py` 的 `Transport*` 异常。
- **输出**：
  - `ToolCallProtocol.invoke_turn(..., timeout_ms=, idle_timeout_ms=)`；`run_agent_loop` / `resume_agent_run` 同名注入参数。
  - L0 结构化失败 `LlmFailure`（`kind` / `stage` / `retryable` / `status_code` / `timeout_ms` / `idle_timeout_ms` / `elapsed_ms`）挂在 `StreamEvent` 与 `TurnResult` 上。
  - 用量记录新增 `timeout_ms` / `idle_timeout_ms` / `fail_kind` / `fail_stage`；出网审计的 `detail` 写入阶段与生效时限。
  - 空闲判定：HTTP 读超时＝连续无任何活动（含心跳 / 推理增量 / 空注释行）；总时限在流中逐行检查；取消优先于超时。

## 可关闭的遗留

无。④ 对齐时逐册复核 [L0](../遗留问题/L0-遗留问题.md) / [L1](../遗留问题/L1-遗留问题.md) / [L2](../遗留问题/L2-遗留问题.md) / [L3](../遗留问题/L3-遗留问题.md) / [L5](../遗留问题/L5-遗留问题.md) / [L6](../遗留问题/L6-遗留问题.md)：未闭项仅 L0 的 `C3` / `C5` / `D1`–`D3`（均人决）与 L5 的 `A1`（真实渠道凭据），归属均非本任务。

## 假设与前提

- **A1**：OpenAI 兼容 SSE 的心跳/注释行可作为“连接仍活跃”的证据；若错，退化为“首字节超时 + 总时限”两段判定。
- **A2**：超时值属于端点能力/运行预算，而不是单次用户意图参数；若错，需在确认卡增加问项。
- **A3**：`reasoning_content` 等推理增量只用于活跃判定，不进最终答案；若错，需定义推理内容是否可回放。
- **A4**：`investigate.llm_timeout_ms` / `investigate.llm_idle_timeout_ms` 的缺省取安全值（总 300s / 空闲 90s），覆盖端点 60s 缺省；非循环调用仍用端点 `default_timeout_ms`。若错，端点级配置将失去对循环的约束。
- **A5**：空闲时限 ≤ 总时限只作建议，不做硬拒绝（与既有上界「只提示」口径一致）；两者同时触发时按空闲/总时限中先到者报失败，取消优先。

## 涉及契约（链接到具体小节）

- [01-平台共享契约](../../docs/技术架构-v2/01-平台共享契约.md) §7 配置项
- [02-L0-本地优先基座](../../docs/技术架构-v2/02-L0-本地优先基座.md) §4 LLM 调用面 · §6 出网网关
- [05-L3-对话主入口](../../docs/技术架构-v2/05-L3-对话主入口.md) §10 自主查证循环

## 备注

证据：`llm-usage/cloud-main` 中 5 次 `failed` 记录耗时 61808 / 66084 / 67876 / 92691 / 105434 ms，全部报「调用超时（>60000ms）」；`client.py:382` 与 `gateway.py:422` 都是“收到下一个 chunk 才检查总耗时”。端点记录 `default_timeout_ms=60000`，`invoke_turn` 不传 timeout，设置页端点表无该字段。
