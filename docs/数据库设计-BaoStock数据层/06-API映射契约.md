---
priority: P0
depends_on: [01-核心实体层.md, 02-行情数据层.md, 03-财务与公司报告层.md, 04-板块与宏观层.md]
consumed_by: [数据管道实现, 05-同步策略与新鲜度契约.md, 07-典型查询模式.md]
---

# 06 API 映射契约

本文档逐接口定义 BaoStock API → 数据库表的映射，是**字段语义的单一事实源**（`indicator_dictionary` 种子数据由此生成）。全局清洗规则（空串→NULL、`—`负号、`或`多值）见 [00-设计总览.md](00-设计总览.md)，下表仅列接口特有规则。

源手册：[BaoStock-API手册](../BaoStock-API手册/README.md)。

## 通用约定

- BaoStock 所有接口返回字符串；数值列落库前转 `REAL`/`INTEGER`，日期归一为 `YYYY-MM-DD`。
- 所有拉取在 `login()` … `logout()` 会话内进行，管道统一管理连接生命周期。
- UPSERT 一律基于目标表主键 / 唯一索引 `ON CONFLICT DO UPDATE`。

## 元信息类

### query_trade_dates() → trade_calendar

手册：[10-获取证券元信息](../BaoStock-API手册/10-获取证券元信息.md)。任务 `bs_calendar`，全量。

| API 字段 | 目标列 | 说明 |
| --- | --- | --- |
| calendar_date | trade_calendar.calendar_date | |
| is_trading_day | trade_calendar.is_trading_day | 字符串 '0'/'1' 转 INTEGER |

### query_stock_basic() → security

手册：[09-证券基本资料](../BaoStock-API手册/09-证券基本资料.md)。任务 `bs_security_basic`，全量 UPSERT。

| API 字段 | 目标列 | 说明 |
| --- | --- | --- |
| code | security.code | 主键 |
| code_name | security.code_name | |
| ipoDate | security.ipo_date | |
| outDate | security.out_date | 空串→NULL |
| type | security.type | '1'~'5' 转 INTEGER |
| status | security.status | '1'/'0' 转 INTEGER |

### query_all_stock(day) → security（增量发现）

手册：[10-获取证券元信息](../BaoStock-API手册/10-获取证券元信息.md)。任务 `bs_all_stock`，每交易日。用途：**发现新上市证券**（对 security UPSERT）与**行情覆盖对账**（当日源证券总数记入 `sync_state.last_row_count`，供完整性校验 1 使用）。`tradeStatus`/`code_name` 字段为当日快照，不落库（历史快照由 `k_line_daily.trade_status` 承载）。

## 行情类

### query_history_k_data_plus() → k_line_daily / k_line_period / k_line_minute

手册：[04-获取历史A股K线数据](../BaoStock-API手册/04-获取历史A股K线数据.md)。任务 `bs_k_daily` / `bs_k_period` / `bs_k_minute`。

**固定拉取参数**：`adjustflag='3'`（不复权，全库唯一存储口径）；`frequency` 按目标表取 'd' / 'w' / 'm' / '5' / '15' / '30' / '60'；`fields` 取目标表对应全集。

| API 字段 | k_line_daily | k_line_period | k_line_minute | 说明 |
| --- | --- | --- | --- | --- |
| date | trade_date | trade_date | — | |
| time | — | — | bar_start | `YYYYMMDDHHMMSSsss` → `YYYY-MM-DD HH:MM:SS`（截去毫秒） |
| code | code | code | code | |
| open / high / low / close | 同名 | 同名 | 同名 | 精度 4 位 |
| preclose | preclose | — | — | 除权日≠前日 close，正确行为 |
| volume | volume | volume | volume | INTEGER，股 |
| amount | amount | amount | amount | 元 |
| adjustflag | — | — | — | 恒 '3'，不落库 |
| turn | turn | turn | — | 停牌空串→NULL |
| tradestatus | trade_status | — | — | '1'/'0' 转 INTEGER |
| pctChg | pct_chg | pct_chg | — | 日线口径 vs 区间口径，见 02 文档 |
| peTTM / pbMRQ / psTTM / pcfNcfTTM | 同名 snake_case | — | — | 指数行为空→NULL |
| isST | is_st | — | — | '1'/'0' 转 INTEGER |

