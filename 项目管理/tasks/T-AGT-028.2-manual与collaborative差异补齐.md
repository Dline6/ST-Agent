---
id: T-AGT-028.2
parent: T-AGT-028
title: manual 与 collaborative 差异补齐
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/01-平台共享契约.md
arch_link: "[01 §7](../../docs/技术架构-v2/01-平台共享契约.md)"
priority: P2
milestone: M11
depends_on: [T-AGT-028.1]
status: todo
decisions: [D-154]
verify:
---

# T-AGT-028.2 · manual 与 collaborative 差异补齐

## 目标
现两档在闸门上结论相同；给 `manual` 真实差异（每步单独批、不自动续跑），`collaborative` 保持一次确认放行本步。红线两档皆拒。

## 验收标准（Given-When-Then）
- GWT-1：Given `manual`，Then 每步单独确认且不自动续跑。
- GWT-2：Given `collaborative`，Then 一次确认可放行本步。
- GWT-3：Given 红线动作，Then 任何档位都拒绝。

## 接口面
（④ 对齐时补：`_decide` 的档位 × 风险映射表。）

## 可关闭的遗留
（④ 对齐时逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区后补；检查 8 兜底。）

## 假设与前提
（④ 对齐时补：不放宽任何现有红线；只让两档产生可观察差异。）
