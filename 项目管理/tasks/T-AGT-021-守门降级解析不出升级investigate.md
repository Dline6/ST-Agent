---
id: T-AGT-021
parent: null
title: 守门降级：解析不出升级 investigate
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §3.2](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M11
depends_on: [T-AGT-020]
status: todo
decisions: [D-154]
verify:
---

# T-AGT-021 · 守门降级：解析不出升级 investigate

## 目标
`query` / `configure` 的目标解析不出时，不再返回 `empty`，改为升格为 `investigate`（用户原话作 `task`）并产出确认卡，使能力池外的开放式问题能进入循环。

## 验收标准（Given-When-Then）
- GWT-1：Given 输入「最近ST股票有没有什么热门板块?」，When `post`，Then 出 `investigate` 确认卡（target 为原话），不再 `empty`。
- GWT-2：Given 目标 Skill 已注册或匹配成功，Then 仍按原去向出卡，不降级。
- GWT-3：Given 理解端口未注入，Then 仍返回 `unavailable`（不硬猜）。

## 接口面
（④ 对齐时补：`IntentProtocol._resolved` / `undispatchable` / `withheld_round` 的降级分支；`DialogFacade.post` 的守门分支。）

## 可关闭的遗留
（④ 对齐时逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区后补；检查 8 兜底。）

## 假设与前提
（④ 对齐时补：升格后的 `investigate` 卡可被现有 `resume` 承接；不改变已注册目标的既有路径。）
