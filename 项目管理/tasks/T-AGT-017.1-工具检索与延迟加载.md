---
id: T-AGT-017.1
parent: T-AGT-017
title: 工具检索与延迟加载
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P2
milestone: M10
depends_on: [T-INT-010]
status: done
decisions: [D-137, D-149]
verify: "执行日志 [T-AGT-017.1]；新增 15 例 + 全量 4910 passed；出厂目录 27 条 10276 字符未触发"
---

# T-AGT-017.1 · 工具检索与延迟加载

## 目标
目录超过阈值时只暴露摘要与检索入口，按需展开。

## 验收标准（Given-When-Then）
- GWT-1：Given 目录超过阈值，When 装配，Then 只暴露摘要与检索入口。
- GWT-2：Given 模型检索，Then 按需展开匹配条目。
- GWT-3：Given 目录未超阈值，Then 行为与现在一致。

## 接口面
- 输入（消费的前置接口）：
  - `T-INT-010`（done）——工具目录投影：`SkillRegistry.tool_catalog() -> tuple[ToolEntry, ...]`（`src/st_agent/l1/skills/tool_catalog.py`；条目含 `name` / `skill_id` / `description` / `parameters`）。
  - `T-AGT-004.1`（done）——工作上下文装配件：`st_agent.l3.runtime.context` 的 `assemble_work_context()` / `WorkContext.tool_specs()` / `WorkContext.render_prompt()`。
  - `T-AGT-004.2`（done）——循环驱动：`run_agent_loop()`（工具名 → 条目映射、控制原语拦截点、`LoopOutcome`）。
  - `T-AGT-011.1`（done）——循环内控制原语的先例（`sk_plan_update` 在循环内拦截，不产 `skill_run` / Trace 步）。
- 输出（本任务交付的公共 API / 落盘位置）：
  - 新模块 `src/st_agent/l3/runtime/tool_search.py`：`TOOL_SEARCH_TOOL`（`"sk_tool_search"`）、`DEFAULT_TOOL_SEARCH_LIMIT` / `MAX_TOOL_SEARCH_LIMIT`、`search_catalog(entries, query, *, limit=…)`、`tool_search_spec()`、`ToolSearchRecord`；惰性阈值无单独常量，缺省取本次预算。
  - `WorkContext` 新增 `lazy_tools` / `retrieved_tools` / `tool_search_note` 三字段；`tool_specs()` 与 `render_prompt()` 在惰性态改为「摘要 + 已展开条目」，**未超阈值时输出逐字节同现状**。
  - `LoopOutcome.tool_searches`（`ToolSearchRecord`）+ `AgentRunRecord.tool_searches` 留痕字段（决策，不产 Trace 步 / `skill_run`）。
  - `run_agent_loop(..., tool_lazy_chars=...)` 新增注入参数；缺省取本次工作上下文预算（`budget_chars`）。

## 可关闭的遗留
无（2026-10-30 逐册核过 [遗留问题](../遗留问题/README.md) 六册未闭区，无「归属＝本任务」或「解封条件＝本任务 `done`」的条目）。

## 假设与前提
- **A1**（检索入口是循环的**上下文控制**，不注册进 L1 能力目录）——前提：若注册为内置原语，未超阈值时目录会多一条、破坏 GWT-3 的「逐字节不变」。若错的影响：模型可见面与注册面出现一处例外（`.1` 需回改注册面契约）。验证方式：未超阈值用例逐字节比对装配输出；惰性态断言检索入口只出现在摘要段。
- **A2**（阈值＝目录模型可见形态字符数 > 本次预算；缺省 12000）——前提：当前出厂 26 条实测工具段 **9712 字符**（`size_chars` 9790），未触发。若错的影响：出厂的自主循环会立即进入惰性态（行为突变）。验证方式：出厂目录实测断言未触发；构造超阈值目录断言触发。
- **A3**（检索匹配口径＝查询词元全部在 `name` + `description` 中不区分大小写命中，`name` 命中优先，最多返回 5 条、可参数覆盖 1–10）——前提：词法匹配足够，不引入向量检索（D-137 D-5 未解冻 RAG）。若错的影响：检索到的条目与模型意图偏差，模型可再检索一次。验证方式：排序 / 上限 / 空命中用例。
- **A4**（检索不过 L6 闸门、不计步数）——前提：它只改「模型看到什么」，无任何外部副作用，与 `sk_plan_update` 同类；但为如实留痕，检索记录进 `agent_run`。若错的影响：审查面少一条本应经闸门的动作。验证方式：循环用例断言检索后无 `skill_run`、无 Trace 步、步数不变，且 `agent_run` 有检索记录。
- **A5**（空 / 非法 `query` 不终止循环）——给显式说明进下一轮上下文，由模型纠正；到上界仍未纠正即按上界终止。验证方式：非法 query 用例断言循环继续且下一轮上下文含原因。

## 涉及契约
- [01 §2](../../docs/技术架构-v2/01-平台共享契约.md) —— 工具暴露面
- [05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md) —— 工作上下文

## 参考
- Codex `tool_search` / Claude Code 的延迟加载

## 备注
阈值属实现口径；未超阈值时必须逐字节不变。
