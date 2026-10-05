---
id: T-L1-011
parent: null
title: LLM 端点与会话装配接线（组合根注入 transport + provider_hosts 播种 + .env 引导装载）
story: ../../docs/PRD-v2-Agent/story-05-local-first.md
arch: ../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md
arch_link: "[03 §1](../../../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)"
priority: P0
milestone: M1
depends_on: [T-L0-002, T-L0-003, T-L0-016, T-L1-006]
status: done
decisions: [D-058]
verify: 2026-09-28 `[T-L1-011]` · 全量 `pytest` 1787 passed / 7 deselected（live）/ 0 failed（本叶 +19）· live 真实端点 2 passed（含「组合根按 .env 装载并真调通」）· `verify_docs --strict` 检查 1–9 全过 · **红-绿已验**：`_bootstrap_llm_transport` 改 `return None`（不接线）→ `test_call_completes_and_is_audited` / `test_in_scope_endpoint_passes_the_guard` FAIL（退化为 unavailable），恢复后全绿 · **连带**：`tests/l1/test_runtime_l0.py` 与 `tests/integration/rig.py` 各补 `llm_env={}` 以保确定性
---

# T-L1-011 · LLM 端点与会话装配接线

## 目标

让组合根装配出的运行时**默认就是能真实调用 LLM 的**：`build_l1_runtime` / `open_runtime` 装配 `LlmClient` 时注入真实 transport（`T-L0-016` 的发送器 + `provider → host` 映射），并由 `.env` 引导装载端点与凭据——使「`.env` 已填值却无人读、调用恒 `unavailable`」这一现状终结。

## 验收标准（Given-When-Then，从 Story 抄）

- GWT-1（Story 05「配置云端 LLM API Key」）：Given 本机 `.env` 已填 `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`，When `open_runtime(root, passphrase, create=...)` 装配运行时，Then 端点已在 `EndpointRegistry` 登记、凭据已入 `secrets` 分区（界面掩码可查、有使用记录），且 `LlmClient` 的 transport **非 None**。
- GWT-2：Given 装配完成的运行时，When 发起一次 LLM 调用（runner 路径或上层注入的 `LlmClient`），Then 端到端得 `chunk* → done`，审计留 `kind=llm_call` 记录。
- GWT-3：Given 技能执行期发起 LLM，When 该端点的目标主机不在会话 `net_access` 范围，Then 由 `GuardedLlmClient` 拦为 `validation_failed`——**不因组合根接线而放宽沙箱**（`T-L1-001.5` 的 GWT 不回退）。
- GWT-4：Given `.env` 缺失或三键有空值，When 装配，Then **不抛错崩溃**，运行时 LLM 调用仍走 `unavailable`（fail-closed，不臆测端点）。

## 接口面

- **输入（消费的前置接口）**：
  - `T-L0-016` —— 真实 `Transport`（`src/st_agent/l0/llm/http_transport.py` 的 `openai_compat_transport(...)`）+ 修实后的 `EgressGateway.llm_transport(provider_hosts)`（`src/st_agent/l0/net/gateway.py:412`）
  - `T-L1-006` —— `build_l1_runtime(...)` / `open_runtime(...)`（`src/st_agent/l1/runtime.py` 的既有装配面；当前 `transport` 形参缺省 `None`）
  - `T-L0-003` —— `EndpointRegistry.register`、`LlmClient`（`src/st_agent/l0/llm/registry.py` / `client.py`）
  - `T-L0-002` —— `CredentialVault.add`（凭据入 `secrets` 分区；`src/st_agent/l0/secrets/vault.py`）
  - `T-L1-001.3` —— `ProviderHostRegistry.register`（`provider → host` 记录，落 `config` 分区；`src/st_agent/l1/sandbox/provider_hosts.py`）
- **输出（本任务交付的公共 API / 落盘位置）**：
  - `src/st_agent/l1/runtime.py` 改实：`build_l1_runtime(...)` 默认注入 `gateway.llm_transport(provider_hosts)`；新增 `.env` 引导装载入口（端点登记 + 凭据入库 + `provider → host` 播种），`open_runtime(...)` 接入
  - 落盘位置：端点落 `config` 分区（`EndpointRegistry` 既有形态）、凭据落 `secrets` 分区（`T-L0-002` 既有形态）、`provider → host` 落 `config/llm-provider-host/`（`T-L1-006` 定案口径）；**无新增分区**

