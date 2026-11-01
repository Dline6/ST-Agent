---
id: T-AGT-028.1
parent: T-AGT-028
title: 会话级覆盖与三档切换
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/01-平台共享契约.md
arch_link: "[01 §7](../../docs/技术架构-v2/01-平台共享契约.md)"
priority: P1
milestone: M11
depends_on: [T-AGT-020]
status: todo
decisions: [D-154]
verify:
---

# T-AGT-028.1 · 会话级覆盖与三档切换

## 目标
窗口内切换 `manual` / `collaborative` / `autonomous`；覆盖为会话内瞬态，下一步生效；全局切换仍走既有配置与留痕。

## 验收标准（Given-When-Then）
- GWT-1：Given 会话内切到 `manual`，Then 下一步只读动作也需确认。
- GWT-2：Given 切回 `autonomous`，Then 只读自动、写仍需确认。
- GWT-3：Given 会话结束，Then 覆盖消失，不影响全局配置。

## 接口面
（④ 对齐时补：`RuntimeAuthorization.decide` 的档位取用、会话级覆盖的内存面、窗口控件。）

## 可关闭的遗留
（④ 对齐时逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区后补；检查 8 兜底。）

## 假设与前提
（④ 对齐时补：覆盖不改 `agent.authorization` 的落值，也不产生变更记录。）
