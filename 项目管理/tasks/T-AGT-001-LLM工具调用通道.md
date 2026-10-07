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
status: done
decisions: [D-090, D-091, D-093]
verify: 全量 pytest **3593 passed / 0 failed / 14 deselected（live）/ 730.91s**（跨层改动，基线核账 3593 − 新增 34 = 3559 ✓）；范围套件（contracts ∪ layering ∪ tools ∪ integration ∪ l0）**1202 passed（339.67s）**；**live 子集 2 passed**（本批强制项——动了 `l0/llm/**` 与 `l0/net/gateway.py`）；**真实端点工具面探针**（一次性、不入库）真回 `tool_call`（`qwen3.8-flash` · `sk_probe_echo`，GWT-2/3 真网络证据）；新增 34 例；`verify_docs --strict` 检查 1–9 全 0 · 见 [执行日志](../执行日志.md) `[T-AGT-001]`
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

- **输入**（上游全部已交付，无「待交付」项）：
  - [`LlmClient.invoke`](../../src/st_agent/l0/llm/client.py) 与 [`_stream`](../../src/st_agent/l0/llm/client.py)（既有签名；全仓 5 个调用点全为单轮纯文本：L4 视角合成 / 证据重研判、L3 意图理解、L6 两处）· [`StreamEvent`](../../src/st_agent/l0/llm/models.py) / [`EndpointCapability`](../../src/st_agent/l0/llm/models.py) / [`CapabilityRequirement`](../../src/st_agent/l0/llm/models.py) · [`EndpointRegistry.negotiate`](../../src/st_agent/l0/llm/registry.py) · [`http_transport`](../../src/st_agent/l0/llm/http_transport.py) 的按次 sender 工厂闭包 · [`EgressGateway.llm_transport` / `stream`](../../src/st_agent/l0/net/gateway.py)（transport 与流式的**唯一装配跳**）。
  - 契约口径：[02 §4 LLM 调用抽象](../../docs/技术架构-v2/02-L0-本地优先基座.md)（接口能力表「工具调用」行 + 能力诚实性五条）· [01 §2 工具暴露面的唯一来源](../../docs/技术架构-v2/01-平台共享契约.md) · [01 §5 ResultEnvelope](../../docs/技术架构-v2/01-平台共享契约.md)。
- **输出**（**跨任务交接点**——`T-AGT-002` / `T-AGT-004.2` 直接消费，形态定下即不宜轻改）：
  - `st_agent.l0.llm.ToolSpec`——**入参唯一形态**：`name` / `description` / `parameters`（JSON Schema **object**，01 §2 方言）。
  - `st_agent.l0.llm.ToolCall`——**出参唯一形态**：`call_id` / `name` / `arguments`（**已解析的对象**，非增量串）。
  - `StreamEvent` 新态 `tool_call`：`kind ∈ chunk | tool_call | done | error`，`tool_call: ToolCall | None`；`chunk` 仍只载文本增量（**工具调用增量不得呈现为文本**）。
  - `EndpointCapability.supports_function_calling`（缺省 `False`）+ `CapabilityRequirement.needs_function_calling`（缺省 `False`）；`EndpointRegistry.negotiate` 增一维：需求要工具调用而端点未声明 ⇒ 判拒（能力协商失败）。
  - `LlmClient.invoke(..., tools: Sequence[ToolSpec | Mapping] | None = None)`；`tools` 非空即**自动附** `needs_function_calling=True`，不静默发包。
  - `Transport` 与 sender 工厂签名各多一个 `tools` 形参（落 `src/st_agent/l0/llm/**` 与 [`l0/net/gateway.py`](../../src/st_agent/l0/net/gateway.py)）；流式项由 `str` 扩为 `str | ToolCall`（网关按其规范化字节计入 `bytes_in`）。**既有 4 参 transport 替身须同批补参**：`tests/l0/test_llm.py` · `tests/l1/test_runtime_llm.py` · `tests/l3/test_intent_protocol.py`。
  - 测试：`tests/l0/test_llm_tool_calls.py`（GWT-1..5）。

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
- **A4 · 工具名的字母表以平台 `base` 形态为准，不按 OpenAI 的严格字母表收紧**——前提：[01 §2](../../docs/技术架构-v2/01-平台共享契约.md) 定「工具名＝`skill_id` 的 **base**」，而 base 形态是 `^sk_[a-z0-9][a-z0-9_.\-]{0,60}$`（**含 `.`**）；OpenAI 对 `function.name` 的约束是 `^[a-zA-Z0-9_-]{1,64}$`（**不含 `.`**）。L0 若按后者收紧，含 `.` 的合法 base 会在本地被拒 ⇒ [01 §2](../../docs/技术架构-v2/01-平台共享契约.md) 不可实现。故 L0 取 **平台 base 的字母表 + ≤64 长度**，不额外收紧。
  **若错的影响**：某些严格端点会拒含 `.` 的工具名——错误呈现在**提供方侧**而非本地 fail-closed；返工面 = `ToolSpec.name` 的校验件，以及 [`T-AGT-003`](T-AGT-003-工具目录读面.md) 投影侧是否须改名（那会动 [01 §2](../../docs/技术架构-v2/01-平台共享契约.md) 的「工具名＝base」口径）。
  **验证方式**：GWT-2 以含 `_` / `-` / `.` 的 base 形态样例钉住「合法 base 一律通过」；**提供方侧的严格性不在本任务可证范围内**（本机 live 端点是否为严格实现不构成判据），故据实留档给下游。
- **A5 · 审计字节口径不变（仍为 prompt / 文本块口径）**——前提：既有 [`llm_transport`](../../src/st_agent/l0/net/gateway.py) 的 `bytes_out=len(prompt.encode())` 与 [`stream`](../../src/st_agent/l0/net/gateway.py) 的逐块 `len(piece.encode())` 是 **prompt / 文本块**口径（非整请求体字节——这是**既有**近似，非本任务新增）。本任务只把**返回项**扩为「文本块 ∪ 工具调用」（工具调用按其规范序列化计字节），**不改** `bytes_out` 的口径。
  **若错的影响**：带大 `tools` 时 `bytes_out` 低估。——**注意**：既有实现本就低估（未计 `model` / `messages` / `stream` 包裹），故本任务**不新增失真**；若要求 `bytes_out` 反映真实请求体，返工面 = 网关 `_transport` 一处（可另立任务）。
  **验证方式**：GWT-1 以「无 `tools` 时既有审计断言逐字节不变」钉住不回归；新用例断言工具调用项计入 `bytes_in`。

## 涉及契约

- [02 §4 LLM 调用抽象](../../docs/技术架构-v2/02-L0-本地优先基座.md)（工具调用一行 + 能力诚实性五条）
- [01 §2](../../docs/技术架构-v2/01-平台共享契约.md)（工具条目的唯一来源）
- [01 §5 ResultEnvelope](../../docs/技术架构-v2/01-平台共享契约.md)（失败一律走信封）

## 参考

- [D-090](../决策日志.md)（D-a 协议端口 + T1 先行 / D-a′ 能力诚实性）· [D-091](../决策日志.md)（契约落点 ⑤）· [D-093](../决策日志.md)（本批落地形态与五处口径）
- 调研：[Agent 自主能力调研报告](../../docs/Agent自主能力调研报告.md) §8.7
- 下游：[`T-AGT-002`](T-AGT-002-端点能力启动探测.md) · [`T-AGT-004.2`](T-AGT-004.2-循环驱动、协议端口与上界.md)
