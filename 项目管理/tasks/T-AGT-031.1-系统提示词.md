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
status: todo
decisions: [D-090, D-154]
verify:
---

# T-AGT-031.1 · 系统提示词

## 目标

为每轮 LLM 请求注入**确定性系统提示词**：承载角色中性、输出契约、当前日期/时区、数据时效规则、歧义处理、收敛条件与工具使用规则。**任务锚定（会话主任务、跟进语解析、历史装载）不在此叶**，见 [`T-AGT-033.3`](T-AGT-033.3-任务锚点与历史装载.md)。

## 验收标准（Given-When-Then）

- GWT-1：Given 一次循环调用，Then 请求体含 `system` 消息，内容至少覆盖：中性/不拟人/不构成投资建议、用工具取证并以工具结果与 `fetched_at` 为依据、歧义先声明假设或提问、何时结束并给出结论、当前日期与用户时区、本地数据 `as_of` 与时效提示、截断结果按运行时给出的分片规则回读。同一输入两次装配逐字节一致（时钟由入参注入）。
- GWT-2：Given 系统提示词文本，Then 不含人名、性格、第一人称、情感表达或投资建议措辞；相关断言有测试钉住。
- GWT-3：Given 端点不支持 `system` 角色，Then 显式降级为确定性前缀并入 user 消息并标注，不静默丢弃。

## 接口面

待 ④ 对齐实填。预计落点：

- 输入：`WorkContext.render_prompt`（[`context.py`](../../src/st_agent/l3/runtime/context.py)）· `NativeToolCallProtocol.invoke_turn`（[`protocol.py`](../../src/st_agent/l3/runtime/protocol.py)）· 请求体组装（[`http_transport.py`](../../src/st_agent/l0/llm/http_transport.py)）· 时钟/时区口径（[01 §8](../../docs/技术架构-v2/01-平台共享契约.md)）。
- 输出：L3 系统提示词装配件（新）· `WorkContext.system_prompt` · L0 `messages` 支持 `system` 角色 · 提示词版本/长度留痕（不落明文）。

## 可关闭的遗留

无（新建任务；④ 对齐时逐册复核）。

## 假设与前提

- **A1**：当前 OpenAI 兼容端点支持 `system` 角色；若错，按 GWT-3 降级为确定性前缀，并保留同一份文本来源。
- **A2**：当前日期/时区由调用方注入（测试固定时钟），系统提示词本身保持逐字节确定性；若错，日期必须进留痕并接受非确定性。
- **A3**：系统提示词不进审计、不落盘明文；只记长度与版本号。

## 涉及契约（链接到具体小节）

- [01-平台共享契约](../../docs/技术架构-v2/01-平台共享契约.md) §5 结果与日志 · §8 时区口径
- [02-L0-本地优先基座](../../docs/技术架构-v2/02-L0-本地优先基座.md) §4 LLM 调用面
- [05-L3-对话主入口](../../docs/技术架构-v2/05-L3-对话主入口.md) §10 自主查证循环
- [03-goals](../../docs/PRD-v2-Agent/03-goals.md) 中性输出口径

## 备注

证据：全仓当前无 `system` 角色（`http_transport.py:159` 只发单条 user 消息），`render_prompt()` 无角色/输出契约/当前日期。任务锚定部分已迁至 [`T-AGT-033.3`](T-AGT-033.3-任务锚点与历史装载.md)（「继续」作为唯一任务、原问题被历史预算挤出等问题归该叶）。
