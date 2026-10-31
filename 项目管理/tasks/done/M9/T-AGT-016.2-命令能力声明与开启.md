---
id: T-AGT-016.2
parent: T-AGT-016
title: 命令能力声明与开启
story: ../../docs/PRD-v2-Agent/story-05-local-first.md
arch: ../../docs/技术架构-v2/02-L0-本地优先基座.md
arch_link: "[02 §7](../../../../docs/技术架构-v2/02-L0-本地优先基座.md)"
priority: P1
milestone: M9
depends_on: [T-AGT-016.1]
status: done
decisions: [D-137, D-148]
verify: local-exec 离线档 full、默认关、开启留痕可回滚；设置页「本地命令」开关；见执行日志 [T-AGT-016.2]（commit 7dca980）
---

# T-AGT-016.2 · 命令能力声明与开启

## 目标
把命令执行登记为离线能力，并在设置页显式开关。

## 验收标准（Given-When-Then）
- GWT-1：Given [02 §7](../../../../docs/技术架构-v2/02-L0-本地优先基座.md)，When 登记 `local-exec`，Then 离线清单**归入「可用」组**并标注「出厂默认关闭，需显式开启」（本机能力断网可用；`none` 专指必须联网）。
- GWT-2：Given 默认，Then 禁用。
- GWT-3：Given 用户显式开启，Then 留痕且可回滚。

## 接口面
- 输入（消费的前置接口）：
  - `T-AGT-016.1`（本批）：`RUN_COMMAND` / `sk_run_command` 与 `commands` 运行态入口——开关关闭时由本叶在原语执行前拦截。
  - `T-AGT-015.3`（done）：`WebEgressFamily` 与 `/api/settings/web` 视图——开关类条目的「默认关 + 显式开启 + 留痕 + 设置面」先例。
  - `T-L1-012`（done）：`ConfigRegistryFacade` 与族适配器——条目注册与统一落值面。
  - `T-L0-001` / `l0/net/capabilities.py`（done）：`DEFAULT_CAPABILITIES` 与 `seed_default_capabilities`——出厂能力声明表。
- 输出（本任务交付的公共 API / 落盘位置）：
  - `src/st_agent/l0/exec/exec_config.py`：`LOCAL_EXEC_ENABLED_CONFIG_ID = "local-exec/enabled"`、`get/set_local_exec_enabled`、`exec_config_entries()`；缺省 `false`。
  - `src/st_agent/l1/registry/families.py`：`LocalExecFamily`（变更留痕前缀 `local-exec-change/`）。
  - `src/st_agent/l0/net/capabilities.py`：`local-exec` 出厂声明。
  - `src/st_agent/l6/authorization.py`：`skill.sk_run_command` → `collaborative-required`。
  - `src/st_agent/ui/surfaces/settings/command.py` + `/api/settings/command` + 设置左栏「本地命令」。

## 可关闭的遗留
无（开工查六册未闭区，无命中）。

## 假设与前提
- **A1**（能力离线档）——`local-exec` 登记为 `full`（本机能力、断网可用）；设置面板另显「默认关闭」。若按 GWT-1 字面登记 `none`，会把本机能力误标为「必须联网」。**验证**：`offline_report()` 按 `full` 归组；④ 由负责人确认。
- **A2**（开关只作用于命令原语）——`local-exec/enabled` 不影响文件 / 网络 / 数据原语。**验证**：关闭态仅 `sk_run_command` 不可用，既有套件全绿。
- **A3**（开启留痕可回滚）——经统一落值面开启产生 `ChangeRecord`（`change_id` 可回滚）。**验证**：集成用例断言留痕与读回 `true`。
- **A4**（默认禁用）——未落值时读为 `false`，原语 `unavailable` 且零执行。**验证**：默认态用例。
- **A5**（设置面板不新造渲染件）——复用 `setting_panel` 与既有设置路由形态（`T-AGT-015.3` 先例）。**验证**：UI 设置装配用例。

## 涉及契约
- [02 §7](../../../../docs/技术架构-v2/02-L0-本地优先基座.md) —— 能力声明表

## 参考
- `l0/net/capabilities.py`（`DEFAULT_CAPABILITIES`）

## 备注
`local-exec` 与 `net-search` 是本批新增的两条出厂声明；其中 `net-search` 与 `web-fetch` 已由 [`T-AGT-015`](T-AGT-015-开放网络.md) 先期登记（`l0/net/capabilities.py`），本叶只补 `local-exec` 与命令开关。
