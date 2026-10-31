---
id: T-AGT-018.1
parent: T-AGT-018
title: 并行工具调用
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P2
milestone: M10
depends_on: [T-INT-010]
status: done
decisions: [D-137, D-151]
verify: "全量 4930 passed / 16 deselected / 2 errors（两例 test_shell 环境性）；新增 tests/l3/test_agent_parallel.py 9 例、tests/integration/test_m5_autonomy.py 接线用例；见执行日志 [T-AGT-018.1]"
---

# T-AGT-018.1 · 并行工具调用

## 目标
一轮多个工具调用，逐个过闸门后并行执行。

## 验收标准（Given-When-Then）
- GWT-1：Given 一轮多个 `tool_calls`，When 执行，Then 逐个过闸门并并行执行。
- GWT-2：Given 任一被拒，Then 该步被拒、其余按规则处理，不静默丢。
- GWT-3：Given 底层并发不安全，Then 显式退回串行并标注。

## 接口面
- 输入（消费的前置接口）：
  - `l3/runtime/protocol.py::TurnResult.tool_calls`——一轮多个调用（`T-AGT-001` 已交付；L0 按 `index` 拼接、升序全出）。
  - `l3/runtime/loop.py::run_agent_loop`——现行只取 `tool_calls[0]`（改造点）。
  - `l1/runner/runner.py::SkillRunner.run`——真流水线（参数 / 权限 / 沙箱 / 留痕），循环不绕过任何一环。
  - `l1/skills/tool_catalog.py::ToolEntry`——按工具名解析执行目标。
  - `l3/runtime/gate.py::ActionGate.authorize`——无状态，可按调用顺序先全部问一遍。
  - 并发安全依据：`l0/storage/store.py::_part_lock`（[02 §2.4](../../docs/技术架构-v2/02-L0-本地优先基座.md) 的分区级 RLock + 原子替换，[D-015](../决策日志.md)）、`SkillRunner` 构造后无可变共享状态、`Trace` 为 frozen 追加式、`SkillRegistry` 每次读 Store 无内存缓存。
- 输出（本任务交付的公共 API / 落盘位置）：
  - `run_agent_loop(..., parallel_safe: bool = False)`——**缺省不声明并发安全，显式退回串行**；组合根核验后注入 `True`。
  - `LoopOutcome.parallel_used: bool` / `parallel_note: str` / `executed_steps: int`——并发形态与**实际消耗步数**的显式留痕。
  - `AgentRunRecord.parallel_used` / `parallel_note` / `executed_steps`（缺省值保证旧记录可读）。
  - `resume_agent_run(..., parallel_safe=False)` 同参透传。
  - 契约：[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)「并行工具调用」段；测试 `tests/l3/test_agent_parallel.py`。

## 可关闭的遗留
无（L0 / L1 / L2 / L3 / L5 / L6 六册未闭区逐册核过，无「归属＝本任务」或「解封条件＝本任务 `done`」的条目；检查 8 = 0）。

## 假设与前提
- **A1**（底层并发安全有据）——`Store` 按分区互斥（D-015）、`SkillRunner` 无可变共享状态、`Trace` frozen 追加式、注册表读取无内存缓存。若错：并发批可能丢留痕或乱序。验证：`tests/l3/test_agent_parallel.py` 用真 `Store` + 真 `SkillRunner` 跑并发批，断言每条 `skill_run` 记录都在。
- **A2**（并行批只含 `autonomous-ok`）——写类 / 出网 / 命令遇到非自主结论即交还，故不会并发写入。若错：写类动作并发。验证：拒绝用例断言整批零执行、零留痕。
- **A3**（缺省 False 不改变既有单调用行为）——单调用不写 `parallel_note`，先后次序与既有逐字节一致。若错：既有循环用例红。验证：既有 `tests/l3/test_agent_loop.py` 全绿 + 单调用用例。
- **A4**（Trace 合并确定有序）——各分支以同一 frozen base 起跑，完成后按调用顺序 append 回主链。若错：推理链乱序。验证：慢 / 快两调用用例断言步与链均按调用顺序。
- **A5**（一轮多调用按多动作计预算）——`executed + 本轮可执行数 > max_steps` 即触界，整批不执行。若错：上界语义漂移。验证：`max_steps=1` + 两调用用例。
- **A6**（组合根注入 True 后生产真并行）——组合根在核验 A1 后注入；未注入的根仍串行。若错：生产退回串行（功能未生效）。验证：`composition/agent.py` 的两个入口均显式传 `parallel_safe=True`，由集成套件按端口取值断言。

## 涉及契约
- [05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md) —— 循环执行语义（并行工具调用段）

## 参考
- `l3/runtime/loop.py:282`（现「一轮只认第一个调用」）

## 备注
**先验证再承诺**：`SkillRunner` 与 `Store` 的并发安全没有证据，任务 `假设与前提` 须先列这条验证。