## 可关闭的遗留

无（`T-L1-011` 新建，各层遗留册未闭区无归属本任务的条目——L0 册 `A1b`→`T-ECO-002`、`A4`→`T-ECO-001`、`A5`→L5、`C1`→`T-L0-007.2`、`C2`/`D1`–`D3`→待立项或人；L1 册 `D2`→L3 双通道与 `T-ECO-002`；L2 册 `B1`→`T-L3-005`、`C1`→待人立项；L3 册 `A1`→待人立项。均与 LLM 装配无关；本次亦不新增遗留）

## 假设与前提

- **A1**：`.env` 引导装载落组合根（L1 `runtime.py`），且**不改产品面文档**——它是开发 / 自用通道；产品正典通道是设置页（`T-L3-003`/`T-L3-004` 双通道），本任务不注册 [01 §7](../../../../docs/技术架构-v2/01-平台共享契约.md) 配置注册表条目。
  - 若错的影响：若项目认为引导应另立 app / 引导层，则返工面＝装载入口的归属与 `build_l1_runtime` 签名。
  - 验证方式：`T-L3-003` 开工时复查端点的配置通道是否与本任务重叠。
- **A2**：组合根注入的是**普通网关**的 `llm_transport`（非 `SandboxedGateway.llm_transport`）——技能侧越界由 runner 的 `GuardedLlmClient`（`sandbox.py:438`，按会话 `net_access` 核对）在边界拦截，故 `LlmClient` 层无需再套一层沙箱。
  - 若错的影响：若技能侧实际不经 `GuardedLlmClient`，则沙箱约束失效，波及 `T-L1-001.5` 的 GWT。
  - 验证方式：GWT-3 用例（越界端点 → `validation_failed`）在装配后的运行时上成立。
- **A3**：`.env` 三键足以表达一个可用端点（`LLM_BASE_URL` → provider 主机、`LLM_MODEL` → 模型名、`LLM_API_KEY` → 凭据值）。
  - 若错的影响：若端点形态超出（多端点 / 优先级 / 本地推理并存，[02 §4](../../../../docs/技术架构-v2/02-L0-本地优先基座.md)「多端点并存」），需扩展为显式端点配置文件；返工面＝装载入口。
  - 验证方式：单端点实跑通过（GWT-2）；多端点留待设置页任务。

## 涉及契约（链接到具体小节）

- [02 §4 LLM 调用抽象](../../../../docs/技术架构-v2/02-L0-本地优先基座.md) —— 端点配置与「多端点并存」的语义
- [02 §3 密钥与凭据子系统](../../../../docs/技术架构-v2/02-L0-本地优先基座.md) —— Key 落 `secrets`、掩码显示、使用记录
- [03 §1 L1 运行时](../../../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md) —— 组合根装配面
- [01 §7 配置元模型](../../../../docs/技术架构-v2/01-平台共享契约.md) —— 本任务**不**登记（见 A1），仅作后续设置页任务的落点备注

## 参考

- Story（What）：[story-05-local-first](../../../../docs/PRD-v2-Agent/story-05-local-first.md)（LLM 调用主权段）
- 前置：[T-L0-016](T-L0-016-LLM真实发包与端点出网接线.md)（本任务消费其 transport）
- 装配面：[T-L1-006](../M0/T-L1-006-L1运行时装配组合根.md)（组合根）

## 备注

本任务只接**到 L1 组合根**为止：L3 对话面（`IntentProtocol` / `DispatchBus` / `LlmIntentUnderstander`）的装配不在范围内——那属对话主入口的装配，归 [T-INT-002](T-INT-002-M1集成关卡首次可对话.md) 收口（其 `depends_on` 已含本任务）。`.env` 为**不入库**文件（`.gitignore`），故测试不得依赖真实 `.env`：装载入口须可注入值（`env=` 形参或等价物），用例以注入值驱动。
