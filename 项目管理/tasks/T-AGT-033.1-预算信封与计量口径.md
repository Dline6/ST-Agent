---
id: T-AGT-033.1
parent: T-AGT-033
title: 预算信封与计量口径
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M11
depends_on: [T-AGT-009.3, T-L0-003]
status: todo
decisions: [D-090, D-142]
verify:
---

# T-AGT-033.1 · 预算信封与计量口径

## 目标

每次运行只算一个 `ContextEnvelope`：以端点真实窗口 `W` 为唯一硬边界，扣除输出预留与工具载荷后得到可用预算；统一字符/token 口径（中文用保守比例或 provider tokenizer），端点上限检查把 `tools` 计入；会话层与工作层共用同一份计量并都产出 `dropped`；记忆配额按实际命中动态分配，不再固定 50%。不再向用户暴露“建议不超过 32000”这类与端点窗口无关的数字。

## 验收标准（Given-When-Then）

- GWT-1：Given 端点 `max_context_tokens=128000`，Then 运行时派生 `ContextEnvelope`（窗口 × 安全比例 − 输出预留 − `tokens(tools)`）；用户界面不再出现与窗口脱节的固定“建议上限”。
- GWT-2：Given 中文 prompt，Then token 估算使用保守比例或 provider tokenizer；端点上限检查**同时计入 `tools`**；超限时返回显式 `context_overflow`（带续跑入口），不落到 `llm_failed`。
- GWT-3：Given 会话层 `assemble_context` 因预算舍弃消息，Then 被舍项进入 `dropped` 并一路带到 `agent_run` 留痕；不再出现“会话层丢了、报告显示空”的双层不一致。
- GWT-4：Given 记忆为空或命中少于配额，Then 未用配额回收给历史/步骤；不再固定 50% 浪费（对应记忆机制复盘第 ⑨ 条）。**切片自身的计量/截断归 [`T-MEM-001.5`](T-MEM-001.5-切片计量与截断策略.md)**；本叶只管配额分配与回收。
- GWT-5：Given 端点能力未知，Then 用保守 fallback 并显式标注；删除 `4000` / `16000` 两个漂移缺省，改由同一函数派生。
- GWT-6：Given 两次相同输入与相同端点，Then 信封与分配逐字节一致（时钟/随机不参与）。

## 接口面

待 ④ 对齐实填。预计落点：

- 输入：`EndpointCapability.max_context_tokens`（[`models.py`](../../src/st_agent/l0/llm/models.py)）· `estimate_tokens`（[`models.py`](../../src/st_agent/l0/llm/models.py)）· `LlmClient.invoke` 的上限检查（[`client.py`](../../src/st_agent/l0/llm/client.py)）· `assemble_context`（[`session.py`](../../src/st_agent/l3/chat/session.py)）· `assemble_work_context`（[`context.py`](../../src/st_agent/l3/runtime/context.py)）· `LoopBounds`（[`bounds.py`](../../src/st_agent/l3/runtime/bounds.py)）· `AgentRunRecord.context_dropped`（[`report.py`](../../src/st_agent/l3/runtime/report.py)）。
- 输出：`ContextEnvelope`（新）· 统一 token 估算/端点上限检查 · 会话层 `dropped` · 动态记忆配额 · 删除漂移缺省。

## 可关闭的遗留

无（新建任务；④ 对齐时逐册复核）。

## 假设与前提

- **A1**：端点窗口是唯一可靠硬边界；提供方 tokenizer 不可得时用“中文 ≥1 字符/token”的保守比例，宁可提前压缩也不把超限请求发出去。
- **A2**：`ContextEnvelope` 是运行期派生值，不作为用户可编辑配置；用户只配置“总 token/费用/时间”硬上限与“单次调用最长多久”。
- **A3**：会话层 `dropped` 字段为新增可选字段，旧会话/旧留痕按空处理，向后兼容。
- **A4**：记忆切片自身的计量（是否含 `edges`）与截断策略归 [`T-MEM-001.5`](T-MEM-001.5-切片计量与截断策略.md)；本叶不重复实现。

## 涉及契约（链接到具体小节）

- [01-平台共享契约](../../docs/技术架构-v2/01-平台共享契约.md) §3 用量归属 · §7 配置项 · §8 时区口径
- [02-L0-本地优先基座](../../docs/技术架构-v2/02-L0-本地优先基座.md) §4 LLM 调用面
- [05-L3-对话主入口](../../docs/技术架构-v2/05-L3-对话主入口.md) §10 规模预算与超限取舍

## 备注

证据：`estimate_tokens` 为 4 字符/token（`models.py:80`）；`client.py:196` 只检查 `prompt`，未计 `tools`；`SessionContext` 无 `dropped`；`assemble_context` 固定 50% 记忆配额（`session.py:539`）；`DEFAULT_CONTEXT_BUDGET=4000` 与 `DEFAULT_WORK_CONTEXT_BUDGET=16000` 两个缺省已漂移。
