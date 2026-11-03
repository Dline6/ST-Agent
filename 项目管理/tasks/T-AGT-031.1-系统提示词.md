---
id: T-AGT-031.1
parent: T-AGT-031
title: 系统提示词
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M11
depends_on: [T-AGT-004.2, T-AGT-029.1]
status: done
decisions: [D-090, D-154, D-165]
verify: 见执行日志 [T-AGT-031.1]；范围套件 3019 passed / 2 环境性 errors；live 3 passed；verify_docs PASS；commit 362fc76
---

# T-AGT-031.1 · 系统提示词

## 目标

为每轮 LLM 请求注入**确定性系统提示词**：承载角色中性、输出契约、当前日期/时区、数据时效规则、歧义处理、收敛条件与工具使用规则。**任务锚定（会话主任务、跟进语解析、历史装载）不在此叶**，见 [`T-AGT-033.3`](T-AGT-033.3-任务锚点与历史装载.md)。

## 验收标准（Given-When-Then）

- GWT-1：Given 一次循环调用，Then 请求体含 `system` 消息，内容至少覆盖：中性/不拟人/不构成投资建议、用工具取证并以工具结果与 `fetched_at` 为依据、歧义先声明假设或提问、何时结束并给出结论、当前日期与用户时区、本地数据 `as_of` 与时效提示、截断结果按运行时给出的分片规则回读。同一输入两次装配逐字节一致（时钟由入参注入）。
- GWT-2：Given 系统提示词文本，Then 不含人名、性格、第一人称、情感表达或投资建议措辞；相关断言有测试钉住。
- GWT-3：Given 端点不支持 `system` 角色，Then 显式降级为确定性前缀并入 user 消息并标注，不静默丢弃。

## 接口面

- **输入**：
  - 时钟/时区：组合根 [`composition/agent.py`](../../src/st_agent/composition/agent.py) 的 `now`（已是 `datetime` 带时区；缺省本机当前时刻），按 [01 §8](../../docs/技术架构-v2/01-平台共享契约.md) 只传给装配件，循环本体不读时钟。
  - 上下文装配：[`assemble_work_context`](../../src/st_agent/l3/runtime/context.py) 新增 `system_prompt: str = ""` 与 `system_prompt_version: str = ""` 透传；`run_agent_loop` 新增同名参数。
  - 协议端口：[`NativeToolCallProtocol.invoke_turn`](../../src/st_agent/l3/runtime/protocol.py) 把 `WorkContext.system_prompt` 交给 L0；端点能力档 `EndpointCapability.supports_system_role`（新，缺省 `True`）决定是否降级。
  - L0 请求体：[`http_transport.py`](../../src/st_agent/l0/llm/http_transport.py) 的 `messages` 目前只发单条 user；`LlmClient.invoke` / `_stream` / transport 增加 `system_prompt: str | None` 透传。
- **输出**：
  - 新模块 [`l3/runtime/system_prompt.py`](../../src/st_agent/l3/runtime/system_prompt.py)：`SYSTEM_PROMPT_VERSION` 与 `build_system_prompt(*, now) -> str`（纯函数，确定性）。
  - `WorkContext.system_prompt` / `system_prompt_version`；L0 请求体在非空时发 `[{"role":"system"},{"role":"user"}]`，为空时逐字节同现状。
  - 端点不支持 `system` 角色时，`NativeToolCallProtocol` 把同一份文本以**确定性前缀**并入 user 消息并标注来源，不静默丢弃。
  - `LlmUsageRecord.system_prompt_version` / `system_prompt_chars`（只记版本与长度，不落明文）。

## 可关闭的遗留

无。④ 对齐时逐册复核 [L0](../遗留问题/L0-遗留问题.md) / [L1](../遗留问题/L1-遗留问题.md) / [L2](../遗留问题/L2-遗留问题.md) / [L3](../遗留问题/L3-遗留问题.md) / [L5](../遗留问题/L5-遗留问题.md) / [L6](../遗留问题/L6-遗留问题.md)：未闭项仅 L0 的 `C3` / `C5` / `D1`–`D3`（均人决）与 L5 的 `A1`（真实渠道凭据），归属均非本任务。

## 假设与前提

- **A1**：当前 OpenAI 兼容端点支持 `system` 角色；若错，按 GWT-3 降级为确定性前缀，并保留同一份文本来源。
- **A2**：当前日期/时区由调用方注入（测试固定时钟），系统提示词本身保持逐字节确定性；若错，日期必须进留痕并接受非确定性。
- **A3**：系统提示词不进审计、不落盘明文；只记长度与版本号。
- **A4**：降级判断取端点能力档的新字段 `supports_system_role`（缺省 `True`）；若存量端点实际不支持却被判为支持，请求会显式失败而不是静默丢失提示词——届时把该端点字段改为 `False` 即走前缀降级。
- **A5**：系统提示词计入 `prompt_tokens` 估算与工作上下文规模口径（`size_chars`）；无系统提示词时数值与现状逐字节一致。

## 涉及契约（链接到具体小节）

- [01-平台共享契约](../../docs/技术架构-v2/01-平台共享契约.md) §5 结果与日志 · §8 时区口径
- [02-L0-本地优先基座](../../docs/技术架构-v2/02-L0-本地优先基座.md) §4 LLM 调用面
- [05-L3-对话主入口](../../docs/技术架构-v2/05-L3-对话主入口.md) §10 自主查证循环
- [03-goals](../../docs/PRD-v2-Agent/03-goals.md) 中性输出口径

## 备注

证据：全仓当前无 `system` 角色（`http_transport.py:159` 只发单条 user 消息），`render_prompt()` 无角色/输出契约/当前日期。任务锚定部分已迁至 [`T-AGT-033.3`](T-AGT-033.3-任务锚点与历史装载.md)（「继续」作为唯一任务、原问题被历史预算挤出等问题归该叶）。
