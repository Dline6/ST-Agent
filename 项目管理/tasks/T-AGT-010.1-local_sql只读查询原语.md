---
id: T-AGT-010.1
parent: T-AGT-010
title: local_sql（只读查询原语）
story: ../../docs/PRD-v2-Agent/story-05-local-first.md
arch: ../../docs/技术架构-v2/02-L0-本地优先基座.md
arch_link: "[02 §5](../../docs/技术架构-v2/02-L0-本地优先基座.md)"
priority: P0
milestone: M8
depends_on: [T-AGT-008.3]
status: done
decisions: [D-137, D-141]
verify: SELECT 返回列与行且 sql/params/as_of/行数可回溯；写语句与多语句 validation_failed 不执行；max_rows 显式截断 + hint；超时 failed + 收窄建议；库缺 unavailable。范围套件 2343 passed，新增 17 例（本叶 10），verify_docs --strict PASS；见执行日志 [T-AGT-010.1]
---

# T-AGT-010.1 · local_sql（只读查询原语）

## 目标
暴露只读 SQL 原语，复用 `MarketDb.query` 的白名单与只读连接。

## 验收标准（Given-When-Then）
- GWT-1：Given 一条 `SELECT`，When 调用，Then 返回列与行，并把 **SQL 原文、绑定参数、`as_of`、行数**记入 Trace。
- GWT-2：Given `INSERT` / `UPDATE` / DDL / 多语句，Then `validation_failed` 且**不执行**。
- GWT-3：Given 超出行数或执行时限上限，Then 显式标注截断并给出继续查询的方式。
- GWT-4：Given 库缺失，Then `unavailable` 并标注首次同步未完成。

## 接口面
- 输入（消费的前置接口）：
  - `T-AGT-008.2` —— `BuiltinPrimitive`（`requires=("market",)`）与 `install_builtins`
  - `T-AGT-008.3` —— 标量参数走 `values`、对象输入走 `inputs`（`sql` 为标量参数，`params` 为对象输入）
  - 组合根注入的 `market_query`：须提供 `query(sql, params=(), *, max_rows=None, timeout_ms=None) -> ResultEnvelope`
  - L0 `MarketDb.query`（只读白名单、库缺失 `unavailable`）；本叶为其增可选上限参数
- 输出（本任务交付的公共 API / 落盘位置）：
  - 内置原语种子 `sk_local_sql_v1.0`（`st_agent.l1.builtin.local_sql.LOCAL_SQL`，并入 `BUILTIN_PRIMITIVES`）
  - `MarketDb.query(..., *, max_rows=None, timeout_ms=None)`——不传上限时行为逐字节不变；
     传上限时 ok 载荷增 `row_count` / `truncated` / `limit` / `hint`
  - 留痕：`execution_log/skill-run/<id>.json`（`params` 含 SQL 原文，`inputs` 含绑定参数）

## 可关闭的遗留
无——各层册未闭区逐册核过，无命中（`verify_docs.py --strict` 检查 8 = 0）。

## 假设与前提
- **A1**：SQL 原文与绑定参数以「可下钻」口径进 Trace——`sql` 作为标量参数进入 `TraceStep.input_digest`，绑定参数进 `SkillRun.inputs`，`as_of` 与行数进 ok 载荷（其摘要进 `output_digest`），由 `ref=skill_run_id` 回溯完整原文。
  - 若错的影响：仅 `local_sql` 执行器与用例的可解释面返工，不波及 L1 流水线。
  - 验证方式：用例断言 `input_digest` 随 SQL 变化、`skill-run` JSON 同时含 `params.sql` 与 `inputs.params`。
- **A2**：行数上限用 `SELECT * FROM (<sql>) LIMIT ?` 包裹（`PRAGMA` / `EXPLAIN` 无法包裹，退化为取回后切片）；时限用 SQLite progress handler。不传上限的既有调用行为不变。
  - 若错的影响：`MarketDb.query` 与其既有调用方；接入面集中在 L0 一处。
  - 验证方式：截断用例、超时用例、既有 `query` 用例全绿。
- **A3**：超时没有可安全返回的部分结果，按 `failed` 显式中止并给出收窄建议，不编造半截结果。
  - 若错的影响：GWT-3 的「时限」分支语义；只影响 `local_sql`。
  - 验证方式：无穷递归 CTE + 极小 `timeout_ms` 用例断言 `failed` 与收窄提示。

## 涉及契约
- [02 §5](../../docs/技术架构-v2/02-L0-本地优先基座.md) —— 只读查询面
- [01 §4](../../docs/技术架构-v2/01-平台共享契约.md) —— SQL 原文进 Trace

## 参考
- `l0/market/db.py` 的 `query(sql, params)`

## 备注
**可解释硬要求**（铁律 5）：SQL 原文与参数必须进 Trace，否则「查了库」不可复核。行数与时限上限属实现口径，但**必须显式**，不得静默截断。