**接口特定规则**：指数无分钟线（拉取侧按 `security.type=2` 排除分钟任务）；停牌日行的存在性保留（见 [02-行情数据层.md](02-行情数据层.md) 契约）。

### query_adjust_factor() → adjust_factor

手册：[06-查询复权因子信息](../BaoStock-API手册/06-查询复权因子信息.md)。任务 `bs_adjust_factor`，upsert_window。

| API 字段 | 目标列 | 说明 |
| --- | --- | --- |
| code | adjust_factor.code | |
| dividOperateDate | adjust_factor.ex_date | 主键 |
| foreAdjustFactor | fore_adjust_factor | |
| backAdjustFactor | back_adjust_factor | |
| adjustFactor | adjust_factor | |

### query_dividend_data() → dividend

手册：[05-查询除权除息信息](../BaoStock-API手册/05-查询除权除息信息.md)。任务 `bs_dividend`，按 `year`（当年+前 1 年）× `yearType='report'` 拉取，UPSERT 冲突键为唯一索引 `(code, divid_operate_date)`；`divid_operate_date` 为 NULL 的行**先查后插**（无冲突键，按已有行的 `(code, divid_plan_announce_date)` 比对去重）。

| API 字段 | 目标列 | 说明 |
| --- | --- | --- |
| code | dividend.code | |
| dividPreNoticeDate | divid_pre_notice_date | 空串→NULL，下同 |
| dividAgmPumDate | divid_agm_pum_date | |
| dividPlanAnnounceDate | divid_plan_announce_date | |
| dividPlanDate | divid_plan_date | |
| dividRegistDate | divid_regist_date | |
| dividOperateDate | divid_operate_date | 唯一索引列 |
| dividPayDate | divid_pay_date | |
| dividStockMarketDate | divid_stock_market_date | |
| dividCashPsBeforeTax | divid_cash_ps_before_tax | |
| dividCashPsAfterTax | divid_cash_ps_after_tax | **含「或」多值取第一个数值** |
| dividStocksPs | divid_stocks_ps | |
| dividCashStock | divid_cash_stock | 描述原文，TEXT 原样保留 |
| dividReserveToStockPs | divid_reserve_to_stock_ps | |

## 财务类（六接口 → financial_quarter 宽表）

手册：[07-查询季频财务数据信息](../BaoStock-API手册/07-查询季频财务数据信息.md)。任务 `bs_fin_quarter`，`year`+`quarter` 逐季拉取、按 `(code, stat_date)` 合并 UPSERT；`pub_date` 取六接口最大值。

