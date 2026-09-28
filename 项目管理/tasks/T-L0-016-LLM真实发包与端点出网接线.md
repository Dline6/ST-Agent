---
id: T-L0-016
parent: null
title: LLM 真实发包与端点出网接线（按次 sender + OpenAI 兼容流式发送器）
story: ../../docs/PRD-v2-Agent/story-05-local-first.md
arch: ../../docs/技术架构-v2/02-L0-本地优先基座.md
arch_link: "[02 §4/§6](../../docs/技术架构-v2/02-L0-本地优先基座.md)"
priority: P0
milestone: M1
depends_on: [T-L0-003, T-L0-004]
status: done
decisions: [D-058]
verify: 2026-09-28 `[T-L0-016]` · 范围 `pytest tests/contracts tests/test_layering.py tests/test_tools.py tests/integration tests/l0` 750 passed / 0 failed（本叶 +25）· live 真实端点 1 passed · `verify_docs --strict` 检查 1–9 全过 · **红-绿已验**：`sender=per_call` 改回 `sender=None`（修复前形态）→ `test_llm_http_transport.py` 9 条 FAIL（prompt 到不了发送器），恢复后全绿
---

# T-L0-016 · LLM 真实发包与端点出网接线

## 目标

让 LLM 调用**真正出网**：补上 `EgressGateway.stream` 缺失的**按次 sender** 通道（现只有 `execute` 有），交付 OpenAI 兼容的**流式真实发送器**，并把 `llm_transport` 接线成「prompt/key 交发送器、网关只审计字节数」的完整路径——`client.py:243` 的 `transport is None → unavailable` 降级自此只在「未配置 / 离线」时发生。

## 验收标准（Given-When-Then，从 Story 抄）

- GWT-1（Story 05「用户配置云端 LLM API Key → 调用 LLM」）：Given 端点已在 `EndpointRegistry` 登记、凭据已在 `CredentialVault`，When 经 `LlmClient.invoke` + `EgressGateway.llm_transport(...)` 发起一次调用，Then **真实发送器收到 prompt 与 key**、产出 `chunk* → done`；审计留一条 `kind=llm_call` 的 `ok`（只含字节数与目的）。
- GWT-2：Given 提供方按 OpenAI 兼容协议流式返回，When 逐块读取，Then 逐块产出文本并以 `[DONE]`/流结束终止；连接失败 / 超时分别映射 `TransportUnavailableError` / `TransportTimeoutError`（T-L0-003 异常体系），**不假装成功**。
- GWT-3（Story 05「请求直接发到 LLM 提供方（不经过产品方服务器）+ 请求内容不留存」）：Given 一次成功调用，When 检视落盘与审计导出，Then 目标主机＝该 provider 登记的主机、prompt / response / key **盘上明文 0 命中**、审计无内容字段。
- GWT-4：Given 端点的 provider 未登记目标主机，When 调用，Then `unavailable` 信封，且**不触碰网络**。
- GWT-5：Given `set_online(False)`，When 调用，Then `unavailable` + `pending_reconnect` 留痕（沿用 T-L0-004 语义，不另造一套）。

## 接口面

- **输入（消费的前置接口）**：
  - `T-L0-004` —— `EgressGateway.execute/stream`（`src/st_agent/l0/net/gateway.py`；审计落 `execution_log/net/**`）、`Egress*` 异常体系、`Sender` 协议 `(kind, target_host, timeout_ms) -> (bytes_out, bytes_in, chunks)`
  - `T-L0-003` —— `LlmClient.invoke`（`src/st_agent/l0/llm/client.py`）、`Transport` 协议签名 `(endpoint, prompt, key, timeout_ms) -> Iterable[str]`、`Transport*` 异常体系、`LlmEndpoint`（`provider` / `credential_id` / `capability`；`src/st_agent/l0/llm/models.py`）
  - `T-L0-002` —— `CredentialVault.use`（key 的唯一取用口，**由 `LlmClient` 调用**，本任务不直接取用）
