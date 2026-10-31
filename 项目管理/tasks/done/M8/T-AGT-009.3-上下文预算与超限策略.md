---
id: T-AGT-009.3
parent: T-AGT-009
title: 上下文预算与超限策略
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M8
depends_on: [T-AGT-009.2]
status: done
decisions: [D-137, D-142]
verify: "范围 1279 passed；全量 4622 passed（2 例已知环境性 ERROR）；见执行日志 [T-AGT-009.3]"
---

# T-AGT-009.3 · 上下文预算与超限策略

## 目标
历史 / 记忆 / 工具同时进入后重定工作上下文预算，超限时**显式取舍**而非静默截断。

## 验收标准（Given-When-Then）
- GWT-1：Given 超预算上下文，When 装配，Then 按声明顺序取舍，并在运行报告里标注被舍弃项。
- GWT-2：Given 未超预算，Then 逐字节原样。
- GWT-3：Given 预算配置项被修改，Then 下次装配生效并留痕。

## 接口面
- 输入（消费的前置接口）：
  - `.1` 的 `session_id` 透传、`.2` 的 `history` / `memory` 注入面
  - 01 §7 统一配置门面（`l1/registry`）与 `investigate.` 族适配器（`l3/runtime/registry_adapter.py`）；既有 `investigate.max_steps` / `.max_llm_calls` 的落盘与变更留痕口径（`l3/runtime/bounds.py`）
  - `DEFAULT_WORK_CONTEXT_BUDGET` / `RESULT_TRUNCATE_CHARS`（`l3/runtime/context.py`）
- 输出（本任务交付的公共 API / 落盘位置）：
  - 新配置项 `investigate.context_budget`（整数、缺省取 `DEFAULT_WORK_CONTEXT_BUDGET`、上限属实现口径、`change_policy` 需确认，落 `config` 分区 `investigate/context_budget.json`）；`LoopBounds.budget_chars()` 读取
  - `WorkContext.dropped: tuple[str, ...]`、`LoopOutcome.dropped` / `budget_chars`、`AgentRunRecord.context_dropped` / `budget_chars`
  - 取舍顺序：记忆切片 → 历史消息（自旧向新）→ 步骤不丢；任务与工具目录恒保留

## 可关闭的遗留
无（同父任务）

## 假设与前提
- A1：取舍顺序＝记忆 → 历史（旧→新）；任务与工具目录不参与取舍，步骤按 D-090 **不丢弃**。若错：可能牺牲本轮证据或能力面，违反可解释 / 可执行。验证：超预算用例断言保留集与被舍项标注。
- A2：预算项是**长期偏好**（非意图级参数），登记 01 §7 并走门面既有 `ChangeRecord`，「下次装配生效 + 留痕」由此兑现。若错：改为意图级参数会动 05 §3.2 确认卡，超出本叶范围。验证：改配置后装配用例 + 变更记录断言。
- A3：仍超限时如实 `over_budget=True`、不压缩（D-090 冻结）。若错：须解冻压缩并扩里程碑。验证：超限用例的 `over_budget` 与 `dropped` 并存。
- A4：上限取 32000 属实现口径（防跑飞）；缺省与 `DEFAULT_WORK_CONTEXT_BUDGET` 单一事实源，不另立数字。若错：登记面与运行期缺省分叉。验证：配置缺省断言。

## 涉及契约
- [01 §7](../../../../docs/技术架构-v2/01-平台共享契约.md) —— 预算条目
- [05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md) —— 超限语义

## 参考
- `l3/runtime/context.py`（现 `over_budget` 只标注、不压缩）

## 备注
本期**不做**压缩（D-090 冻结）；超限策略是「如实取舍 + 标注」，压缩留 M10。