| API 字段 | 目标列 | 中文语义 | 单位/说明 |
| --- | --- | --- | --- |
| pubDate | pub_date（合并） | 财报发布日期 | 六接口最大值 |
| statDate | stat_date | 财报统计季度末日 | 主键 |
| — query_profit_data() — | | | |
| roeAvg | roe_avg | 净资产收益率(平均) | % |
| npMargin | np_margin | 销售净利率 | % |
| gpMargin | gp_margin | 销售毛利率 | % |
| netProfit | net_profit | 净利润 | 元 |
| epsTTM | eps_ttm | 每股收益 TTM | |
| MBRevenue | mb_revenue | 主营营业收入 | 元 |
| totalShare | total_share | 总股本 | 股 |
| liqaShare | liqa_share | 流通股本 | 股 |
| — query_operation_data() — | | | |
| NRTurnRatio | nr_turn_ratio | 应收账款周转率 | 次 |
| NRTurnDays | nr_turn_days | 应收账款周转天数 | 天 |
| INVTurnRatio | inv_turn_ratio | 存货周转率 | 次 |
| INVTurnDays | inv_turn_days | 存货周转天数 | 天 |
| CATurnRatio | ca_turn_ratio | 流动资产周转率 | 次 |
| AssetTurnRatio | asset_turn_ratio | 总资产周转率 | |
| — query_growth_data() — | | | |
| YOYEquity | yoy_equity | 净资产同比增长率 | |
| YOYAsset | yoy_asset | 总资产同比增长率 | |
| YOYNI | yoy_ni | 净利润同比增长率 | |
| YOYEPSBasic | yoy_eps_basic | 基本每股收益同比增长率 | |
| YOYPNI | yoy_pni | 归母净利润同比增长率 | |
| — query_balance_data() — | | | |
| currentRatio | current_ratio | 流动比率 | |
| quickRatio | quick_ratio | 速动比率 | |
| cashRatio | cash_ratio | 现金比率 | |
| YOYLiability | yoy_liability | 总负债同比增长率 | |
| liabilityToAsset | liability_to_asset | 资产负债率 | |
| assetToEquity | asset_to_equity | 权益乘数 | |
| — query_cash_flow_data() — | | | |
| CAToAsset | ca_to_asset | 流动资产/总资产 | |
| NCAToAsset | nca_to_asset | 非流动资产/总资产 | |
| tangibleAssetToAsset | tangible_asset_to_asset | 有形资产/总资产 | |
| ebitToInterest | ebit_to_interest | 已获利息倍数 | 源存在 `—` 负号 |
| CFOToOR | cfo_to_or | 经营现金流/营业收入 | 源存在 `—` 负号 |
| CFOToNP | cfo_to_np | 经营现金净流量/净利润 | 源存在 `—` 负号 |
| CFOToGr | cfo_to_gr | 经营现金净流量/营业总收入 | 源存在 `—` 负号 |
| — query_dupont_data() — | | | |
| dupontROE | dupont_roe | 净资产收益率 | 与 roe_avg 同源算法 |
| dupontAssetStoEquity | dupont_asset_sto_equity | 权益乘数（杜邦） | |
| dupontAssetTurn | dupont_asset_turn | 总资产周转率（杜邦） | 与 asset_turn_ratio 同源算法 |
| dupontPnitoni | dupont_pnitoni | 归母净利润/净利润 | |
| dupontNitogr | dupont_nitogr | 净利润/营业总收入 | |
| dupontTaxBurden | dupont_tax_burden | 净利润/利润总额 | |
| dupontIntburden | dupont_intburden | 利润总额/息税前利润 | |
| dupontEbittogr | dupont_ebittogr | 息税前利润/营业总收入 | |

## 公司报告类

### query_performance_express_report() → performance_express

手册：[08-查询季频公司报告信息](../BaoStock-API手册/08-查询季频公司报告信息.md)。任务 `bs_perf_express`，按发布日期窗口拉取。

| API 字段 | 目标列 | 说明 |
| --- | --- | --- |
| code | code | |
| performanceExpPubDate | pub_date | |
| performanceExpStatDate | stat_date | 主键 |
| performanceExpUpdateDate | update_date | |
| performanceExpressTotalAsset | total_asset | 元 |
| performanceExpressNetAsset | net_asset | 元 |
| performanceExpressEPSChgPct | eps_chg_pct | |
| performanceExpressROEWa | roe_wa | |
| performanceExpressEPSDiluted | eps_diluted | |
| performanceExpressGRYOY | gr_yoy | |
| performanceExpressOPYOY | op_yoy | |

### query_forecast_report() → profit_forecast

手册：[08-查询季频公司报告信息](../BaoStock-API手册/08-查询季频公司报告信息.md)。任务 `bs_forecast`。