- **输出（本任务交付的公共 API / 落盘位置）**：
  - `EgressGateway.stream(..., sender=None)` 新增**按次 sender** 形参（与 `execute` 对称）；`SandboxedGateway.stream` 经既有 `**kwargs` 透传（`src/st_agent/l1/sandbox/sandbox.py`，签名不变）
  - 新模块 `src/st_agent/l0/llm/http_transport.py`：`openai_compat_transport(...)` —— 签名与 `Transport` 对齐，内部按次构造网关 sender 闭包（**携带 prompt/key**，网关侧仍只见字节数）
  - `EgressGateway.llm_transport(provider_hosts)`（`gateway.py:412`）**修实**：把 prompt 交给按次 sender（现仅换算字节数即丢弃）
  - 落盘位置：**无新增分区**，沿用 `execution_log/net/<kind>/` 审计

## 可关闭的遗留

无（`T-L0-016` 新建，各层遗留册未闭区无归属本任务的条目——L0 册 `A1b`/`A4`/`A5`/`C1`/`C2`/`D1`–`D3`、L1 册 `D2`、L2 册 `B1`/`C1`、L3 册 `A1` 均与 LLM 发包无关；本次亦不新增遗留）

## 假设与前提

- **A1**：真实提供方走 OpenAI 兼容 `POST {base}/chat/completions`（含 Ollama 的 `/v1` 形态），流式为 SSE 逐行 `data: {...}`。
  - 若错的影响：需按提供方另写协议适配器；返工面＝本任务的传输实现（接口面 `Transport` 签名不变，故下游 T-L1-011 不受影响）。
  - 验证方式：`tests/live/` 加 `@pytest.mark.live` 用例，对本机已配端点实跑一次（默认排除，不进 CI）。
- **A2**：网关「只记字节数」的审计纪律**允许**在按次 sender 内携带 prompt/key——信封在网关之外组装，网关签名不变。
  - 若错的影响：若架构要求网关见到内容，则须改 [02 §6](../../docs/技术架构-v2/02-L0-本地优先基座.md) 的审计纪律，并波及 T-L0-004 的全部用例。
  - 验证方式：本任务不得使 `tests/l0/test_net.py::TestGwt4LlmAdapter::test_no_plaintext_in_audit_or_disk` FAIL；实现后复查 02 §6 措辞与代码一致。
- **A3**：`.env` **不在本层读取**（装载归 `T-L1-011`）。
  - 若错的影响：两端职责重叠，出现两处装载实现。
  - 验证方式：`T-L1-011` 的接口面「输入」列不得出现本任务的读取动作。

## 涉及契约（链接到具体小节）

- [02 §4 LLM 调用抽象](../../docs/技术架构-v2/02-L0-本地优先基座.md) —— 零中转 / 流式返回 / 失败语义 / 用量报告
- [02 §6 出网审计](../../docs/技术架构-v2/02-L0-本地优先基座.md) —— 唯一出口、审计字段、「网关签名只有字节数」的边界
- [01 §5 ResultEnvelope](../../docs/技术架构-v2/01-平台共享契约.md) —— 失败语义的载体

## 参考

- Story（What）：[story-05-local-first](../../docs/PRD-v2-Agent/story-05-local-first.md)（LLM 调用主权段 + 网络活动透明化段）
- 被消费方：[T-L1-011](T-L1-011-LLM端点与会话装配接线.md)

## 备注

02 §6 的「按次 sender 通道」为**结构性补齐**（`execute` 早有、`stream` 独缺），故本任务含一处层文档写实，按铁律 8 先文档后代码。`SandboxedGateway.llm_transport`（T-L1-001.5 的 A4 结构性 API）**不由本任务消费**——技能侧越界由 `GuardedLlmClient` 在 runner 边界拦截（见 T-L1-011 假设 A2）。
