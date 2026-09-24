-- =====================================================================
-- ST Agent 本地市场数据库 — BaoStock 数据层 Schema（SQLite）
-- 依据：《数据库设计-BaoStock数据层》文档族（README.md 为索引）
-- 约定：仅落不复权原始行情（adjustflag=3），复权价经视图推导；空串一律清洗为 NULL
-- =====================================================================

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------
-- 元数据层
-- ---------------------------------------------------------------------

-- 数据源注册。BaoStock 为首个源；后续源（公告/龙虎榜/舆情）在此登记
CREATE TABLE data_source (
  source_id  TEXT PRIMARY KEY,          -- 'baostock'
  name       TEXT NOT NULL,
  manual_ref TEXT                       -- 源文档位置（本地路径或 URL）
);

-- 同步任务状态与水位。每个同步任务一行，Agent 据此判断数据新鲜度
CREATE TABLE sync_state (
  task_key        TEXT PRIMARY KEY,     -- 如 'bs_k_daily'
  table_name      TEXT NOT NULL,        -- 目标表
  source_id       TEXT NOT NULL REFERENCES data_source(source_id),
  mode            TEXT NOT NULL CHECK (mode IN ('full','incremental','snapshot','upsert_window')),
  schedule_desc   TEXT NOT NULL,        -- 触发节奏的人类可读描述
  watermark       TEXT,                 -- 增量水位（表级，语义见 05-同步策略）
  last_success_at TEXT,                 -- 最近一次成功完成时间（UTC ISO-8601）
  last_row_count  INTEGER,              -- 最近一次同步影响行数
  last_status     TEXT CHECK (last_status IN ('ok','partial','failed')),
  last_error      TEXT
);

-- 指标字典。种子数据以 06-API映射契约.md 为单一事实源生成
CREATE TABLE indicator_dictionary (
  indicator_key   TEXT PRIMARY KEY,     -- 目标列名（全库唯一）
  domain          TEXT NOT NULL,        -- 行情/财务/公司报告/宏观
  source_api      TEXT NOT NULL,        -- 来源接口名
  api_field       TEXT NOT NULL,        -- 接口原始字段名
  name_cn         TEXT NOT NULL,
  unit            TEXT,                 -- %、元、股、次、天、亿元 等；无量纲为 NULL
  precision_note  TEXT,                 -- 精度说明（源数据精度）
  algorithm_desc  TEXT                  -- 算法说明（摘自手册）
);

-- ---------------------------------------------------------------------
-- 核心实体层（源无关，全局唯一事实）
-- ---------------------------------------------------------------------

-- 证券主档。code 为全库统一主键（交易所.六位代码），各数据源代码经映射后写入
CREATE TABLE security (
  code      TEXT PRIMARY KEY,           -- 'sh.600000' / 'sz.000001' / 指数代码
  code_name TEXT,
  ipo_date  TEXT,                       -- 'YYYY-MM-DD'
  out_date  TEXT,                       -- 退市日期，未退市为 NULL
  type      INTEGER CHECK (type IN (1,2,3,4,5)),  -- 1股票 2指数 3其它 4可转债 5ETF
  status    INTEGER CHECK (status IN (0,1))       -- 1上市 0退市
);
CREATE INDEX idx_security_status ON security(status);

-- 交易日历（上交所口径，1990 年至今）
CREATE TABLE trade_calendar (
  calendar_date  TEXT PRIMARY KEY,      -- 'YYYY-MM-DD'
  is_trading_day INTEGER NOT NULL CHECK (is_trading_day IN (0,1))
);

-- ---------------------------------------------------------------------
-- 板块域
-- ---------------------------------------------------------------------

-- 行业分类快照（BaoStock 每周一更新，保留历史快照）
CREATE TABLE stock_industry (
  code                    TEXT NOT NULL REFERENCES security(code),
  update_date             TEXT NOT NULL,
  code_name               TEXT,         -- 快照时的证券名称
  industry                TEXT,         -- 所属行业（如 '银行'）
  industry_classification TEXT,         -- 如 '申万一级行业'
  PRIMARY KEY (code, update_date)
);

