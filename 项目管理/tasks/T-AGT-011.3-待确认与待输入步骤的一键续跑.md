---
id: T-AGT-011.3
parent: T-AGT-011
title: 待确认与待输入步骤的一键续跑
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M8
depends_on: [T-AGT-011.2, T-AGT-012.1]
status: todo
decisions: [D-137]
verify:
---

# T-AGT-011.3 · 待确认与待输入步骤的一键续跑

## 目标
把「终止交还」从死胡同变成可续跑：已收集的证据不重跑。

## 验收标准（Given-When-Then）
- GWT-1：Given 循环因 `needs-confirmation` 终止，When 用户确认，Then 从该步继续而非重跑全程。
- GWT-2：Given 因 `ask_user` 终止，When 用户作答，Then 带答案续跑。
- GWT-3：Given 续跑时依据已过期，Then 显式提示并允许重跑。

## 接口面
<④ 对齐时实填（`doing` 起不可留占位），见 [工作流](../工作流.md) 第 ④ 步>
- 输入（消费的前置接口）：
- 输出（本任务交付的公共 API / 落盘位置）：

## 可关闭的遗留
<④ 对齐时实填：扫 [遗留问题](../遗留问题/README.md) 各层册的未闭区，逐条列「归属＝本任务」或「解封条件＝本任务 `done`」的条目（册内 id + 处置）；无命中写 `无`。>

## 假设与前提
<④ 对齐时实填；每条 `A<n>` 含 前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`>

## 涉及契约
- [05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md) —— 交还与续跑
- [05 §4](../../docs/技术架构-v2/05-L3-对话主入口.md) —— 派发回执

## 参考
- `l3/runtime/report.py`（`pending` 已携待执行的那一步）

## 备注
`report` 里已经有 `pending`（待执行的那一步），续跑主要是**回执 + UI**，不是重造循环。
