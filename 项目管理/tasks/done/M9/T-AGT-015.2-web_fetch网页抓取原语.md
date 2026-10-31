---
id: T-AGT-015.2
parent: T-AGT-015
title: web_fetch（网页抓取原语）
story: ../../docs/PRD-v2-Agent/story-05-local-first.md
arch: ../../docs/技术架构-v2/02-L0-本地优先基座.md
arch_link: "[02 §6](../../../../docs/技术架构-v2/02-L0-本地优先基座.md)"
priority: P1
milestone: M9
depends_on: [T-AGT-015.1]
status: done
decisions: [D-137, D-147]
verify: web_fetch 仅名单内主机、正文抽文本、非文本 / 重定向显式拒绝；见执行日志 [T-AGT-015.2]（commit d1e4659）
---

# T-AGT-015.2 · web_fetch（网页抓取原语）

## 目标
允许域名单内的 GET，并把正文抽成文本。

## 验收标准（Given-When-Then）
- GWT-1：Given 域名单内 URL，When 抓取，Then 返回正文文本并标注来源。
- GWT-2：Given 名单外主机，Then 拦截。
- GWT-3：Given 非文本 / 二进制，Then 显式不支持，不返回乱码。

## 接口面
- 输入（消费的前置接口）：
  - `T-AGT-008.2` / `T-AGT-008.3`——内置原语注册与执行面（`install_builtins` / `SkillContext.gateway` / `.sandbox`）。
  - `T-L0-018.2`（D-073）——`EgressGateway.stream`（唯一出网口与审计点）。
  - `T-L1-001.5`——`host_in_scope`（域名 glob 匹配）与 `SkillSandbox.check_net`。
  - `T-AGT-015.1`——`l0/net/web_config.py` 的 `load_web_settings()`（出网开关与允许域名）。
- 输出（本任务交付的公共 API / 落盘位置）：
  - `l0/net/fetch.py`——`build_fetch_sender()`、`extract_html_text()`、`FetchedPayload`（自报线上字节）。
  - `l1/builtin/web.py`——`WEB_FETCH`（`sk_web_fetch_v1.0`），登记进 `BUILTIN_PRIMITIVES`。
  - 出网类目 `web_fetch`（`l0/net/models.py` / `gateway.py`）。
  - 能力声明 `web-fetch`（`l0/net/capabilities.py`）。

## 可关闭的遗留
无（六册未闭区逐册核过，无「归属＝本任务」或「解封条件＝本任务 `done`」的条目；检查 8 = 0）。

## 假设与前提
- **A1** 域名名单由 `web-egress/allowed-hosts`（逗号 / 换行分隔的 glob）给出，默认空＝全拒；原语声明 `net_access:*`，名单是运行期边界。若错的影响：名单需改为静态声明，`web_fetch` 只能访问固定站点。验证方式：名单内放行 / 名单外 `validation_failed` 且零发包的用例。
- **A2** 只接受文本类 `Content-Type`（`text/*`、`application/xhtml+xml`、`application/json`）；二进制 / 非文本 → `unavailable`，不做替换解码。若错的影响：会有乱码或误读二进制的风险。验证方式：各类型响应头用例与解码失败用例。
- **A3** 不自动跟随重定向（3xx 显式拒绝），避免绕过域名名单。若错的影响：若需跟随，必须逐跳复核名单。验证方式：302 响应用例。
- **A4** 响应体上限 2 MiB；超限显式失败，不截半截正文。若错的影响：大页面不可抓，但不会拖垮进程。验证方式：超限用例。
- **A5** 正文是不可信数据：只返回纯文本 + 来源 URL + `fetched_at`，不执行页面中的指令。若错的影响：提示注入面。验证方式：原语描述与载荷字段断言。

## 涉及契约
- [02 §6](../../../../docs/技术架构-v2/02-L0-本地优先基座.md) —— 出网审计与域名单

## 参考
- `l1/sandbox/scopes.py`（`net_access` 主机模式匹配）

## 备注
网页正文是**不可信数据**：进入上下文前须标注来源，且不得被当作指令执行。
