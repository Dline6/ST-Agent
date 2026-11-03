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
status: todo
decisions: [D-090, D-154]
verify:
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

待 ④ 对齐实填。预计落点：

- 输入：端点记录 `default_timeout_ms`（[`registry.py`](../../src/st_agent/l0/llm/registry.py)）· `LlmClient.invoke(timeout_ms=…)`（[`client.py`](../../src/st_agent/l0/llm/client.py)）· 网关 `stream(timeout_ms=…)`（[`gateway.py`](../../src/st_agent/l0/net/gateway.py)）· 循环 `invoke_turn`（[`protocol.py`](../../src/st_agent/l3/runtime/protocol.py)）· 配置族（[`bounds.py`](../../src/st_agent/l3/runtime/bounds.py)）。
- 输出：`ToolCallProtocol.invoke_turn(..., timeout_ms=…)` · `investigate.llm_timeout_ms`（或端点可编辑字段）· 空闲/总时限两段判定 · 设置页/API 暴露 · 用量与审计字段。

## 可关闭的遗留

无（新建任务；④ 对齐时逐册复核）。

## 假设与前提

- **A1**：OpenAI 兼容 SSE 的心跳/注释行可作为“连接仍活跃”的证据；若错，退化为“首字节超时 + 总时限”两段判定。
- **A2**：超时值属于端点能力/运行预算，而不是单次用户意图参数；若错，需在确认卡增加问项。
- **A3**：`reasoning_content` 等推理增量只用于活跃判定，不进最终答案；若错，需定义推理内容是否可回放。

## 涉及契约（链接到具体小节）

- [01-平台共享契约](../../docs/技术架构-v2/01-平台共享契约.md) §7 配置项
- [02-L0-本地优先基座](../../docs/技术架构-v2/02-L0-本地优先基座.md) §4 LLM 调用面 · §6 出网网关
- [05-L3-对话主入口](../../docs/技术架构-v2/05-L3-对话主入口.md) §10 自主查证循环

## 备注

证据：`llm-usage/cloud-main` 中 5 次 `failed` 记录耗时 61808 / 66084 / 67876 / 92691 / 105434 ms，全部报「调用超时（>60000ms）」；`client.py:382` 与 `gateway.py:422` 都是“收到下一个 chunk 才检查总耗时”。端点记录 `default_timeout_ms=60000`，`invoke_turn` 不传 timeout，设置页端点表无该字段。
