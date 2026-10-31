---
id: T-AGT-008.3
parent: T-AGT-008
title: 参数通道与目录合并
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M8
depends_on: [T-AGT-008.2]
status: done
decisions: [D-137, D-139]
verify: 执行日志 [T-AGT-008]——双通道拆参、未声明键被拒、闸门看整份 arguments 用例（tests/l1/test_builtin.py / tests/l3/test_agent_loop.py）；全量 4578 passed / 2 例既有环境性 ERROR
---

# T-AGT-008.3 · 参数通道与目录合并

## 目标
让对象型参数（如 SQL 的绑定参数数组）能经 `inputs` 通道进入执行器，而不是塞成 JSON 字符串。

## 验收标准（Given-When-Then）
- GWT-1：Given 一个声明对象参数的原语，When 模型给出对象实参，Then 经 `inputs` 核验后进入执行器。
- GWT-2：Given 一个标量参数原语，When 模型给出实参，Then 仍走 `values` 通道，存量行为不变。
- GWT-3：Given 同一 base 的多个版本，Then 工具目录仍折叠为一条。

## 接口面
- 输入（消费的前置接口）：
  - [`T-AGT-008.2`](T-AGT-008.2-内置原语注册与执行器.md) 交付的 builtin 注册与执行器（本叶只消费其描述体的 `input_schema` 声明）。
  - `ToolEntry` / `project_tool_entry`（`src/st_agent/l1/skills/tool_catalog.py:57` / `:77`）——模型可见条目与声明面的唯一投影。
  - `SkillRunner.run(skill_id, values, inputs=…)`（`src/st_agent/l1/runner/runner.py:220`）——`values`（标量）与 `inputs`（对象，按 `input_schema` 核验）两条既有通道。
  - 循环执行步（`src/st_agent/l3/runtime/loop.py:317`）——当前把 `call.arguments` 整体当 `values` 传。
- 输出（本任务交付的公共 API / 落盘位置）：
  - `ToolEntry` 增 `scalar_parameters` / `object_properties`（默认空元组，向后兼容），由 `project_tool_entry` 按描述体填。
  - 新增 `st_agent.l1.skills.tool_catalog.split_arguments(entry, arguments) -> (values, inputs)`：按声明拆两通道。
  - `src/st_agent/l3/runtime/loop.py` ③ 改为先 `split_arguments` 再 `runner.run(..., values, inputs=…)`；闸门仍看整份 `arguments`。
  - 契约文档：[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 的 ③ 执行段补通道路由一句。

## 可关闭的遗留
无（逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区，无「归属＝本任务」或「解封条件＝本任务 `done`」的条目）。

## 假设与前提
- **A1**：通道路由**按声明**——`parameters` 名 → `values`；`input_schema.properties` ∪ `required` 的键 → `inputs`；两者都未声明的键 → `values`，交 L1 按「未知参数」显式拒（不静默丢）。若错的影响：需要自由键的原语必须把键声明进 `input_schema`，否则模型给的键会被拒。验证方式：对象参数 / 标量参数 / 未声明键三组用例。
- **A2**：闸门仍接收**整份** `arguments`（拆通道只发生在执行前）；若闸门要按通道分级，需另改 `_gate_decision`。若错的影响：动作级风险分级可能看不到通道信息。验证方式：既有闸门用例 + 新用例断言 `seen.arguments` 完整。
- **A3**：存量标量原语逐字节不变（全部键都命中 `parameters`）。若错的影响：官方 Pack 与既有 Skill 的调用面回归。验证方式：既有循环 / 执行器用例全绿。

## 涉及契约
- [01 §2](../../../../docs/技术架构-v2/01-平台共享契约.md) —— `parameters`（标量）与 `input_schema`（对象）两条通道
- [05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md) —— 循环按通道填充实参

## 参考
- `l3/runtime/loop.py`（现按 `values` 传参）
- `l1/runner/runner.py` 的 `run(values, inputs=…)`

## 备注
**实现陷阱**：循环当前把 `call.arguments` 整体当 `values` 传（`loop.py:317`），而 `values` 只认 `ParameterSpec` 的标量；不改通道，每个对象型原语都要写 JSON 字符串 hack。
