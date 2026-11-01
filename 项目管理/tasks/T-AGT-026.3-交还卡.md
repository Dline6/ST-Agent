---
id: T-AGT-026.3
parent: T-AGT-026
title: 交还卡
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/PRD-v2-Agent/14-desktop-form.md
arch_link: "[14 §3](../../docs/PRD-v2-Agent/14-desktop-form.md)"
priority: P1
milestone: M11
depends_on: [T-AGT-026.1]
status: todo
decisions: [D-154]
verify:
---

# T-AGT-026.3 · 交还卡

## 目标
`gate_pending` 出确认卡、`ask_user` 出问项卡；确认后续跑同一 run。

## 验收标准（Given-When-Then）
- GWT-1：Given `gate_pending`，Then 出确认卡（允许 / 拒绝）。
- GWT-2：Given `ask_user`，Then 出问项卡（一问一参、可跳过）。
- GWT-3：Given 用户确认，Then 续跑同一 run 而非新建。

## 接口面
（④ 对齐时补：`agent_continuation` 载荷与卡片组件的对应关系。）

## 可关闭的遗留
（④ 对齐时逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区后补；检查 8 兜底。）

## 假设与前提
（④ 对齐时补：复用既有 `resume` 端点；不新增第二条续跑路径。）
