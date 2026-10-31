---
id: T-AGT-008.1
parent: T-AGT-008
title: 契约：工具源加 builtin
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/01-平台共享契约.md
arch_link: "[01 §2](../../../../docs/技术架构-v2/01-平台共享契约.md)"
priority: P0
milestone: M8
depends_on: []
status: done
decisions: [D-137, D-139]
verify: 执行日志 [T-AGT-008]——builtin 来源、版本折叠、两条不可写语义与导出拒绝用例（tests/l1/test_builtin.py / tests/eco/test_export.py / tests/ui/test_ui_skills.py）；全量 4578 passed / 2 例既有环境性 ERROR
---

# T-AGT-008.1 · 契约：工具源加 builtin

## 目标
[01 §2](../../../../docs/技术架构-v2/01-平台共享契约.md) 的 `source` 枚举增 `builtin`，并补三条语义；代码侧枚举与描述体同步。

## 验收标准（Given-When-Then）
- GWT-1：Given 01 §2 增 `builtin`，When 注册一条 builtin 描述体，Then 工具目录投影出该条目，且版本折叠口径与 Skill 一致。
- GWT-2：Given builtin 条目，When 走分享 / 导出面，Then 显式拒绝并点名（不可导出、不可分享）。
- GWT-3：Given 存量 `official` / `user-built` / `mcp-mapped` / `imported`，When 跑全量契约套件，Then 行为逐字节不变。

## 接口面
- 输入（消费的前置接口）：
  - [01 §2](../../../../docs/技术架构-v2/01-平台共享契约.md) 的 `source` 枚举与「工具暴露面的唯一来源」两段——按铁律 8 **先改该节**，再改代码。
  - `SkillRegistry.register(..., source=…)`（`src/st_agent/l1/skills/registry.py:165`）——描述体写入面，`source` 经 `SkillDescriptor` 的 `SkillSource` 字面量校验。
  - `SkillRegistry.set_parameters`（`registry.py:260`）与 `publish_version`（`registry.py:217`）——「不可编辑 / 不可版本化」的两处既有写口。
  - `ShareExporter.export_skill`（`src/st_agent/eco/export.py:87`）——`.stskill` 导出面。
  - `SkillsSurface` 的来源标签与参数面板（`src/st_agent/ui/surfaces/skills.py:221` / `:341`）。
- 输出（本任务交付的公共 API / 落盘位置）：
  - `st_agent.contracts.capability_types.SkillSource` 增 `"builtin"`（`capability_types.py:30`）；`SkillDescriptor` 字段形态不变。
  - `SkillRegistry.set_parameters` / `publish_version` 遇 `source == "builtin"` 抛 `SkillValidationError` 并点名语义，**不落盘**。
  - `ShareExporter.export_skill` 遇 builtin 抛 `ShareExportError` 并点名（不可导出 / 不可分享）。
  - UI：`_SKILL_SOURCE_LABELS` 增「内置原语」；builtin 参数面板条目 `actions=[]` + `note`（只读，不摆必然失败的按钮）。
  - 契约文档：[01 §2](../../../../docs/技术架构-v2/01-平台共享契约.md)（枚举 + 三条语义）。

## 可关闭的遗留
无（逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区，无「归属＝本任务」或「解封条件＝本任务 `done`」的条目）。

## 假设与前提
- **A1**：「不可版本化」＝ `publish_version` 拒 builtin；**注册**仍用带版本的 `skill_id`（`sk_<base>_v<主>.<次>`，不改 id 形态）。若错的影响：工具目录折叠（`project_catalog`）与执行目标解析都要另开分支。验证方式：本叶用例断言注册与折叠均沿用既有路径。
- **A2**：「不可编辑」＝ `set_parameters` 这一唯一参数写口拦截 + UI 不摆可点动作；`unregister` 不在三条语义内（保留既有行为）。若错的影响：需再给注销面加豁免或显式拒绝。验证方式：registry 单测 + UI 参数面板静态守卫。
- **A3**：「不可导出分享」只拦 `.stskill` **本体**导出；**引用** builtin 的 `.stflow` / `.stlens` 仍按原口径导出（依赖清单照常列出它，内置随每次安装齐备）。若错的影响：需在工作流 / 视角导出时级联拒绝含 builtin 依赖的分享物。验证方式：eco 用例 + 既有 flow / lens 导出回归。

## 涉及契约
- [01 §2](../../../../docs/技术架构-v2/01-平台共享契约.md) —— `source` 枚举与三条语义
- `contracts/capability_types.py` —— 代码侧枚举镜像

## 参考
- [D-137](../../../决策日志.md) D-1 拍定：原语作为 Skill 的一种来源，不另开第二注册面

## 备注
三条语义：**不可编辑 / 不可导出分享 / 不可版本化**。01 §2 的「注册面＝可见面」不破。
