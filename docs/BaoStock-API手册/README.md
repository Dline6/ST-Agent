# BaoStock Python API 手册（本地结构化版）

> 来源：<https://baostock.com/mainContent?file=pythonAPI.md>（爬取后拆分为按章节的本地文档）
> 接口包：`baostock`（Python），全部接口需先 `login()`、结束前 `logout()`。

## 使用说明

- 每个章节一个独立 Markdown 文件，链接均为相对路径，可离线跳转。
- 原文中的示例数据 XLSX、复权因子简介 PDF 已下载到 `assets/` 目录，正文链接指向本地文件。
- 正文文字与原网页一致，仅修正 Markdown 排版并把网页链接改成本地链接。

## 章节目录

| 章节 | 文件 | 内容 |
| --- | --- | --- |
| 1 入门示例 | [01-入门示例.md](01-入门示例.md) | HelloWorld |
| 2 登录 | [02-登录.md](02-登录.md) | login() |
| 3 登出 | [03-登出.md](03-登出.md) | logout() |
| 4 获取历史A股K线数据 | [04-获取历史A股K线数据.md](04-获取历史A股K线数据.md) | 获取历史A股K线数据：query_history_k_data_plus()、历史行情指标参数 |
| 5 查询除权除息信息 | [05-查询除权除息信息.md](05-查询除权除息信息.md) | 除权除息信息：query_dividend_data() |
| 6 查询复权因子信息 | [06-查询复权因子信息.md](06-查询复权因子信息.md) | 复权因子：query_adjust_factor() |
| 7 查询季频财务数据信息 | [07-查询季频财务数据信息.md](07-查询季频财务数据信息.md) | 季频盈利能力：query_profit_data()、季频营运能力：query_operation_data()、季频成长能力：query_growth_data()、季频偿债能力：query_balance_data()、季频现金流量：query_cash_flow_data()、季频杜邦指数：query_dupont_data() |
| 8 查询季频公司报告信息 | [08-查询季频公司报告信息.md](08-查询季频公司报告信息.md) | 季频公司业绩快报：query_performance_express_report()、季频公司业绩预告：query_forecast_report() |
| 9 证券基本资料 | [09-证券基本资料.md](09-证券基本资料.md) | 证券基本资料：query_stock_basic() |
| 10 获取证券元信息 | [10-获取证券元信息.md](10-获取证券元信息.md) | 交易日查询：query_trade_dates()、证券代码查询：query_all_stock() |
| 11 宏观经济数据 | [11-宏观经济数据.md](11-宏观经济数据.md) | 存款利率：query_deposit_rate_data()、贷款利率：query_loan_rate_data()、存款准备金率：query_required_reserve_ratio_data()、货币供应量：query_money_supply_data_month()、货币供应量(年底余额)：query_money_supply_data_year() |
| 12 板块数据 | [12-板块数据.md](12-板块数据.md) | 行业分类：query_stock_industry()、上证50成分股：query_sz50_stocks()、沪深300成分股：query_hs300_stocks()、中证500成分股：query_zz500_stocks() |
| 13 示例程序 | [13-示例程序.md](13-示例程序.md) | 获取指定日期全部股票的日K线数据 |

## 接口索引

| 接口 | 作用 | 所在章节 |
| --- | --- | --- |
| `login()` | 登录系统 | [02-登录.md](02-登录.md) |
| `logout()` | 登出系统 | [03-登出.md](03-登出.md) |
| `query_history_k_data_plus()` | A股/指数历史K线（日周月、5/15/30/60分钟，支持复权） | [04-获取历史A股K线数据.md](04-获取历史A股K线数据.md) |
| `query_dividend_data()` | 除权除息信息 | [05-查询除权除息信息.md](05-查询除权除息信息.md) |
| `query_adjust_factor()` | 复权因子 | [06-查询复权因子信息.md](06-查询复权因子信息.md) |
| `query_profit_data()` | 季频盈利能力 | [07-查询季频财务数据信息.md](07-查询季频财务数据信息.md) |
| `query_operation_data()` | 季频营运能力 | [07-查询季频财务数据信息.md](07-查询季频财务数据信息.md) |
| `query_growth_data()` | 季频成长能力 | [07-查询季频财务数据信息.md](07-查询季频财务数据信息.md) |
| `query_balance_data()` | 季频偿债能力 | [07-查询季频财务数据信息.md](07-查询季频财务数据信息.md) |
| `query_cash_flow_data()` | 季频现金流量 | [07-查询季频财务数据信息.md](07-查询季频财务数据信息.md) |
| `query_dupont_data()` | 季频杜邦指数 | [07-查询季频财务数据信息.md](07-查询季频财务数据信息.md) |
| `query_performance_express_report()` | 季频公司业绩快报 | [08-查询季频公司报告信息.md](08-查询季频公司报告信息.md) |
| `query_forecast_report()` | 季频公司业绩预告 | [08-查询季频公司报告信息.md](08-查询季频公司报告信息.md) |
| `query_stock_basic()` | 证券基本资料 | [09-证券基本资料.md](09-证券基本资料.md) |
| `query_trade_dates()` | 交易日查询 | [10-获取证券元信息.md](10-获取证券元信息.md) |
| `query_all_stock()` | 证券代码查询 | [10-获取证券元信息.md](10-获取证券元信息.md) |
| `query_deposit_rate_data()` | 存款利率 | [11-宏观经济数据.md](11-宏观经济数据.md) |
| `query_loan_rate_data()` | 贷款利率 | [11-宏观经济数据.md](11-宏观经济数据.md) |
| `query_required_reserve_ratio_data()` | 存款准备金率 | [11-宏观经济数据.md](11-宏观经济数据.md) |
| `query_money_supply_data_month()` | 货币供应量（月度） | [11-宏观经济数据.md](11-宏观经济数据.md) |
| `query_money_supply_data_year()` | 货币供应量（年底余额） | [11-宏观经济数据.md](11-宏观经济数据.md) |
| `query_stock_industry()` | 行业分类 | [12-板块数据.md](12-板块数据.md) |
| `query_sz50_stocks()` | 上证50成分股 | [12-板块数据.md](12-板块数据.md) |
| `query_hs300_stocks()` | 沪深300成分股 | [12-板块数据.md](12-板块数据.md) |
| `query_zz500_stocks()` | 中证500成分股 | [12-板块数据.md](12-板块数据.md) |