-- 指数成分快照（上证50/沪深300/中证500，每周一更新，保留历史快照）
CREATE TABLE index_constituent (
  index_key   TEXT NOT NULL CHECK (index_key IN ('sz50','hs300','zz500')),
  code        TEXT NOT NULL REFERENCES security(code),
  update_date TEXT NOT NULL,
  code_name   TEXT,
  PRIMARY KEY (index_key, code, update_date)
);
CREATE INDEX idx_index_constituent_date ON index_constituent(index_key, update_date);

-- ---------------------------------------------------------------------
-- 行情域
-- ---------------------------------------------------------------------

-- 日线行情（不复权）。含停牌行（开高低收=前收、量额为0、换手率为 NULL）
CREATE TABLE k_line_daily (
  code         TEXT NOT NULL REFERENCES security(code),
  trade_date   TEXT NOT NULL,
  open         REAL,                    -- 精度4位，元
  high         REAL,
  low          REAL,
  close        REAL,
  preclose     REAL,                    -- 除权除息日为交易所公布的理论前收
  volume       INTEGER,                 -- 股
  amount       REAL,                    -- 元
  turn         REAL,                    -- %，精度6位；停牌为 NULL
  trade_status INTEGER CHECK (trade_status IN (0,1)),  -- 1正常 0停牌
  pct_chg      REAL,                    -- %，精度6位
  pe_ttm       REAL,                    -- 滚动市盈率
  pb_mrq       REAL,                    -- 市净率
  ps_ttm       REAL,                    -- 滚动市销率
  pcf_ncf_ttm  REAL,                    -- 滚动市现率
  is_st        INTEGER CHECK (is_st IN (0,1)),
  PRIMARY KEY (code, trade_date)
);
CREATE INDEX idx_k_line_daily_date ON k_line_daily(trade_date);

-- 周/月线行情（不复权）。周线每周最后交易日、月线每月最后交易日各产生一行
CREATE TABLE k_line_period (
  code       TEXT NOT NULL REFERENCES security(code),
  frequency  TEXT NOT NULL CHECK (frequency IN ('w','m')),
  trade_date TEXT NOT NULL,             -- 区间最后交易日
  open       REAL,
  high       REAL,
  low        REAL,
  close      REAL,
  volume     INTEGER,
  amount     REAL,
  turn       REAL,
  pct_chg    REAL,                      -- 相对区间首个交易日前收盘的涨跌幅
  PRIMARY KEY (code, frequency, trade_date)
);

-- 分钟线行情（不复权，可选域：仅关注池证券按需拉取；指数无分钟线）
CREATE TABLE k_line_minute (
  code      TEXT NOT NULL REFERENCES security(code),
  frequency TEXT NOT NULL CHECK (frequency IN ('5','15','30','60')),
  bar_start TEXT NOT NULL,              -- 'YYYY-MM-DD HH:MM:SS'（API time 字段归一化）
  open      REAL,
  high      REAL,
  low       REAL,
  close     REAL,
  volume    INTEGER,                    -- 该周期内累计
  amount    REAL,
  PRIMARY KEY (code, frequency, bar_start)
);

-- 复权因子（每个除权除息日一行）
CREATE TABLE adjust_factor (
  code              TEXT NOT NULL REFERENCES security(code),
  ex_date           TEXT NOT NULL,      -- 除权除息日期（dividOperateDate）
  fore_adjust_factor REAL NOT NULL,     -- 向前复权因子
  back_adjust_factor REAL NOT NULL,     -- 向后复权因子
  adjust_factor     REAL NOT NULL,      -- 本次复权因子
  PRIMARY KEY (code, ex_date)
);

