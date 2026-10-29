---
id: T-AGT-015
parent: null
title: 开放网络
story: ../../docs/PRD-v2-Agent/story-05-local-first.md
arch: ../../docs/技术架构-v2/02-L0-本地优先基座.md
arch_link: "[02 §6](../../docs/技术架构-v2/02-L0-本地优先基座.md)"
priority: P1
milestone: M9
depends_on: [T-AGT-008.3]
status: done
decisions: [D-137, D-147]
verify: 三叶合成验收：搜索 / 抓取经唯一网关、默认关且关闭零请求、域名名单与搜索密钥掩码；见执行日志 [T-AGT-015.1]–[.3]（commit 待回填）
---

# T-AGT-015 · 开放网络

> **分组节点**：本节点不直接交付，交付由 [`T-AGT-015.1`](T-AGT-015.1-web_search联网搜索原语.md) / [`T-AGT-015.2`](T-AGT-015.2-web_fetch网页抓取原语.md) / [`T-AGT-015.3`](T-AGT-015.3-首次告知与出网开关.md) 承接；父状态与父 `depends_on` 由 `render_ledger.py render` 按 children 派生。

## 目标
让 Agent 能搜、能抓，且每次出网可审、可关。

## 验收标准（Given-When-Then）
- 分组节点不单独验收——由搜索 / 抓取 / 告知与开关三叶合成。

## 接口面
- 输入（消费的前置接口）：
  - `T-AGT-008.2` / `T-AGT-008.3`——内置原语注册面与执行面（`install_builtins`、`SkillContext.gateway` / `.sandbox`、实参双通道）。
  - `T-L0-018.2`（D-073）——`EgressGateway`（`stream` / `query` / 审计开关），全平台唯一出网口。
  - `T-L1-001.5`——`SkillSandbox.check_net` 与 `host_in_scope`（`net_access` 声明核对）。
  - `T-L0-004`——`CredentialVault`（搜索密钥落 `secrets` 分区）。
  - `T-UI-018.1`——设置区 `setting_panel` / `/api/settings/apply` 与凭据动作路由。
  - `T-AGT-012.1`——`DEFAULT_RISK_GRADING` 共享风险分级清单。
- 输出（本任务交付的公共 API / 落盘位置）：
  - `l0/net/web_config.py`：条目 `search.provider` / `search.key` / `web-egress/enabled` / `web-egress/allowed-hosts` 与 `load_web_settings()`。
  - `l0/net/search.py` / `l0/net/fetch.py`：Brave · Tavily 适配器、可注入 HTTP 发送口、结果归一化与 HTML→文本抽取。
  - `l1/builtin/web.py`：`sk_web_search_v1.0` / `sk_web_fetch_v1.0`，经 `BUILTIN_PRIMITIVES` 登记。
  - 设置读面 `/api/settings/web` 与设置视图 `/settings?view=web`。
  - 能力声明 `net-search` / `web-fetch`；`NetworkRequestKind` 增 `web_search` / `web_fetch`；L6 清单增两条 `collaborative-required` 规则。

## 可关闭的遗留
无（六册未闭区逐册核过，无「归属＝本任务」或「解封条件＝本任务 `done`」的条目；检查 8 = 0）。

## 假设与前提
- **A1** 搜索提供方取 Brave + Tavily 两个 JSON REST 适配器；未配置即 `unavailable`。若错的影响：适配器需按真实响应微调，返工集中在 `l0/net/search.py`。验证方式：注入 HTTP 替身的归一化用例。
- **A2** `search.key` 保留为 01 §7 条目，但密钥本体只落 `secrets` 分区（凭据恒加密），条目只回显「已配置 / 未配置」。若错的影响：若要配置分区可导出密钥，违反铁律 1，不取。验证方式：设置读面与 `CredentialVault` 用例。
- **A3** 「出网开关」限定为 Agent 搜索 / 抓取能力，不是全局断网开关；关闭＝零请求，既有数据同步 / LLM 不受影响。若错的影响：全局断网会改变既有链路语义，需另立契约。验证方式：关闭态下网关零调用用例。
- **A4** `web_fetch` 声明 `net_access:*`，运行期边界是用户域名名单（默认空＝全拒）。若错的影响：名单无法运行期配置则须改为静态声明。验证方式：名单外 `validation_failed` 且未发包用例。
- **A5** 网页正文是不可信数据：只返回文本并标注来源 URL，不执行页面指令；不自动跟随重定向。若错的影响：自动跟随会绕过名单，不可取。验证方式：重定向显式拒绝用例。
- **A6** 工作上下文预算暂维持 12000；新增 2 个工具后实测，超预算再按 D-146 口径调整。验证方式：工具目录字符数用例。
- **A7** 首次同意＝设置页阅读说明后显式开启（`requires_confirmation`）并留 `ChangeRecord`；不另造运行时弹窗状态机。验证方式：设置落值与留痕用例。
- **A8** 不新增分区、不改权限模型语法；`NetworkRequestKind` 增两个取值属加法，存量五类逐字节不变。验证方式：契约与网关存量用例。

## 涉及契约
- [02 §6](../../docs/技术架构-v2/02-L0-本地优先基座.md) —— 出网审计
- [02 §7](../../docs/技术架构-v2/02-L0-本地优先基座.md) —— 离线能力声明

## 参考
- 调研报告 §13.1

## 备注
`live-fetch` 能力声明已存在（`none` 级）；搜索是**新**能力，须同批声明。
