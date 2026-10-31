---
id: T-AGT-015.3
parent: T-AGT-015
title: 首次告知与出网开关
story: ../../docs/PRD-v2-Agent/story-05-local-first.md
arch: ../../docs/技术架构-v2/02-L0-本地优先基座.md
arch_link: "[02 §6](../../../../docs/技术架构-v2/02-L0-本地优先基座.md)"
priority: P1
milestone: M9
depends_on: [T-AGT-015.1]
status: done
decisions: [D-137, D-147]
verify: 出网开关默认关、关闭零请求、开启留痕可回滚；设置页展示出境范围与搜索凭据掩码；见执行日志 [T-AGT-015.3]（commit d1e4659）
---

# T-AGT-015.3 · 首次告知与出网开关

## 目标
把「什么会出网」讲清楚，并让用户能一键关掉。

## 验收标准（Given-When-Then）
- GWT-1：Given 首次使用搜索 / 抓取，Then 告知数据出境范围并取得同意。
- GWT-2：Given 用户关闭，Then 工具不可用且原因显式。
- GWT-3：Given 关闭状态，Then 不发起任何出网请求。

## 接口面
- 输入（消费的前置接口）：
  - `T-AGT-015.1`——`l0/net/web_config.py` 与两条原语（开关与名单的消费方）。
  - `T-L0-018.2`（D-073）——`net-audit/enabled` 的既有先例（与审计开关不是同一件）。
  - `T-UI-018.1`——设置区 `setting_panel` / `/api/settings/apply` / 凭据动作路由。
- 输出（本任务交付的公共 API / 落盘位置）：
  - `web-egress/enabled`（布尔，默认 `false`）与 `web-egress/allowed-hosts`（字符串）两条 01 §7 条目；落盘 `config/web-egress/…`，留痕 `execution_log/web-egress-change/`。
  - `L1` 注册族 `WebEgressFamily` / `SearchFamily`（`l1/registry/families.py`），由 `build_l1_runtime` 注入统一门面。
  - 设置读面 `/api/settings/web` 与设置视图 `/settings?view=web`（告知文案 + 开关 + 名单 + 提供方 + 搜索凭据掩码）。
  - 关闭态两条原语 `unavailable` 并携带完整出境告知与开启指引。

## 可关闭的遗留
无（六册未闭区逐册核过，无「归属＝本任务」或「解封条件＝本任务 `done`」的条目；检查 8 = 0）。

## 假设与前提
- **A1** 出网开关只作用于 Agent 搜索 / 抓取，不是全局断网开关；关闭＝原语直接 `unavailable`、网关零调用。若错的影响：全局断网会改变既有数据同步 / LLM 语义，需另立契约。验证方式：关闭态下替身 sender 零调用用例。
- **A2** 首次同意＝设置页阅读告知后显式开启（`change_policy.requires_confirmation=True`）并留 `ChangeRecord`；不另造运行时弹窗状态机。若错的影响：若要求首次调用时弹窗，需要 L3 交互状态机，超出本叶。验证方式：开启产生变更留痕、可回滚的用例。
- **A3** 允许域名默认空＝全拒；模式为大小写不敏感的 glob，`*.example.com` 同时匹配裸域。若错的影响：默认放行会削弱数据主权。验证方式：空名单拒绝、`*.x` 匹配裸域、名单外拦截用例。
- **A4** 搜索密钥由既有凭据 chrome 增改吊销，标识 `search-api-key`；设置读面只出掩码，不出明文。若错的影响：密钥需另造 UI 通道。验证方式：读面掩码与凭据分区锁定态用例。

## 涉及契约
- [02 §6](../../../../docs/技术架构-v2/02-L0-本地优先基座.md) —— 出网开关
- [02 §7](../../../../docs/技术架构-v2/02-L0-本地优先基座.md) —— 离线清单

## 参考
- [D-073](../../../../docs/技术架构-v2/01-平台共享契约.md)

## 备注
与既有审计开关**不是同一件**：审计管「留不留痕」，本叶管「允不允许出网」。