-- 除权除息信息。ex_date 可能为 NULL（未实施/未公告完整），故用代理主键 + 业务唯一索引
CREATE TABLE dividend (
  dividend_id              INTEGER PRIMARY KEY,
  code                     TEXT NOT NULL REFERENCES security(code),
  divid_pre_notice_date    TEXT,        -- 预披露公告日
  divid_agm_pum_date       TEXT,        -- 股东大会公告日
  divid_plan_announce_date TEXT,        -- 预案公告日
  divid_plan_date          TEXT,        -- 分红实施公告日
  divid_regist_date        TEXT,        -- 股权登记日
  divid_operate_date       TEXT,        -- 除权除息日
  divid_pay_date           TEXT,        -- 派息日
  divid_stock_market_date  TEXT,        -- 红股上市交易日
  divid_cash_ps_before_tax REAL,        -- 每股股利税前，元
  divid_cash_ps_after_tax  REAL,        -- 每股股利税后，元（含"或"多值时取第一个数值）
  divid_stocks_ps          REAL,        -- 每股红股
  divid_cash_stock         TEXT,        -- 分红送转描述原文（如 '10转1派5.15元…'）
  divid_reserve_to_stock_ps REAL        -- 每股转增资本
);
CREATE UNIQUE INDEX uq_dividend ON dividend(code, divid_operate_date);

-- ---------------------------------------------------------------------
-- 财务域（六接口合并为宽表，按 (code, stat_date) 对齐）
-- ---------------------------------------------------------------------

CREATE TABLE financial_quarter (
  code     TEXT NOT NULL REFERENCES security(code),
  stat_date TEXT NOT NULL,              -- 财报统计季度末日 'YYYY-MM-DD'
  pub_date  TEXT,                       -- 六接口 pubDate 的最大值（最新披露）
  -- 盈利能力 query_profit_data()
  roe_avg       REAL,                   -- 净资产收益率(平均) %
  np_margin     REAL,                   -- 销售净利率 %
  gp_margin     REAL,                   -- 销售毛利率 %
  net_profit    REAL,                   -- 净利润，元
  eps_ttm       REAL,                   -- 每股收益 TTM
  mb_revenue    REAL,                   -- 主营营业收入，元
  total_share   REAL,                   -- 总股本
  liqa_share    REAL,                   -- 流通股本
  -- 营运能力 query_operation_data()
  nr_turn_ratio   REAL,                 -- 应收账款周转率，次
  nr_turn_days    REAL,                 -- 应收账款周转天数，天
  inv_turn_ratio  REAL,                 -- 存货周转率，次
  inv_turn_days   REAL,                 -- 存货周转天数，天
  ca_turn_ratio   REAL,                 -- 流动资产周转率，次
  asset_turn_ratio REAL,                -- 总资产周转率
  -- 成长能力 query_growth_data()
  yoy_equity   REAL,                    -- 净资产同比增长率
  yoy_asset    REAL,                    -- 总资产同比增长率
  yoy_ni       REAL,                    -- 净利润同比增长率
  yoy_eps_basic REAL,                   -- 基本每股收益同比增长率
  yoy_pni      REAL,                    -- 归母净利润同比增长率
  -- 偿债能力 query_balance_data()
  current_ratio     REAL,               -- 流动比率
  quick_ratio       REAL,               -- 速动比率
  cash_ratio        REAL,               -- 现金比率
  yoy_liability     REAL,               -- 总负债同比增长率
  liability_to_asset REAL,              -- 资产负债率
  asset_to_equity   REAL,               -- 权益乘数
  -- 现金流量 query_cash_flow_data()
  ca_to_asset          REAL,            -- 流动资产/总资产
  nca_to_asset         REAL,            -- 非流动资产/总资产
  tangible_asset_to_asset REAL,         -- 有形资产/总资产
  ebit_to_interest     REAL,            -- 已获利息倍数
  cfo_to_or            REAL,            -- 经营现金流净额/营业收入
  cfo_to_np            REAL,            -- 经营性现金净流量/净利润
  cfo_to_gr            REAL,            -- 经营性现金净流量/营业总收入
  -- 杜邦指数 query_dupont_data()
  dupont_roe              REAL,         -- 净资产收益率
  dupont_asset_sto_equity REAL,         -- 权益乘数
  dupont_asset_turn       REAL,         -- 总资产周转率
  dupont_pnitoni          REAL,         -- 归母净利润/净利润
  dupont_nitogr           REAL,         -- 净利润/营业总收入
  dupont_tax_burden       REAL,         -- 净利润/利润总额
  dupont_intburden        REAL,         -- 利润总额/息税前利润
  dupont_ebittogr         REAL,         -- 息税前利润/营业总收入
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (code, stat_date)
);

