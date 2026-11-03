---
id: T-AGT-033
parent: null
title: 上下文装载机制重整
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M11
depends_on: [T-AGT-009.3, T-L0-003, T-AGT-017.1, T-AGT-009.2, T-AGT-029.1, T-AGT-004.2, T-AGT-004.1, T-AGT-017.2, T-AGT-031.2]
status: done
decisions: [D-090, D-142, D-145, D-160]
verify:
---

# T-AGT-033 · 上下文装载机制重整

> **分组节点**：本节点不直接交付，交付由 [`T-AGT-033.1`](T-AGT-033.1-预算信封与计量口径.md) / [`T-AGT-033.2`](T-AGT-033.2-工具目录预算与惰性阈值.md) / [`T-AGT-033.3`](T-AGT-033.3-任务锚点与历史装载.md) / [`T-AGT-033.4`](T-AGT-033.4-长任务步骤压缩与预算联动.md) / [`T-AGT-033.5`](T-AGT-033.5-截断回读与预览派生.md) / [`T-AGT-033.6`](../../T-AGT-033.6-Agent自调申请与进展检查.md) 承接；父状态与父 `depends_on` 由 `render_ledger.py render` 按 children 派生。

## 目标

修复 2026-11-03 真链路实验暴露的结构性问题：工具目录、步骤、历史共用一条“可手调总预算”，惰性阈值又等于总预算；历史“遇大即停”导致原问题被挤出；字符与 token 口径混用；截断/回读/预览四处固定数字互不相干；长任务步骤无压缩。方向是**把“上下文怎么切”交给运行时派生与 Agent 在钳制内自选，只保留“端点窗口 × 安全比例”这一条硬边界**，不再靠反复抬高全局预算和给“建议值”维持。

## 验收标准（Given-When-Then）

- GWT-1：由 `.1`–`.6` 合成，父节点不单独验收。

## 接口面

子叶并集（详见各叶）：

- **输入**：`assemble_context`（[`session.py`](../../../../src/st_agent/l3/chat/session.py)）· `assemble_work_context` / `_fit_budget` / `render_prompt`（[`context.py`](../../../../src/st_agent/l3/runtime/context.py)）· 端点能力（[`registry.py`](../../../../src/st_agent/l0/llm/registry.py) / [`models.py`](../../../../src/st_agent/l0/llm/models.py)）· 配置族（[`bounds.py`](../../../../src/st_agent/l3/runtime/bounds.py)）· 工具目录（[`tool_catalog.py`](../../../../src/st_agent/l1/skills/tool_catalog.py) / [`tool_search.py`](../../../../src/st_agent/l3/runtime/tool_search.py)）· 回读原语（[`results.py`](../../../../src/st_agent/l1/builtin/results.py)）。
- **输出**：`ContextEnvelope`（端点窗口派生的硬边界）· 目录/历史/步骤/记忆的分配规则 · 任务锚点 · 步骤压缩 · 从预算派生的截断/回读参数 · Agent 自调申请动作。

## 可关闭的遗留

无（新建任务；④ 对齐时逐册复核 L0 / L1 / L2 / L3 / L5 / L6 未闭区）。

## 假设与前提

子叶并集，详见各叶 A 编号。

## 涉及契约（链接到具体小节）

- [01-平台共享契约](../../../../docs/技术架构-v2/01-平台共享契约.md) §7 配置项
- [02-L0-本地优先基座](../../../../docs/技术架构-v2/02-L0-本地优先基座.md) §4 LLM 调用面
- [05-L3-对话主入口](../../../../docs/技术架构-v2/05-L3-对话主入口.md) §1 会话模型 · §10 自主查证循环

## 备注

本机实测：35 个工具的模型可见形态 **11927 字符**；真实运行 `agr_ec73…` 的 23 个步骤重建后 **17110 字符**；目录 + 步骤约 **29000 字符**，而出厂预算是 **16000**。用真实会话模拟：预算 16000 时只保留最后 4 条消息，原始提问被 13:22:56 的 7086 字符载荷挡住而丢弃；预算 4000 时只剩最后一条失败消息。用户把预算调到 256000 掩盖了该问题，但单条结果仍被 1200 阈值压成 200 字符预览。
