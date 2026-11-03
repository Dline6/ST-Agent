---
id: T-AGT-031
parent: null
title: 真链路核心稳定性
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M11
depends_on: [T-AGT-004.2, T-AGT-029.1, T-L0-003, T-AGT-011.3]
status: done
decisions: [D-090, D-154, D-165]
verify:
---

# T-AGT-031 · 真链路核心稳定性

> **分组节点**：本节点不直接交付，交付由 [`T-AGT-031.1`](T-AGT-031.1-系统提示词.md) / [`T-AGT-031.2`](T-AGT-031.2-LLM超时语义与可配置.md) / [`T-AGT-031.3`](T-AGT-031.3-LLM失败重试与可续跑.md) 承接；父状态与父 `depends_on` 由 `render_ledger.py render` 按 children 派生。**截断/回读**已迁至 [`T-AGT-033.5`](T-AGT-033.5-截断回读与预览派生.md)，**任务锚定/历史装载**已迁至 [`T-AGT-033.3`](T-AGT-033.3-任务锚点与历史装载.md)。

## 目标

修复 2026-11-03 真链路实验（会话 `sess_edf8e2ae9db74c418be0`，主运行 `agr_ec73267803164d28a851`）暴露的三个 P0 阻塞：无系统提示词、LLM 调用超时语义错误且不可配置、失败不重试且 `llm_failed` 不可续跑。上下文装载/任务锚定/截断回读的结构性问题由 [`T-AGT-033`](T-AGT-033-上下文装载机制重整.md) 承接。验收以“同一条真实任务能稳定跑完并给出带来源的结论”为准，不以单测注入替身为准。

## 验收标准（Given-When-Then）

- GWT-1：由 `.1` / `.2` / `.3` 合成，父节点不单独验收。

## 接口面

子叶并集（详见各叶）：

- **输入**：[`context.py`](../../../../src/st_agent/l3/runtime/context.py) 的工作上下文装配 · [`protocol.py`](../../../../src/st_agent/l3/runtime/protocol.py) 的 `NativeToolCallProtocol.invoke_turn` · [`client.py`](../../../../src/st_agent/l0/llm/client.py) 的流式调用与超时 · [`continuation.py`](../../../../src/st_agent/l3/runtime/continuation.py) 的续跑终止集合 · [`facades/chat.py`](../../../../src/st_agent/composition/facades/chat.py) 的入口与续跑载荷。
- **输出**：系统提示词装配件 · `invoke_turn(timeout_ms=…)` 与超时配置项 · 有界重试与 `llm_failed` 续跑路径。

## 可关闭的遗留

无（新建任务；④ 对齐时逐册复核 L0 / L1 / L2 / L3 / L5 / L6 未闭区）。

## 假设与前提

子叶并集，详见各叶 A 编号。

## 涉及契约（链接到具体小节）

- [01-平台共享契约](../../../../docs/技术架构-v2/01-平台共享契约.md) §4 LLM 能力与工具调用 · §7 配置项
- [02-L0-本地优先基座](../../../../docs/技术架构-v2/02-L0-本地优先基座.md) §4 LLM 调用面 · §6 出网网关
- [05-L3-对话主入口](../../../../docs/技术架构-v2/05-L3-对话主入口.md) §10 自主查证循环

## 备注

证据：`D:\Documents\STAgent\execution_log\agent-run\agr_ec73267803164d28a851.json`（`termination=llm_failed`、`reason=调用超时（>60000ms）`、18 轮 / 23 个主步骤）。LLM 用量记录显示 5 次真实超时耗时 61.8s / 66.1s / 67.9s / 92.7s / 105.4s，分别对应 1.2k–11k prompt tokens——超时与上下文大小无关。
