---
id: T-AGT-033.2
parent: T-AGT-033
title: 工具目录预算与惰性阈值
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M11
depends_on: [T-AGT-017.1, T-AGT-033.1]
status: todo
decisions: [D-145, D-160]
verify:
---

# T-AGT-033.2 · 工具目录预算与惰性阈值

## 目标

把工具目录从“总预算里的一个大块”改为**独立配额**：目录可见形态超过 `ContextEnvelope` 的固定比例（如 40%–50%）即进入惰性态，而不是超过整个预算才惰性；必要时按任务先筛一批工具，其余仍可经 `sk_tool_search` 展开。目录增长不再要求抬高全局预算。

## 验收标准（Given-When-Then）

- GWT-1：Given 35 个工具、目录约 11927 字符、工作信封固定比例，Then 惰性态**实际触发**；完整描述与参数只给已展开条目，摘要索引与检索入口可见。
- GWT-2：Given 惰性态，Then `sk_tool_search` 在工具通道可见并可展开条目；未展开条目仍可被直接调用（名字在摘要里），权限/沙箱/留痕无特例。
- GWT-3：Given 任务明确的查询，Then 可选的任务感知预筛只影响“先给哪些完整描述”，不减少可检索/可调用能力；预筛结果可解释。
- GWT-4：Given 目录从 35 增到 40+，Then 不需要抬高全局 `context_budget` 才能运行；测试钉住“目录增长不改变全局预算缺省”。
- GWT-5：Given 用户关闭部分工具，Then 目录配额按过滤后的目录计算，不把关闭项计入。

## 接口面

待 ④ 对齐实填。预计落点：

- 输入：`ContextEnvelope`（[`T-AGT-033.1`](T-AGT-033.1-预算信封与计量口径.md)）· `_tools_section_chars` / `tool_lazy_chars`（[`context.py`](../../src/st_agent/l3/runtime/context.py)）· `search_catalog` / `tool_search_spec`（[`tool_search.py`](../../src/st_agent/l3/runtime/tool_search.py)）· `project_catalog`（[`tool_catalog.py`](../../src/st_agent/l1/skills/tool_catalog.py)）· 工具开关过滤（[`composition/agent.py`](../../src/st_agent/composition/agent.py)）。
- 输出：目录独立配额 · 惰性阈值比例 ·（可选）任务感知预筛 · 目录增长的回归测试。

## 可关闭的遗留

无（新建任务；④ 对齐时逐册复核）。

## 假设与前提

- **A1**：惰性态对模型可用依赖系统提示词/工具描述的引导；与 [`T-AGT-031.1`](T-AGT-031.1-系统提示词.md) 同批验证。
- **A2**：任务感知预筛是可选优化，不做也能只靠惰性阈值满足 GWT-1/2；若做，必须保留“其余可检索”。
- **A3**：目录配额是 `ContextEnvelope` 的派生值，不是新的用户配置项。

## 涉及契约（链接到具体小节）

- [01-平台共享契约](../../docs/技术架构-v2/01-平台共享契约.md) §2 工具来源 · §7 配置项
- [05-L3-对话主入口](../../docs/技术架构-v2/05-L3-对话主入口.md) §10 工具检索与延迟加载

## 备注

证据：目录 11927 字符 < 缺省预算 16000，所以惰性态永不触发（`context.py:521` 缺省阈值 = 整个预算）；D-144/D-145/D-160 的预算连续上调（4000→6000→12000→16000）正是目录增长驱动的跑步机。
