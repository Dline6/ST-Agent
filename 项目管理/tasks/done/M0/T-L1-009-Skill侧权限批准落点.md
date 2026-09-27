---
id: T-L1-009
parent: null
title: Skill 侧权限批准落点（L1 遗留 D1 收口）
story: ../../docs/PRD-v2-Agent/story-02-skills-runtime.md
arch: ../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md
arch_link: "[03 §1.5](../../../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)"
priority: P0
milestone: M0
depends_on: [T-L1-001.1, T-L1-002.1, T-L1-004.1, T-L1-005.2, T-L1-006]
status: done
decisions: [D-042]
verify:
---

# T-L1-009 · Skill 侧权限批准落点（L1 遗留 D1 收口）

## 目标

给 [01 §10](../../../../docs/技术架构-v2/01-平台共享契约.md)「能力在安装/挂载时声明权限，用户**逐项批准**」补上 **Skill 侧**的持久落点——MCP 侧早有 `McpPermissionBook`（`config/mcp-permission/<server_id>.json`），Skill 侧至今只有声明（`SkillDescriptor.permissions`）、没有批准态：`SkillRunner.run(approved_permissions=…)` 由调用方每次现给，无人值守的执行（定时调度、L5 投递）取不到「已批准」这一事实。

同批校正一处**声明错位**：官方 Pack 的 12 个 Skill 全部声明了权限（10 个 `local_read:<data/cache/**>`、2 个 `net_access:<*.baostock.com>`），但这三条与已定口径并不同向——[D-031](../../../决策日志.md) ③ 已裁定「读 `data_cache` 内的缓存数据**不是** `local_read` 的范围」（它走 L0 鸭子类型取数面），且官方执行器**从不调用沙箱三类出口**（取数全经注入的 `market_query`）。即这 12 条是**惰性声明**（沙箱 enforcement 永不作数），却照样触发 [`SkillRunner` 的硬门](../../../../src/st_agent/l1/runner/runner.py) → 调度与试跑对官方 Skill 恒失败。

## 验收标准（Given-When-Then，从 Story 抄）

Story 02 的验收段不含「权限批准落点」条，故 GWT 逐条锚定 [01 §10](../../../../docs/技术架构-v2/01-平台共享契约.md)、[09 §3](../../../../docs/技术架构-v2/09-生态与分享.md) 的「权限审核 → 用户批准 → 安装」与 [L1 册 `D1`](../../../遗留问题/L1-遗留问题.md) 的解封条件，按叶子分散承载——见各叶子文件的 GWT 节（`.1` 五条 + `.2` 六条 + `.3` 三条）。

## 拆分记录（③ 已执行）

命中拆分触发（GWT 合计 14 条 ≫ 5；跨 [01 §10](../../../../docs/技术架构-v2/01-平台共享契约.md) 与 [03 §1.1](../../../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md) / [§1.5](../../../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md) / [§2](../../../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md) 三小节；中途需独立记录的决策），已拆为三个叶子：

- `T-L1-009.1` 语义下沉与账本本体：`contracts/permissions.py`（共用形态与措辞）+ `l1/permission_book.py`（共用簿记基类）+ `SkillPermissionBook`（base 粒度）；MCP 侧改为继承基类但**公共 API 与落盘逐字节不变**
- `T-L1-009.2` 挂载与供给接线：组合根持账本并出 `PermissionSource` adapter（注入 `Scheduler`）；MCP 派生 Skill 的批准**委托 MCP 账本**；复合 Skill 走**独立批准**
- `T-L1-009.3` 官方 Pack 声明校正：12 处 `permissions=()` + 01 §10 / 03 补口径注

④ 对齐定案两处（2026-09-27）：

① **官方 Pack 的 12 条声明判为「错位」并清空**（原候选：保留声明 + 按 provenance 记为 `approved`）——清空后官方 Pack **无需批准**，调度与试跑对官方 Skill 立即恢复可用，D1 的实际痛点随之消解；保留声明则须由产品替用户造「已批准」事实，与 [D-039](../../../决策日志.md) 否决 F 的理由（声明 ≠ 批准）相抵。
② **复合 Skill 取「独立批准」**（原候选：继承成员批准态）——[D-019](../../../决策日志.md) 已定复合 Skill 的 `permissions` 为成员的**并集**，而复合体是一等 Skill、独立展示给用户；「成员批过」不等于「复合体被展示过」。

父任务 `T-L1-009` 的 `status` 由 children 派生，不再手填。

## 接口面

- 输入 / 输出逐条见各叶子文件（父任务不承载实现面）。
- 本任务交付的总入口：`SkillPermissionBook`（`st_agent.l1.skills`）与组合根的 `PermissionSource` adapter，消费方 [`T-INT-001`](T-INT-001-M0集成关卡骨架打通冒烟.md)。

## 可关闭的遗留

- **L1 册 `D1`**（Skill 侧权限批准记录无落点；原归属「待人定（待立项）」→ 本批**修正归属**为 `T-L1-009`）：**本批关闭**——由 `.1`（账本本体）+ `.2`（供给接线）兑现，决策 [D-042](../../../决策日志.md)。销账随本批收口执行。

## 假设与前提

无（父任务不承载实现；假设按 `A<n>` 分列各叶子文件）。

## 涉及契约

- [01 §10 权限声明模型](../../../../docs/技术架构-v2/01-平台共享契约.md)（能力声明 → 用户逐项批准；批准落点）
- [03 §1.1 Skill Runtime](../../../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md) / [§1.5 执行沙箱](../../../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md) / [§2 官方 Skill Pack](../../../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)
- [09 §3 导入校验流水线](../../../../docs/技术架构-v2/09-生态与分享.md)（权限审核 → 用户批准 → 安装；下游写入面）

## 参考

- Story（What）：[skills-runtime](../../../../docs/PRD-v2-Agent/story-02-skills-runtime.md) · [mcp-hub](../../../../docs/PRD-v2-Agent/story-08-mcp-hub.md)
- 遗留：[L1 册 `D1`](../../../遗留问题/L1-遗留问题.md)
- 上游：[T-L1-002.1](T-L1-002.1-传输与Server注册表权限批准.md)（`McpPermissionBook` 先例）· [T-L1-005.2](T-L1-005.2-调度执行接线.md)（D1 的登记处）· [T-L1-006](T-L1-006-L1运行时装配组合根.md)（组合根）
- 机制出处：[工作流.md](../../../工作流.md)「拆分触发」

## 备注

实现细节不写此处，留给代码 / commit / 执行日志。
