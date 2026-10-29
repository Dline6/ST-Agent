---
id: T-AGT-015.1
parent: T-AGT-015
title: web_search（联网搜索原语）
story: ../../docs/PRD-v2-Agent/story-05-local-first.md
arch: ../../docs/技术架构-v2/02-L0-本地优先基座.md
arch_link: "[02 §6](../../docs/技术架构-v2/02-L0-本地优先基座.md)"
priority: P1
milestone: M9
depends_on: [T-AGT-008.3]
status: done
decisions: [D-137, D-147]
verify: web_search 经网关返回带来源 / 时间提示的结果、未配置显式不可用、审计不含查询词；见执行日志 [T-AGT-015.1]（commit 待回填）
---

# T-AGT-015.1 · web_search（联网搜索原语）

## 目标
接入第三方搜索，返回带来源与时间的结果。

## 验收标准（Given-When-Then）
- GWT-1：Given 已配置搜索提供方，When 调用，Then 返回结果并标注来源与时间。
- GWT-2：Given 未配置，Then `unavailable` 并给配置指引，**不静默降级**。
- GWT-3：Given 调用，Then 经 `EgressGateway` 留痕（审计只含字节数与目的说明，不含查询正文）。

## 接口面
- 输入（消费的前置接口）：
  - `T-AGT-008.2` / `T-AGT-008.3`——`install_builtins` / `BuiltinPrimitive` / `SkillContext.gateway`（内置原语注册与执行面）。
  - `T-L0-018.2`（D-073）——`EgressGateway.stream(kind, host, sender=…)`；审计只记字节数与目的说明。
  - `T-L0-004`——`CredentialVault.use(credential_id, initiator, purpose)`（搜索密钥明文唯一出口）。
  - `T-UI-018.1`——`setting_panel` 与统一落值面（读 / 改 `search.provider`）。
- 输出（本任务交付的公共 API / 落盘位置）：
  - `l0/net/web_config.py`——`SEARCH_PROVIDER_CONFIG_ID` / `SEARCH_KEY_CONFIG_ID` / `SEARCH_CREDENTIAL_ID`、`load_web_settings(store)`、`web_config_entries()`。
  - `l0/net/search.py`——`SEARCH_PROVIDERS`、`build_search_sender()`、`parse_search_response()`。
  - `l1/builtin/web.py`——`WEB_SEARCH`（`sk_web_search_v1.0`），登记进 `BUILTIN_PRIMITIVES`。
  - 能力声明 `net-search`（`l0/net/capabilities.py`）。
  - 出网类目 `web_search`（`l0/net/models.py` / `gateway.py`）。
  - 搜索密钥落 `secrets` 分区（凭据标识 `search-api-key`）。

## 可关闭的遗留
无（六册未闭区逐册核过，无「归属＝本任务」或「解封条件＝本任务 `done`」的条目；检查 8 = 0）。

## 假设与前提
- **A1** 提供方取 Brave + Tavily 两个 JSON REST 适配器；`search.provider` 未配置即 `unavailable` 并给指引。若错的影响：适配器需按真实响应微调；返工集中在 `l0/net/search.py` 的解析函数。验证方式：注入 HTTP 替身的两个提供方归一化用例 + 错误响应用例。
- **A2** 密钥存 `CredentialVault`（`secrets` 分区恒加密），`search.key` 配置条目只承载注册与「已配置 / 未配置」状态。若错的影响：若要求密钥随配置导出，违反铁律 1，不取。验证方式：`SearchFamily` 落值写凭据库、读面不回显明文的用例。
- **A3** 审计目的说明只写「联网搜索（提供方 X）」，查询词不进 `purpose` / `initiator`，只以 `bytes_out` 计数。若错的影响：查询正文可能进入留痕，违反 02 §6 零明文。验证方式：真实网关 + 审计开启，检索记录中不含查询词。
- **A4** 搜索结果为空时返回 `ok` + `results=[]`（搜索本身成功），不伪造条目；响应形态不符即显式失败，不静默当空。验证方式：空结果与畸形响应两条用例。

## 涉及契约
- [01 §7](../../docs/技术架构-v2/01-平台共享契约.md) —— `search.provider` / `search.key`
- [02 §7](../../docs/技术架构-v2/02-L0-本地优先基座.md) —— `net-search` 能力声明

## 参考
- `l0/net/gateway.py`（唯一出网口）

## 备注
**数据主权**：搜索会把用户问题发给第三方。默认关、首次显式告知（由 [`T-AGT-015.3`] 承接）。
