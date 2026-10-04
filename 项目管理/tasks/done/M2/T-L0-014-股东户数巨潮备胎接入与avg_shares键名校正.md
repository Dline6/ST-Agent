---
id: T-L0-014
parent: null
title: 股东户数巨潮备胎接入与 avg_shares 键名校正
story: ../../docs/PRD-v2-Agent/story-05-local-first.md
arch: ../../docs/技术架构-v2/02-L0-本地优先基座.md
arch_link: "[02 §5](../../../../docs/技术架构-v2/02-L0-本地优先基座.md)"
priority: P1
milestone: M2
depends_on: [T-L0-010.4, T-L0-010.1]
status: done
decisions: [D-041]
verify: 2026-09-27 `[T-L0-014]` · 按范围 `pytest tests/contracts tests/test_layering.py tests/test_tools.py tests/l0` → **644 passed / 0 failed**（113.39s）· `pytest -m live tests/live/test_info_endpoints_live.py` → **1 passed**（真实端点单期 5173 行）· `verify_docs.py --strict` 检查 1–8 全 0 · **红-绿已验**：撤回 `AVG_FREE_SHARES` 键 → 键名用例 FAIL；`backup` 改走 `REPLACE` → 备胎让位用例 FAIL（999999 覆盖主源 120000）；去掉 `records` 守卫 → fail-fast 用例 FAIL
---

# T-L0-014 · 股东户数巨潮备胎接入与 avg_shares 键名校正

## 目标

闭掉 [L0 遗留册 `F6`](../../../遗留问题/L0-遗留问题.md)，并同批修一条相邻的实测缺陷：

1. **`F6` 接备胎**——[D-030](../../../决策日志.md) 把股东户数域定为「**单源（仅东财）、无备胎**」，[D-035](../../../决策日志.md) 复核发现巨潮也有该域源，故只把「无备胎」这一**前提**标记失效、把「采不采用」留给立项。本任务即该立项：接巨潮 `p_sysapi1034` 为 **`role=backup`**（`coverage=all`），东财由 `sole` 转 `primary`。
2. **`avg_shares` 恒 `NULL`**——东财解析件读的键 `AVG_FREE_SHARES` **在真实载荷中不存在**（实际字段为 `AVG_HOLD_NUM`），[`test_info_parsers.py:447`](../../../../tests/l0/test_info_parsers.py) 反把 `None` 当预期断言住（夹具同样缺键，故测不出来）。与 `F4`/`F5` **同族**（「列恒空而无人知」）。

**已实测的关键事实（2026-09-27 真实请求）**：