## 小节索引

### 入门示例

- [HelloWorld](01-入门示例.md#helloworld)

### 登录

- [login()](02-登录.md#login)

### 登出

- [logout()](03-登出.md#logout)

### 获取历史A股K线数据

- [获取历史A股K线数据：query_history_k_data_plus()](04-获取历史A股K线数据.md#获取历史a股k线数据query_history_k_data_plus)
- [历史行情指标参数](04-获取历史A股K线数据.md#历史行情指标参数)

### 查询除权除息信息

- [除权除息信息：query_dividend_data()](05-查询除权除息信息.md#除权除息信息query_dividend_data)

### 查询复权因子信息

- [复权因子：query_adjust_factor()](06-查询复权因子信息.md#复权因子query_adjust_factor)

### 查询季频财务数据信息

- [季频盈利能力：query_profit_data()](07-查询季频财务数据信息.md#季频盈利能力query_profit_data)
- [季频营运能力：query_operation_data()](07-查询季频财务数据信息.md#季频营运能力query_operation_data)
- [季频成长能力：query_growth_data()](07-查询季频财务数据信息.md#季频成长能力query_growth_data)
- [季频偿债能力：query_balance_data()](07-查询季频财务数据信息.md#季频偿债能力query_balance_data)
- [季频现金流量：query_cash_flow_data()](07-查询季频财务数据信息.md#季频现金流量query_cash_flow_data)
- [季频杜邦指数：query_dupont_data()](07-查询季频财务数据信息.md#季频杜邦指数query_dupont_data)

### 查询季频公司报告信息

- [季频公司业绩快报：query_performance_express_report()](08-查询季频公司报告信息.md#季频公司业绩快报query_performance_express_report)
- [季频公司业绩预告：query_forecast_report()](08-查询季频公司报告信息.md#季频公司业绩预告query_forecast_report)

### 证券基本资料

- [证券基本资料：query_stock_basic()](09-证券基本资料.md#证券基本资料query_stock_basic)

### 获取证券元信息

- [交易日查询：query_trade_dates()](10-获取证券元信息.md#交易日查询query_trade_dates)
- [证券代码查询：query_all_stock()](10-获取证券元信息.md#证券代码查询query_all_stock)

### 宏观经济数据

- [存款利率：query_deposit_rate_data()](11-宏观经济数据.md#存款利率query_deposit_rate_data)
- [贷款利率：query_loan_rate_data()](11-宏观经济数据.md#贷款利率query_loan_rate_data)
- [存款准备金率：query_required_reserve_ratio_data()](11-宏观经济数据.md#存款准备金率query_required_reserve_ratio_data)
- [货币供应量：query_money_supply_data_month()](11-宏观经济数据.md#货币供应量query_money_supply_data_month)
- [货币供应量(年底余额)：query_money_supply_data_year()](11-宏观经济数据.md#货币供应量年底余额query_money_supply_data_year)

### 板块数据

- [行业分类：query_stock_industry()](12-板块数据.md#行业分类query_stock_industry)
- [上证50成分股：query_sz50_stocks()](12-板块数据.md#上证50成分股query_sz50_stocks)
- [沪深300成分股：query_hs300_stocks()](12-板块数据.md#沪深300成分股query_hs300_stocks)
- [中证500成分股：query_zz500_stocks()](12-板块数据.md#中证500成分股query_zz500_stocks)

### 示例程序

- [获取指定日期全部股票的日K线数据](13-示例程序.md#获取指定日期全部股票的日k线数据)

## 本地资源文件

- [BaoStockAdjustFactorIntro.pdf](assets/BaoStockAdjustFactorIntro.pdf) — 389127 字节
- [adjust_factor_data.xlsx](assets/adjust_factor_data.xlsx) — 3475 字节
- [history_A_stock_k_data.xlsx](assets/history_A_stock_k_data.xlsx) — 35895 字节
- [history_Dividend_data.xlsx](assets/history_Dividend_data.xlsx) — 3861 字节
- [history_k_data.xlsx](assets/history_k_data.xlsx) — 47898 字节

