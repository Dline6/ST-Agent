---
id: T-AGT-008.1
parent: T-AGT-008
title: 契约：工具源加 builtin
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/01-平台共享契约.md
arch_link: "[01 §2](../../docs/技术架构-v2/01-平台共享契约.md)"
priority: P0
milestone: M8
depends_on: []
status: todo
decisions: [D-137]
verify:
---

# T-AGT-008.1 · 契约：工具源加 builtin

## 目标
[01 §2](../../docs/技术架构-v2/01-平台共享契约.md) 的 `source` 枚举增 `builtin`，并补三条语义；代码侧枚举与描述体同步。

## 验收标准（Given-When-Then）
- GWT-1：Given 01 §2 增 `builtin`，When 注册一条 builtin 描述体，Then 工具目录投影出该条目，且版本折叠口径与 Skill 一致。
- GWT-2：Given builtin 条目，When 走分享 / 导出面，Then 显式拒绝并点名（不可导出、不可分享）。
- GWT-3：Given 存量 `official` / `user-built` / `mcp-mapped` / `imported`，When 跑全量契约套件，Then 行为逐字节不变。

## 接口面
<④ 对齐时实填（`doing` 起不可留占位），见 [工作流](../工作流.md) 第 ④ 步>
- 输入（消费的前置接口）：
- 输出（本任务交付的公共 API / 落盘位置）：

## 可关闭的遗留
<④ 对齐时实填：扫 [遗留问题](../遗留问题/README.md) 各层册的未闭区，逐条列「归属＝本任务」或「解封条件＝本任务 `done`」的条目（册内 id + 处置）；无命中写 `无`。>

## 假设与前提
<④ 对齐时实填；每条 `A<n>` 含 前提内容 / 若错的影响 / 验证方式；确认无假设写 `无（<原因>）`>

## 涉及契约
- [01 §2](../../docs/技术架构-v2/01-平台共享契约.md) —— `source` 枚举与三条语义
- `contracts/capability_types.py` —— 代码侧枚举镜像

## 参考
- [D-137](../决策日志.md) D-1 拍定：原语作为 Skill 的一种来源，不另开第二注册面

## 备注
三条语义：**不可编辑 / 不可导出分享 / 不可版本化**。01 §2 的「注册面＝可见面」不破。