| API 字段 | 目标列 | 说明 |
| --- | --- | --- |
| code | code | |
| profitForcastExpPubDate | pub_date | |
| profitForcastExpStatDate | stat_date | 主键 |
| profitForcastType | forecast_type | 文本原样，不枚举归一 |
| profitForcastAbstract | abstract | 摘要原文 |
| profitForcastChgPctUp | chg_pct_up | % |
| profitForcastChgPctDwn | chg_pct_dwn | % |

## 板块类

### query_stock_industry() → stock_industry

手册：[12-板块数据](../BaoStock-API手册/12-板块数据.md)。任务 `bs_industry`，每周快照。

| API 字段 | 目标列 | 说明 |
| --- | --- | --- |
| updateDate | update_date | 主键成分 |
| code | code | 主键成分 |
| code_name | code_name | |
| industry | industry | 退市证券可为空 |
| industryClassification | industry_classification | 如 '申万一级行业' |

### query_sz50_stocks() / query_hs300_stocks() / query_zz500_stocks() → index_constituent

手册：[12-板块数据](../BaoStock-API手册/12-板块数据.md)。任务 `bs_sz50` / `bs_hs300` / `bs_zz500`，每周快照。

| API 字段 | 目标列 | 说明 |
| --- | --- | --- |
| updateDate | update_date | 主键成分 |
| code | code | 主键成分 |
| code_name | code_name | |
| （接口身份） | index_key | sz50 / hs300 / zz500 由任务写入，非 API 字段 |

## 宏观类

### query_deposit_rate_data() → macro_deposit_rate

手册：[11-宏观经济数据](../BaoStock-API手册/11-宏观经济数据.md)。

| API 字段 | 目标列 |
| --- | --- |
| pubDate | pub_date（PK） |
| demandDepositRate | demand |
| fixedDepositRate3Month / 6Month / 1Year / 2Year / 3Year / 5Year | fixed_3m / fixed_6m / fixed_1y / fixed_2y / fixed_3y / fixed_5y |
| installmentFixedDepositRate1Year / 3Year / 5Year | installment_1y / installment_3y / installment_5y |

### query_loan_rate_data() → macro_loan_rate

| API 字段 | 目标列 |
| --- | --- |
| pubDate | pub_date（PK） |
| loanRate6Month | loan_6m |
| loanRate6MonthTo1Year | loan_6m_1y |
| loanRate1YearTo3Year | loan_1y_3y |
| loanRate3YearTo5Year | loan_3y_5y |
| loanRateAbove5Year | loan_above_5y |
| mortgateRateBelow5Year | mortgage_below_5y |
| mortgateRateAbove5Year | mortgage_above_5y |

（源字段 `mortgate` 为 BaoStock 原文拼写，落库修正为 `mortgage`。）

### query_required_reserve_ratio_data() → macro_reserve_ratio

拉取固定 `yearType='0'`（公告日口径）。

| API 字段 | 目标列 |
| --- | --- |
| pubDate | pub_date（PK） |
| effectiveDate | effective_date |
| bigInstitutionsRatioPre / After | big_pre / big_after |
| mediumInstitutionsRatioPre / After | medium_pre / medium_after |

### query_money_supply_data_month() → macro_money_supply_month

| API 字段 | 目标列 | 说明 |
| --- | --- | --- |
| statYear / statMonth | stat_year / stat_month | PK，'01' 等两位月份转 INTEGER |
| m0Month / m0YOY / m0ChainRelative | m0 / m0_yoy / m0_chain | 同比环比存在 `—` 负号 |
| m1Month / m1YOY / m1ChainRelative | m1 / m1_yoy / m1_chain | |
| m2Month / m2YOY / m2ChainRelative | m2 / m2_yoy / m2_chain | |

### query_money_supply_data_year() → macro_money_supply_year

| API 字段 | 目标列 |
| --- | --- |
| statYear | stat_year（PK） |
| m0Year / m0YearYOY | m0 / m0_yoy |
| m1Year / m1YearYOY | m1 / m1_yoy |
| m2Year / m2YearYOY | m2 / m2_yoy |