- **端点可用，GET 即可**：`GET https://webapi.cninfo.com.cn/api/sysapi/p_sysapi1034?rdate=<YYYYMMDD>` → HTTP 200 / `resultcode=200`；`rdate=20250630` **5173** 条、`20240930` **5110** 条、`20260630` **5253** 条（均为该报告期**全市场**）。**非**季末的 `rdate` 行为未测，实现只按季末迭代。
- **鉴权成本≈6 行**：`Accept-Enckey = base64(AES-128-CBC-PKCS7(明文=unix 秒, key=iv='1234567887654321'))`——固定密钥，**不需要** akshare 的 `py_mini_racer` JS 引擎；而 `cryptography` **已是本项目核心依赖**（[pyproject.toml](../../../../pyproject.toml)）。算法出自 [`akshare` `data/cninfo.js`](https://github.com/akfamily/akshare) 的 `getResCode1()`，本任务**取知识不取依赖**。
- **字段近 1:1**：`F001N`＝本期股东人数 / `F002N`＝上期 / `F003N`＝增幅% / `F004N`＝人均持股 / `ENDDATE`＝统计截止日 / `SECCODE`。`change_num` 由 `F001N - F002N` 推导。
- **两源同口径（逐票对照实测）**：同报告期 `2026-06-30` 取两源共同代码 **497** 只——`avg_shares` 与东财 `AVG_HOLD_NUM` **逐票完全一致**；`holder_num` **97.6% 一致**（12 只分歧，最大偏差 17%，如 `000488` 93718 vs 77810）。→ 可直接同表并列；分歧行由 `backup` 的 `INSERT OR IGNORE` **让位**给主源。
- **形状差异（决定主备方向）**：巨潮按**报告期**一次给全市场、起点 **2017Q1**，**能逐季回补历史**；东财 `RPT_HOLDERNUMLATEST` 是「**latest**」、**没有报告期参数**（按 `END_DATE` 过滤才可得指定期），故 [05](../../../../docs/数据库设计-BaoStock数据层/05-同步策略与新鲜度契约.md) 登记的「**每周重拉最近 8 个季度**」对东财**实际不成立**（只拿得到每票最新一期）——巨潮在此**不是单纯冗余，是实质互补**。

## 验收标准（Given-When-Then）

- **GWT-1 备胎接入**：Given 巨潮报告期全市场流，When 跑 `info_shareholder_num_cninfo`，Then 在窗口内的**各季末**逐期取数并写入 `shareholder_num`（`source_id` 为新登记的巨潮 API 源），**不覆盖**东财既有行。
- **GWT-2 补齐历史**：Given 东财只落每票**最新一期**、更早季末为空，When 备胎跑完，Then 窗口内东财**未覆盖**的 `(code, stat_date)` 由巨潮补齐，且东财已有行**逐字节不变**。
- **GWT-3 主备让位**：Given 两源同 `(code, stat_date)` **值不同**（实测 497 票中 12 票如此），When 落库，Then **东财值胜出**、巨潮行被丢弃；`info_shareholder_num_em` 的 `role` 由 `sole` 改 `primary`、新任务登记 `backup`。
- **GWT-4 `avg_shares` 键名校正**：Given 东财**真实载荷**，When `parse_shareholder_num_em`，Then `avg_shares` 取 `AVG_HOLD_NUM`（**不再恒 `None`**）；巨潮行同列取 `F004N`；两源该列**同口径**（总股本/户数）。
- **GWT-5 结构变更 fail-fast**：Given 巨潮载荷缺 `records` / 结构不符，When 抓取，Then **显式抛错**——不以空结果冒充「无数据」（沿用 T-L0-012 定下的 fail-fast 范式）。

## 接口面

- **输入（消费的前置接口）**：
  - `T-L0-010.4` [`fetch.py`](../../../../src/st_agent/l0/info/fetch.py)：`parse_shareholder_num_em`（本任务改其 `avg_shares` 取键）、`_fetch_info_shareholder_num_em`、`HttpInfoFetcher._request`（**当前无自定义 header 能力**，本任务补）、`_datacenter`
  - `T-L0-010.4` [`sources.py`](../../../../src/st_agent/l0/info/sources.py)：`InfoTask("info_shareholder_num_em", …, coverage="all", role="sole")`（`role` 改 `primary`）
  - `T-L0-010.1` [`sources.py`](../../../../src/st_agent/l0/info/sources.py)：`INFO_SOURCES` / `InfoSource(source_id, name, manual_ref, hosts)`（新增一项）；`hosts_of`（审计取首项主机）
  - `T-L0-010.1` [`sync.py`](../../../../src/st_agent/l0/info/sync.py)：`INFO_SOURCES` 播种 `data_source`（`INSERT OR IGNORE`）· `window_for`（`domain == "shareholder_num"` 分支已给 2 年窗口，**本任务不改**）· 角色写入语义（`primary`→`INSERT OR REPLACE` / `backup`→`INSERT OR IGNORE`）
  - `T-L0-004` [`l0/net/gateway.py`](../../../../src/st_agent/l0/net/gateway.py)：`execute(...)` 抓取唯一出口（本任务不新增出口）
- **输出（本任务交付的公共 API 与落盘位置）**：
  - 源：`InfoSource("<新 id>", "巨潮资讯网（数据中心 API）", …, ("webapi.cninfo.com.cn",))`——**与新登记主机不同的既有 `cninfo` 源分列**（既例：`cninfo` / `cninfo_irm`）
  - 任务：`InfoTask("info_shareholder_num_cninfo", "shareholder_num", "shareholder_num", <新 id>, "upsert_window", …, "all", "backup", watermark_desc="已覆盖的最近统计截止日")`
  - 解析件：`parse_shareholder_num_cninfo(payload) -> tuple[dict, ...]`（**新写**，纯函数、离线可测）
  - 抓取：`_fetch_info_shareholder_num_cninfo`（Enckey 计算 + 窗口内季末迭代）
  - `_request` 的**自定义 header** 能力（`Accept-Enckey`）
  - 落盘：`shareholder_num` 表**结构不变**（仅 `source_id` 取值与列语义注释）；`data_source` 播种项由 `INFO_SOURCES` 派生
  - 消费面：[`T-L4-001`](T-L4-001-视角模型Lens常设阵容用户可增删.md) / [`T-L4-002`](T-L4-002-多视角执行编排.md)（筹码集中度的支撑面）

## 可关闭的遗留

- **`F6`（本批关闭）**：[L0 册 F 段](../../../遗留问题/L0-遗留问题.md) ——「股东户数『无备胎』判定不再成立」。处置：GWT-1/2/3 接入巨潮作备胎 + `role` 校正 + 修订 [05](../../../../docs/数据库设计-BaoStock数据层/05-同步策略与新鲜度契约.md)；收工时销账。**就地修正其「归属 / 解封条件」**：归属由「待人定（待立项）」改为「`T-L0-014`」；解封条件由「人定是否采用该源并立项」改为「`T-L0-014` `done`」。
- **`F7`（本批新登记 + 关闭）**：`shareholder_num.avg_shares` 在东财源**恒 `NULL`**（解析件读 `AVG_FREE_SHARES`，真实键为 `AVG_HOLD_NUM`），而测试把 `None` 当预期断言住。处置：GWT-4 键名校正 + 夹具按真实载荷校正。收工时从 F 段未闭区移入「已闭（备查）」，并写执行日志留痕。
- **其余册内条目**（L0 册 `A1b` / `A4` / `A5` / `C1` / `C2` / `D1`–`D3` / `F1`（深市部分）；[L1 册](../../../遗留问题/L1-遗留问题.md) 未闭区）的归属与解封条件**均非本任务** → 无命中。

## 假设与前提

- **A1** 巨潮 `Accept-Enckey` 的算法为**固定密钥 AES-128-CBC-PKCS7(unix 秒)**，且服务端在时效窗口内接受 / 若错（密钥轮换或窗口收紧）：该源请求全败 → 备胎退化为不可用（**有**主源兜底，且失败经 `sync_state` 显式可见，不静默）/ 验证：2026-09-27 实测 3 次（GET + POST、两个报告期）全 200 且 `resultcode=200`；实现期以 `tests/live/` 用例可复跑。
- **A2** `rdate` 只接受**季末**（0331/0630/0930/1231）/ 若错（支持任意日期）：窗口内迭代粒度可更细（增益，不阻塞）/ 验证：实测三个季末均成功；**非季末取值未测**，故实现只按季末迭代，不依赖非季末行为。
- **A3** 两源**同口径**、可同表并列 / 若错（口径不同源）：备胎行与主源行语义不一致，消费方会读到混合口径 / 验证：实测同报告期 497 票对照——`avg_shares` **逐票完全一致**、`holder_num` **97.6% 一致**（分歧 12 票、最大 17%）；因取 `backup`（`IGNORE`）语义，分歧行**不覆盖**主源，风险被限制在「主源缺失」的行。
- **A4** 备胎行**写一次不再更新**（`IGNORE` 对自身历史行亦不覆盖）——巨潮侧修正**不回流** / 若错（需修正回流）：须改 `role` 语义或另开修正通道 / 验证：GWT-3 断言同 `(code, stat_date)` 二次拉取不改变既有行。
- **A5** 新增一个源只需在 `INFO_SOURCES` 加一项（`data_source` 由 `INSERT OR IGNORE` 播种），**不改 DDL** / 若错（须改表或加列）：DB 迁移面扩大、`T-L0-005` 的建库面返工 / 验证：GWT-1 在临时库上建表播种后 `PRAGMA foreign_key_check` 为空。
- **A6** 备胎的**回补horizon**取本域已登记的「最近 8 个季度」（沿用 `window_for` 的 2 年窗口，**不新增参数**）；更早历史（巨潮可回溯至 2017Q1）**不在本任务** / 若错（消费方需要更深历史）：属对 [05](../../../../docs/数据库设计-BaoStock数据层/05-同步策略与新鲜度契约.md) 水位口径的修订，另立任务 / 验证：GWT-2 只断言窗口内季末被补齐。

## 涉及契约

- [05 同步策略与新鲜度契约](../../../../docs/数据库设计-BaoStock数据层/05-同步策略与新鲜度契约.md) —— `info_shareholder_num_em` 行的**角色**由「单源」改「主」；**新增** `info_shareholder_num_cninfo` 行；「真实端点核验」段补记本条实测
- [02-L0 §5](../../../../docs/技术架构-v2/02-L0-本地优先基座.md) 数据源缓存子系统 —— 股东户数域由「单源」改「主备」
- [数据库设计 00 §多源扩展规约 §2](../../../../docs/数据库设计-BaoStock数据层/00-设计总览.md) —— **复核后无需改动**：该节已泛化声明「域内多源 → 行级 `source_id`、每个源各登记一条任务、主备 / 覆盖分片 / 并行互补三态」（股东户数由单源改主备即该条的一个实例）
- [schema.sql](../../../../docs/数据库设计-BaoStock数据层/schema.sql) —— `shareholder_num.avg_shares` 的列注释补口径（总股本/户数；两源同口径）
- [`T-INT-003`](T-INT-003-M2集成关卡多视角决策闭环.md) 的 `depends_on` —— **须补** `T-L0-014`（`verify_docs.py` 检查 7）
- [`D-030`](../../../决策日志.md) 的该域**事实陈述**由 [`D-041`](../../../决策日志.md) **部分替代**（沿 [D-035](../../../决策日志.md) 的做法；该条其余域的主备判定与「逐域定」的方法论不受影响）

## 参考

- Story（What）：[local-first](../../../../docs/PRD-v2-Agent/story-05-local-first.md) · [multi-lens](../../../../docs/PRD-v2-Agent/story-04-multi-lens.md)（筹码集中度）
- 来源遗留：[L0 册 `F6`](../../../遗留问题/L0-遗留问题.md)（[D-035](../../../决策日志.md) 登记）· 本任务新登记 `F7`
- 前序任务：[`T-L0-010.4`](T-L0-010.4-股东变化域采集与管理.md)（股东变化域）· [`T-L0-010.1`](T-L0-010.1-信息面基件多源映射文本承载文档取数面主档预过滤.md)（信息面基件）· [`T-L0-012`](T-L0-012-龙虎榜席位明细补齐与沪市官方源接入.md)（fail-fast 范式来源）
- 决策：[`D-029`](../../../决策日志.md)（通道归 L0 直抓）· [`D-030`](../../../决策日志.md)（业务键级单一事实源 + 逐域主备）· [`D-032`](../../../决策日志.md)（覆盖显式化）
- 外部信源参考（**取知识不取依赖**）：[`akfamily/akshare`](https://github.com/akfamily/akshare) `stock_hold_num_cninfo.py` + `data/cninfo.js`（端点与 Enckey 算法出处，**经本任务实测校正**）

## 备注

选型（巨潮取 `backup` 而非 `primary`、`avg_shares` 一并修、回补 horizon 取 8 季）记 [决策日志 `D-041`](../../../决策日志.md)。实现细节不写此处，留给代码 / commit / 执行日志。
