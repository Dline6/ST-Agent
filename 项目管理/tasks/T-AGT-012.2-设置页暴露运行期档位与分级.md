---
id: T-AGT-012.2
parent: T-AGT-012
title: 设置页暴露运行期档位与分级
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/01-平台共享契约.md
arch_link: "[01 §7](../../docs/技术架构-v2/01-平台共享契约.md)"
priority: P0
milestone: M8
depends_on: [T-AGT-012.1]
status: done
decisions: [D-137, D-143]
verify: 范围套件 2338 passed / 2 errors（test_shell 环境性）；见执行日志 [T-AGT-012.2]
---

# T-AGT-012.2 · 设置页暴露运行期档位与分级

## 目标
让用户看得到、改得动、可回滚运行期授权口径。

## 验收标准（Given-When-Then）
- GWT-1：Given 设置页，When 打开，Then 显示 agent 档位与动作级分级清单及其缺省来源。
- GWT-2：Given 修改，Then 留痕于 `agent-change/` 且可回滚。
- GWT-3：Given 用户未改，Then 显示缺省值来源，不伪装成用户选择。

## 接口面
- 输入（消费的前置接口）：
  - `RuntimeAuthorization.tier()` / `.entries()` / `.changes()`（[`l6/runtime_authorization.py`](../../src/st_agent/l6/runtime_authorization.py)，T-AGT-005.1 交付）与 `.1` 新增的缺省档
  - 共享清单 `EvolutionAuthorization.grading()`（[`l6/authorization.py`](../../src/st_agent/l6/authorization.py)，T-L6-003.1 交付）
  - 01 §7 统一门面与 `agent.authorization` 族（[`l6/registry_adapter.py`](../../src/st_agent/l6/registry_adapter.py)，T-AGT-005.1 交付）
  - 留痕目录 `execution_log/agent-change/`（`EvolutionStore(change_prefix=AGENT_CHANGE_PREFIX)`）
  - 既有设置区读 / 写面与路由表（[`ui/surfaces/settings/`](../../src/st_agent/ui/surfaces/settings/)、[`ui/server.py`](../../src/st_agent/ui/server.py)）
  - L6 反思门面（[`composition/facades/reflection.py`](../../src/st_agent/composition/facades/reflection.py)）
- 输出（本任务交付的公共 API / 落盘位置）：
  - `RuntimeAuthorization.tier_source()`（`"default"` / `"user"`）与 `.rollback(change_id)`（重新落变更前档位值，本身产生新 `ChangeRecord`）
  - `ReflectionFacade.agent_authorization()` / `.rollback_agent_change(change_id)`
  - `SettingsReadSurface.api_settings_agent_authorization()` → 两段：`panel`（`setting_panel`：档位 + 分级清单只呈现 + 缺省来源）与 `trail`（`table` + `values`：`agent-change/` 近期留痕）
  - `SettingsWriteSurface.api_settings_agent_rollback()` → 固定回环路由 `POST /api/settings/agent-authorization/rollback`
  - 设置区页内视图 `?view=agent`（`ui/web/js/pages/settings.js` 的 `VIEWS` / `VIEW_TITLES` 与渲染分支）
  - 契约 / IA：[11-sitemap](../../docs/PRD-v2-Agent/11-sitemap.md) 设置分区增「运行期授权设置页」

## 可关闭的遗留
无（同 `.1`：逐册核过，无「归属＝本任务」或「解封条件＝本任务 `done`」的条目）

## 假设与前提
- A1：设置页以「设置区页内视图」承载（`/settings?view=agent`），不新增页面路径；演进授权页 `/settings/evolution` 不动。若错（要求独立路径）：须改 [11-sitemap §2.2](../../docs/PRD-v2-Agent/11-sitemap.md) 与 `PAGE_PATHS` / 前端登记表镜像。验证：`tests/ui/test_ui_shell_routing.py` 的页面漂移断言不变。
- A2：档位修改走既有 `/api/settings/apply`（面键 `settings-config`），不另开写通道；留痕落 `agent-change/`。若错（设置区不认该条目）：改为专用写端点，并同批补写面白名单。验证：`tests/ui/test_ui_settings.py` 的 apply 用例与 `agent-change/` 文件断言。
- A3：回滚以「重新落变更前档位值」实现——`ChangeRecord` 无 `rolled_back` 字段，回滚本身是一次新变更；重复回滚因取值未变返回 `None` 并如实说明。若错（须在原记录上标已回滚）：需扩 `ChangeRecord` 形态并改既有读面。验证：`tests/l6/test_runtime_authorization.py` 的回滚用例。
- A4：分级清单在本页**只呈现**，替换仍经清单 owner 的自有 API（[01 §7](../../docs/技术架构-v2/01-平台共享契约.md) 对象类条目口径）。若错（要求页面可改清单）：须另立对象类写面与校验，超出本叶范围。验证：面板条目的 `actions == []` 断言。

## 涉及契约
- [01 §7](../../docs/技术架构-v2/01-平台共享契约.md) —— 条目渲染与留痕

## 参考
- `l6/registry_adapter.py` 的 `agent_family`
- `ui/surfaces/evolution.py` 的既有演进授权页

## 备注
演进授权页已存在；本叶是**并列**的运行期授权页，不与演进面合并（[D-090] ① 的分设口径）。
