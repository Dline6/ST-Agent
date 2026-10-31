---
id: T-AGT-010.2
parent: T-AGT-010
title: data_status（数据现状原语）
story: ../../docs/PRD-v2-Agent/story-05-local-first.md
arch: ../../docs/技术架构-v2/02-L0-本地优先基座.md
arch_link: "[02 §5](../../../../docs/技术架构-v2/02-L0-本地优先基座.md)"
priority: P0
milestone: M8
depends_on: [T-AGT-008.3]
status: done
decisions: [D-137, D-141]
verify: 逐任务/逐表行数与最后成功时间；present/known_empty/missing 三态；覆盖账本返回无 ok 行缺口标的；库缺 unavailable。范围套件 2343 passed，新增 17 例（本叶 5），verify_docs --strict PASS；见执行日志 [T-AGT-010.2]
---

# T-AGT-010.2 · data_status（数据现状原语）

## 目标
让模型知道「有哪些表、多少行、多旧、缺哪个标的」，从而决定去同步还是如实告知。

## 验收标准（Given-When-Then）
- GWT-1：Given 已同步的库，When 调用，Then 返回逐任务 / 逐表行数与最后成功时间。
- GWT-2：Given 未同步或部分同步，Then 显式区分「已知空集」与「数据缺失」。
- GWT-3：Given 覆盖账本，Then 返回缺口标的。

## 接口面
- 输入（消费的前置接口）：
  - `T-AGT-008.2` —— `BuiltinPrimitive`（`requires=("market",)`）与 `install_builtins`
  - 组合根注入的 `market_query`：须提供 `status_snapshot(*, dataset=None, max_codes=50) -> dict`
  - L0 `MarketDb` 的 `sync_state` 全量行 / 逐表行数 / `_AS_OF_SQL` / `sync_coverage`（一次只读连接取齐）
  - 任务清单 `SYNC_TASKS`（`l0/market/tasks.py`）与 `INFO_TASKS`（`l0/info/sources.py`）
- 输出（本任务交付的公共 API / 落盘位置）：
  - 内置原语种子 `sk_data_status_v1.0`（`st_agent.l1.builtin.data_status.DATA_STATUS`，并入 `BUILTIN_PRIMITIVES`）
  - `MarketDb.status_snapshot(*, dataset=None, max_codes=50) -> dict`：`db_exists` / `snapshot_id` / `as_of` / `tasks` / `tables` / `freshness` / `coverage` / `gap_codes` / `notes`
  - 留痕沿用 `execution_log/skill-run/<id>.json`

## 可关闭的遗留
无——各层册未闭区逐册核过，无命中（`verify_docs.py --strict` 检查 8 = 0）。

## 假设与前提
- **A1**：「已知空集」与「数据缺失」按「表是否存在 + 行数 + 所属任务 `last_status` / `last_success_at`」判定：表有行=`present`；表存在、0 行且所属任务已成功同步=`known_empty`；其余（表缺 / 任务未同步 / 未成功）=`missing`。
  - 若错的影响：`data_status.tables` 与 `tasks` 的状态语义；M9 取数原语的判断依据。
  - 验证方式：构造「已同步 0 行」「未同步」「表缺失」三类用例逐条断言。
- **A2**：缺口标的以覆盖账本中**无任何 `ok` 行**的码为缺口，不假设目标代码集（L1 看不到关注池 / 持仓）。
  - 若错的影响：`coverage` / `gap_codes` 的语义；不影响逐表行数。
  - 验证方式：构造同一码分别只有 `partial` / `failed` / `ok` 行的用例。
- **A3**：内置原语在 `build_l1_runtime` 与官方 Pack 同点安装，因此所有 M1–M5 运行时的工具目录都会多两条；既有「12 个 base」断言同步更新。
  - 若错的影响：装配面用例与 UI 能力清单基数。
  - 验证方式：集成套件与能力清单用例。

## 涉及契约
- [02 §5](../../../../docs/技术架构-v2/02-L0-本地优先基座.md) —— 数据现状与新鲜度
- [05 同步策略与新鲜度契约](../../../../docs/数据库设计-BaoStock数据层/05-同步策略与新鲜度契约.md) —— `as_of` 口径

## 参考
- `l0/sync_runner.py` 的 `overview()`
- `l0/market/db.py` 的 `freshness()` / `as_of()`

## 备注
没有本叶，模型无法判断「该不该去联网」——它是 M9 取数原语的前置。