-- ---------------------------------------------------------------------
-- 公司报告域
-- ---------------------------------------------------------------------

-- 业绩快报（同一 stat_date 可能多次更新，以最新一次覆盖）
CREATE TABLE performance_express (
  code      TEXT NOT NULL REFERENCES security(code),
  stat_date TEXT NOT NULL,              -- 业绩快报统计日期
  pub_date  TEXT,                       -- 披露日
  update_date TEXT,                     -- 最新披露日
  total_asset REAL,                     -- 总资产，元
  net_asset   REAL,                     -- 净资产，元
  eps_chg_pct REAL,                     -- 每股收益增长率
  roe_wa      REAL,                     -- 加权 ROE
  eps_diluted REAL,                     -- 摊薄 EPS
  gr_yoy      REAL,                     -- 营业总收入同比
  op_yoy      REAL,                     -- 营业利润同比
  PRIMARY KEY (code, stat_date)
);

-- 业绩预告
CREATE TABLE profit_forecast (
  code         TEXT NOT NULL REFERENCES security(code),
  stat_date    TEXT NOT NULL,           -- 业绩预告统计日期
  pub_date     TEXT,                    -- 发布日期
  forecast_type TEXT,                   -- 如 '略增'/'预增'/'预亏'/'扭亏' 等
  abstract     TEXT,                    -- 摘要原文
  chg_pct_up   REAL,                    -- 归母净利润增长上限 %
  chg_pct_dwn  REAL,                    -- 归母净利润增长下限 %
  PRIMARY KEY (code, stat_date)
);

-- ---------------------------------------------------------------------
-- 宏观域
-- ---------------------------------------------------------------------

-- 存款利率（活期 + 定期 + 零存整取，单位 %）
CREATE TABLE macro_deposit_rate (
  pub_date       TEXT PRIMARY KEY,      -- 发布日期
  demand         REAL,                  -- 活期
  fixed_3m       REAL, fixed_6m REAL, fixed_1y REAL,
  fixed_2y       REAL, fixed_3y REAL, fixed_5y REAL,
  installment_1y REAL, installment_3y REAL, installment_5y REAL
);

-- 贷款利率（含住房公积金贷款，单位 %）
CREATE TABLE macro_loan_rate (
  pub_date         TEXT PRIMARY KEY,
  loan_6m          REAL,                -- 6个月
  loan_6m_1y       REAL,                -- 6个月至1年
  loan_1y_3y       REAL, loan_3y_5y REAL, loan_above_5y REAL,
  mortgage_below_5y REAL, mortgage_above_5y REAL
);

-- 存款准备金率（单位 %）
CREATE TABLE macro_reserve_ratio (
  pub_date       TEXT PRIMARY KEY,      -- 公告日期
  effective_date TEXT,                  -- 生效日期
  big_pre        REAL, big_after   REAL,   -- 大型机构 调整前/后
  medium_pre     REAL, medium_after REAL    -- 中小型机构 调整前/后
);

-- 货币供应量（月度，单位 亿元）
CREATE TABLE macro_money_supply_month (
  stat_year  INTEGER NOT NULL,
  stat_month INTEGER NOT NULL CHECK (stat_month BETWEEN 1 AND 12),
  m0 REAL, m0_yoy REAL, m0_chain REAL,  -- 月度值/同比/环比
  m1 REAL, m1_yoy REAL, m1_chain REAL,
  m2 REAL, m2_yoy REAL, m2_chain REAL,
  PRIMARY KEY (stat_year, stat_month)
);

