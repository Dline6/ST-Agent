---
id: T-AGT-001
parent: null
title: LLM 工具调用通道（tools 入参 / tool_calls 出参）
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/02-L0-本地优先基座.md
arch_link: "[02 §4](../../docs/技术架构-v2/02-L0-本地优先基座.md)"
priority: P0
milestone: M5
depends_on: []
status: todo
decisions: [D-090, D-091]
verify:
---

# T-AGT-001 · LLM 工具调用通道（tools 入参 / tool_calls 出参）

## 目标

把 [02 §4](../../docs/技术架构-v2/02-L0-本地优先基座.md) 新登记的**工具调用**接口能力落成代码——端点接口能**收工具条目**、能**回结构化工具调用**：

1. **入参**：`LlmClient.invoke` 增可选 `tools`（工具条目序列），透传至 transport 请求体的 `tools` 字段。
2. **出参**：`StreamEvent` 承载**工具调用**（名称 + 参数对象 + 调用标识）；流式下按 index 拼接 `delta.tool_calls` 增量——**不得把增量当正文文本**。
3. **能力项**：`EndpointCapability` 增 `supports_function_calling`（缺省 `false`——**缺省不得假定为真**，[02 §4](../../docs/技术架构-v2/02-L0-本地优先基座.md)）。
4. **不动既有单轮纯文本路径**：`tools` 缺省为空时，请求体与行为**逐字节不变**。

## 验收标准（Given-When-Then）

- **GWT-1** Given 不带 `tools` 调用，When 构造请求体，Then 与既有**逐字节一致**（无 `tools` 字段）。
- **GWT-2** Given 带 `tools` 调用，When 构造请求体，Then 含 `tools` 且形态合法（名称 / 描述 / 参数 schema）。
- **GWT-3** Given 端点以流式分片返回工具调用增量，When 消费事件流，Then **拼出完整调用**（名称 + 完整参数对象 + 标识），且**不混入正文文本**。
- **GWT-4** Given 端点未声明 `supports_function_calling`，When 上层请求工具面，Then **显式拒绝**（能力协商失败），**不静默发包**。
- **GWT-5** Given `tools` 形态非法（空名 / 结构坏），When 调用，Then `validation_failed` 且**不发包**。

## 接口面

- **输入**：
  - 既有 [`LlmClient`](../../src/st_agent/l0/llm/client.py)（`invoke` 签名）/ [`StreamEvent`](../../src/st_agent/l0/llm/models.py) / `EndpointCapability` / [`http_transport`](../../src/st_agent/l0/llm/http_transport.py) 的 sender 工厂闭包。
  - 能力协商既有面 [`LlmEndpointRegistry`](../../src/st_agent/l0/llm/registry.py) 的 `satisfies`（本任务**只扩能力项**，不改协商逻辑）。
  - 契约口径：[02 §4 LLM 调用抽象](../../docs/技术架构-v2/02-L0-本地优先基座.md) · [01 §2 工具暴露面的唯一来源](../../docs/技术架构-v2/01-平台共享契约.md)。
- **输出**：
  - `src/st_agent/l0/llm/**`——`invoke` 的 `tools` 入参、工具调用事件形态、`supports_function_calling` 能力项。
  - 供 [`T-AGT-002`](T-AGT-002-端点能力启动探测.md)（探针要发 `tools`）与 [`T-AGT-004.2`](T-AGT-004.2-循环驱动、协议端口与上界.md)（循环要消费工具调用）消费的**接口面**。

## 可关闭的遗留

- 无（开工 ④ 对齐时逐册读 [L0](../遗留问题/L0-遗留问题.md) / [L1](../遗留问题/L1-遗留问题.md) / [L2](../遗留问题/L2-遗留问题.md) / [L3](../遗留问题/L3-遗留问题.md) / [L5](../遗留问题/L5-遗留问题.md) / [L6](../遗留问题/L6-遗留问题.md) 未闭区复核）

## 假设与前提

- **A1 · 走原生 function calling，不做 prompt 约定 JSON**——前提：[D-090](../决策日志.md) D-a 拍定「协议做成端口 + T1 先行」，理由是可靠性增益落在循环最脆的两处（参数 schema 由提供方强制、结束信号无歧义）。
  **若错的影响**：返工面 = transport 请求体、`StreamEvent` 形态、循环侧协议端口——循环本体（[`T-AGT-004`](T-AGT-004-循环运行时.md)）**不受影响**（它只认端口）。
  **验证方式**：GWT-2 / GWT-3 以真发送与真解析证实；协议端口的可替换性由 [`T-AGT-004.2`](T-AGT-004.2-循环驱动、协议端口与上界.md) 的端口单测佐证。
- **A2 · 能力的缺省必须是 `false`**——前提：[02 §4](../../docs/技术架构-v2/02-L0-本地优先基座.md) 明写「缺省不得假定为真」；既有引导装载的 `LLM_CAPABILITY` 是**宽松假设**（其 docstring 自陈「真实档位待设置页任务按端点配置」），若照抄其取向给 `true`，会把 `tools` 发给不认识它的端点。
  **若错的影响**：假阳性 → 循环拿到「静默无视」的结果，**比不门控更糟**。
  **验证方式**：GWT-4 以「未声明即拒绝」钉住；本任务**不**给 `LLM_CAPABILITY` 填 `true`（那是 [`T-AGT-002`](T-AGT-002-端点能力启动探测.md) 探测结果的事）。
- **A3 · 单轮纯文本路径逐字节不变**——前提：`tools` 缺省为空即退回既有行为，既有 5 个调用点（意图理解 / 视角合成 / 证据重研判 / L6 两处）全不带 `tools`。
  **若错的影响**：M1–M4 的既有链路回归。
  **验证方式**：GWT-1 + 跑 `tests/l0` 全量与跨层套件。

## 涉及契约

- [02 §4 LLM 调用抽象](../../docs/技术架构-v2/02-L0-本地优先基座.md)（工具调用一行 + 能力诚实性五条）
- [01 §2](../../docs/技术架构-v2/01-平台共享契约.md)（工具条目的唯一来源）
- [01 §5 ResultEnvelope](../../docs/技术架构-v2/01-平台共享契约.md)（失败一律走信封）

## 参考

- [D-090](../决策日志.md)（D-a 协议端口 + T1 先行 / D-a′ 能力诚实性）· [D-091](../决策日志.md)（契约落点 ⑤）
- 调研：[Agent 自主能力调研报告](../../docs/Agent自主能力调研报告.md) §8.7
- 下游：[`T-AGT-002`](T-AGT-002-端点能力启动探测.md) · [`T-AGT-004.2`](T-AGT-004.2-循环驱动、协议端口与上界.md)
