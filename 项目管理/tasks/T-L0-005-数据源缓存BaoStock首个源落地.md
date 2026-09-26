---
id: T-L0-005
title: 数据源缓存 + BaoStock 首个源落地
story: ../../docs/PRD-v2-Agent/story-05-local-first.md
arch_link: "[02 §5](../../docs/技术架构-v2/02-L0-本地优先基座.md) · [DB 族](../../docs/数据库设计-BaoStock数据层/README.md)"
priority: P0
milestone: M0
depends_on: [T-L0-001, T-L0-004]
status: done
decisions: []
verify: pytest 277/277（既有 238 + 市场 39）· 断链 0 · commits 待收工 · 日志 [T-L0-005]
---

# T-L0-005 · 数据源缓存 + BaoStock 首个源落地

## 对齐（④ · 2026-09-25 已确认「对，按此开工」）
- 交付：`data_cache` 分区内 SQLite 单库（schema.sql 全量表 + 视图）+ `sync_state`/`data_source` 元数据 + 新鲜度自检 + 抓取经网关 `data_fetch`
- 验收：见下 5 条 GWT（首次全量建库离线可查 / 增量水位续跑 / 开关走配置注册表 / 不可用走 `unavailable` + 标注最后更新 / 无第二套口径）
- 路径：`MarketDb` 经 `Store` 读写 `data_cache/market.db` 加密 blob（工作拷贝 + 写回）；fetcher 可注入（测试不碰真网）；清洗 / 复权 / 完整性按 DB 文档；快照锚定 `sync_state` 水位组合
- 假设：`baostock` 包不内置（协议注入）；分钟线默认禁用；开关状态不落库（调用方传入）

## 目标
数据源缓存 + BaoStock 首个源落地（开工 ④ 意图对齐时据 Story 补全）

## 验收标准（Given-When-Then，从 Story 抄）
- GWT-1 首次建库离线可查：Given 首次安装完成 Onboarding，When 执行全量同步，Then 本地 `data_cache/market.db` 含 schema.sql 全量表 + 视图（含元数据三表），之后断网仍可查询历史快照（02 §5「历史数据离线可查」）。
- GWT-2 增量同步与水位：Given 已建库，When 再次同步，Then 各任务按 05 文档水位语义续跑（`sync_state` 更新 `watermark`/`last_success_at`/`last_row_count`），时序前置（`bs_calendar` + `bs_all_stock` 先行）满足后才跑行情/财务任务。
- GWT-3 抓取经网关与清洗口径：Given 任意一次抓取，When 发起请求，Then 唯一出口为 `EgressGateway.execute(kind="data_fetch")`（`target_host="baostock"`）；入库清洗按 00 全局规则（空串→NULL、`—`负号、`或`多值取首值）+ 06 逐接口映射；只落 `adjustflag=3` 不复权行，复权经视图推导。
- GWT-4 新鲜度自检与降级：Given 上层读取数据，When 执行新鲜度自检，Then 以 05 文档自检 SQL + 陈旧判据为准：异常 → 走 `ResultEnvelope.unavailable` 并标注最后更新时间（`as_of` 按域取实际截止）；`dataset_snapshot_id` 锚定读取时的水位组合，不另立快照登记表（02 §5 三要点）。
- GWT-5 开关与完整性：Given 数据源开关状态由调用方传入（配置注册表职责，不落库），When 某源/任务被禁用，Then 该维度结果走 `unavailable` 并标注最后更新时间；每次同步后跑 05 文档 5 项完整性校验，失败记 `sync_state.last_status='partial'` + `last_error`，不回滚已写数据。

## 接口面
> 追补于 2026-09-26：本任务完成早于接口面机制落地，据已合入代码与执行日志回溯填写（非 ④ 对齐时填写）。
- 输入（消费的前置接口）：T-L0-001 `Store`（`data_cache` 分区）；T-L0-004 网关（`execute` + 按次 sender 注入）；数据库设计族 `schema.sql` 与 06 API 映射契约
- 输出（本任务交付的公共 API / 落盘位置）：
  - `MarketDb`：`.exists` / `.init_db` / `.tables` / `.views` / `.last_updated_at` / `.query`（只读白名单）/ `.freshness` / `.as_of` / `.snapshot_id` / `.connect` / `.transact` / `.check_readonly_sql`
  - `BaoStockSync`：`.setup` / `.run_task` / `.run_all` / `.freshness_verdict` / `.dataset_snapshot`；`RUN_ORDER`（全量执行序，2026-09-26 由 [T-L0-007.1](T-L0-007.1-脚本化全量同步与续跑语义.md) 追加为公开常量）；`Fetcher` 协议 + 18 个 `map_*` 映射函数；`TaskSpec` / `get_task` / `window_for` / `quarter_end` / `last_n_quarters` / `last_monday` / `next_day`
  - 清洗：`clean_str` / `clean_num` / `clean_int` / `normalize_date` / `normalize_minute_ts`；错误：`MarketValidationError` / `FetchError` / `FetchUnavailableError`
  - 落盘：`data_cache/market.db`（加密 blob，工作拷贝 + 提交后写回）
  - 消费方：T-L1-001.4（新鲜度查询）、T-L1-004 官方 Pack 行情面、T-L0-005.1 真实抓取器

## 涉及契约
[02 §5](../../docs/技术架构-v2/02-L0-本地优先基座.md) · [DB 族](../../docs/数据库设计-BaoStock数据层/README.md)

## 参考
- Story（What）：[local-first](../../docs/PRD-v2-Agent/story-05-local-first.md)

## 备注
实现细节不写此处，留给代码 / commit / 执行日志。