-- 货币供应量（年底余额，单位 亿元）
CREATE TABLE macro_money_supply_year (
  stat_year INTEGER PRIMARY KEY,
  m0 REAL, m0_yoy REAL,
  m1 REAL, m1_yoy REAL,
  m2 REAL, m2_yoy REAL
);

-- ---------------------------------------------------------------------
-- 视图（复权推导 + 常用便捷视图）
-- 涨跌幅复权法：后复权价 = 不复权价 × Π(除权日≤当日的 back_adjust_factor)
--              前复权价 = 不复权价 × Π(除权日>当日的 fore_adjust_factor)
-- 该推导保证复权后 pct_chg 与原始涨跌幅一致（BaoStock 复权口径）
-- 依赖 SQLite 数学函数 LN/EXP（3.35+ 默认启用）
-- ---------------------------------------------------------------------

CREATE VIEW v_k_line_daily_hfq AS
SELECT
  k.code, k.trade_date,
  k.open  * COALESCE((SELECT EXP(SUM(LN(af.back_adjust_factor))) FROM adjust_factor af
                      WHERE af.code = k.code AND af.ex_date <= k.trade_date), 1.0) AS open,
  k.high  * COALESCE((SELECT EXP(SUM(LN(af.back_adjust_factor))) FROM adjust_factor af
                      WHERE af.code = k.code AND af.ex_date <= k.trade_date), 1.0) AS high,
  k.low   * COALESCE((SELECT EXP(SUM(LN(af.back_adjust_factor))) FROM adjust_factor af
                      WHERE af.code = k.code AND af.ex_date <= k.trade_date), 1.0) AS low,
  k.close * COALESCE((SELECT EXP(SUM(LN(af.back_adjust_factor))) FROM adjust_factor af
                      WHERE af.code = k.code AND af.ex_date <= k.trade_date), 1.0) AS close,
  k.preclose, k.volume, k.amount, k.turn, k.trade_status,
  k.pct_chg, k.pe_ttm, k.pb_mrq, k.ps_ttm, k.pcf_ncf_ttm, k.is_st
FROM k_line_daily k;

CREATE VIEW v_k_line_daily_qfq AS
SELECT
  k.code, k.trade_date,
  k.open  * COALESCE((SELECT EXP(SUM(LN(af.fore_adjust_factor))) FROM adjust_factor af
                      WHERE af.code = k.code AND af.ex_date > k.trade_date), 1.0) AS open,
  k.high  * COALESCE((SELECT EXP(SUM(LN(af.fore_adjust_factor))) FROM adjust_factor af
                      WHERE af.code = k.code AND af.ex_date > k.trade_date), 1.0) AS high,
  k.low   * COALESCE((SELECT EXP(SUM(LN(af.fore_adjust_factor))) FROM adjust_factor af
                      WHERE af.code = k.code AND af.ex_date > k.trade_date), 1.0) AS low,
  k.close * COALESCE((SELECT EXP(SUM(LN(af.fore_adjust_factor))) FROM adjust_factor af
                      WHERE af.code = k.code AND af.ex_date > k.trade_date), 1.0) AS close,
  k.preclose, k.volume, k.amount, k.turn, k.trade_status,
  k.pct_chg, k.pe_ttm, k.pb_mrq, k.ps_ttm, k.pcf_ncf_ttm, k.is_st
FROM k_line_daily k;

-- 每只证券最新一根日线（含 is_st / 估值快照）
CREATE VIEW v_k_line_latest AS
SELECT * FROM (
  SELECT k.*, ROW_NUMBER() OVER (PARTITION BY code ORDER BY trade_date DESC) AS rn
  FROM k_line_daily k
) WHERE rn = 1;

-- 当前 ST 股票池（在市股票中最新日线标记 is_st=1）
CREATE VIEW v_st_universe AS
SELECT s.code, s.code_name, s.ipo_date, l.trade_date AS st_mark_date, l.close, l.pct_chg
FROM security s
JOIN v_k_line_latest l ON l.code = s.code
WHERE s.type = 1 AND s.status = 1 AND l.is_st = 1;
