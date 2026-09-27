---
id: T-INT-001
parent: null
title: M0 集成关卡 · 骨架打通冒烟
story: ../../docs/PRD-v2-Agent/README.md
arch: ../../docs/技术架构-v2/00-架构总览.md
arch_link: "[00 §5](../../../../docs/技术架构-v2/00-架构总览.md)"
priority: P0
milestone: M0
depends_on: [T-SC-001.1, T-SC-001.2, T-SC-001.3, T-SC-001.4, T-SC-001.5, T-SC-002, T-L0-001, T-L0-002, T-L0-003, T-L0-004, T-L0-005.1, T-L0-006, T-L0-008, T-L0-009, T-L0-011, T-L1-001.1, T-L1-001.2, T-L1-001.3, T-L1-001.4, T-L1-001.5, T-L1-001.6, T-L1-002.1, T-L1-002.2, T-L1-002.3, T-L1-003.1, T-L1-003.2, T-L1-003.3, T-L1-003.4, T-L1-003.5, T-L1-004.1, T-L1-004.2, T-L1-004.3, T-L1-005.1, T-L1-005.2, T-L1-005.3, T-L1-006.1, T-L1-006.2, T-L1-006.3, T-L1-006.4, T-L1-007, T-L1-008, T-L1-009.1, T-L1-009.2, T-L1-009.3]
status: done
decisions: [D-045]
verify: 全量 pytest 1342 passed / 0 failed / 3 deselected（445s）；tests/integration 10 条（GWT-1..5）· verify_docs --strict 检查 1–8 全 0 · 红-绿已验（组合根 sandbox 改 None → GWT-4 FAIL）· 见执行日志 [T-INT-001]
---

# T-INT-001 · M0 集成关卡 · 骨架打通冒烟

## 目标
把 M0 分散在各任务里的交付（SC 契约 + L0 基座 + L1 能力池）**装配起来跑一次真实端到端**，用可重复的测试套件证明「骨架打通 / L1 最小闭环」这一里程碑目标成立——而不是把 22 个任务各自 done 当作等价物。

## 验收标准（Given-When-Then）
- GWT-1 装配即用：Given 全新空目录 + 口令，When 经 L1 组合根（T-L1-006）装配运行时（Store → 端点注册/凭据库/出网网关 → SkillRegistry（官方 Pack 播种）→ SkillRunner（注入沙箱）），Then 装配成功且官方 Pack 全部 Skill 可列出。
- GWT-2 一次执行的完整留痕：Given 已装配运行时 + 一个官方 Pack Skill（取数经**注入的离线数据面**），When 执行一次，Then 信封 `ok` · SkillRun 落盘可回放（输入快照/输出/耗时/依赖链）· Trace 含对应步骤（依赖链上每跳各一步）· 结果的 `as_of` 与证据引用口径符合 01 §5/§8；**且读本地缓存不出网 ⇒ 本次执行零出网审计**（02 §6 唯一出口），**审计面由「声明 + 批准 `net_access`」的样例 Skill 经 `ctx.gateway` 在同一装配上兑现**（[D-045]）。
- GWT-3 失败路径不编造：Given 数据源不可用，When 执行，Then 信封 `unavailable` 且带最后更新时间；Given 上游依赖 Skill 失败，When 执行下游，Then 下游 `dependency_failed`，不得用错误数据继续。
- GWT-4 越界拦截端到端：Given 已装配 runner + 沙箱，When 执行器向声明外主机出网或读声明外路径，Then 拦截 + `BehaviorViolation` 留痕 + 底层 sender/文件系统未被触碰。
- GWT-5 数据闭环：Given 已产生执行留痕与用户配置，When 备份 → 完全清空 → 恢复 → 重新装配，Then 运行时可用、官方 Pack 可列出，`config` 面用户数据（Skill 参数改动 / 工作流 / 批准账本）可查；且 `execution_log` **按设计不进归档**——留痕**不随备份走**（非静默丢失），恢复后新执行可正常留痕（[D-045]）。
- 回归：默认 `pytest` 全绿（含本关卡）；`verify_docs.py --strict` 通过；本关卡用例全部离线可跑（真实网络部分标 `live` 另跑）。

## 接口面
- **输入**（逐条点名上游任务 id + 具体 API / 落盘位置）
  - 装配入口：`T-L1-006` → `open_runtime(root, passphrase, *, create, market_query=…) -> L1Runtime` 与 `build_l1_runtime(...)` / `L1Runtime.set_online`（`src/st_agent/l1/runtime.py`）
  - L0 存储：`T-L0-001` / `T-L0-008` / `T-L0-009` → `Store.create` / `Store.open`；分区 `data_cache` / `execution_log` / `config`（`src/st_agent/l0/storage/store.py`）
  - L0 数据面：`T-L0-005.1` → `MarketDb.init_db` / `transact` / `query` / `snapshot_id`（`src/st_agent/l0/market/db.py`；落 `data_cache/market.db`）
  - L0 出网面：`T-L0-004` → `EgressGateway.execute` / `query` / `register_capability`（`src/st_agent/l0/net/gateway.py`；审计落 `execution_log/net/**`）
  - L0 端点与凭据：`T-L0-002` → `CredentialVault`；`T-L0-003` → `EndpointRegistry` / `LlmClient`
  - L0 备份面：`T-L0-006` → `create_backup` / `restore_backup` / `wipe_all`（`src/st_agent/l0/backup/backup.py`）
  - L1 执行面：`T-L1-001.2` → `SkillRunner.run(...) -> RunOutcome`（`envelope` / `trace` / `skill_run_id`）；`T-L1-001.1` → `SkillRegistry`；`T-L1-001.3/.5` → `SkillSandbox` / `SandboxSession`（`violations()` 读 `execution_log/sandbox-violation/**`）
  - L1 官方 Pack：`T-L1-004.1` → `install_official_pack`（组合根已调用；官方执行器取数经注入 `market_query`）；`T-L1-003.1` → `WorkflowStore.save`（GWT-5 的 config 面恢复证据）
  - L1 权限：`T-L1-009.1` → `SkillPermissionBook.declare` / `approve`（越界样例 Skill 的批准落点）
- **输出**
  - `tests/integration/`（新目录）：`conftest.py`（可重复装配 rig）+ `test_m0_skeleton.py`（GWT-1..5 端到端冒烟），默认离线可跑、进默认 `pytest`
  - 本里程碑接口的**实际调用面清单**＝本任务 `## 接口面` 的输入列表（装配路径上真正被调用的构造函数与公开方法）
  - 口径接线：`.github/workflows/ci.yml` 把 `tests/integration` 纳入「跨层套件」基表（否则 PR 范围判定下该套件不被执行）；[工作流 §测试分层](../../../工作流.md) 表同步

## 可关闭的遗留
- 无（开工查 [L0 册](../../../遗留问题/L0-遗留问题.md) / [L1 册](../../../遗留问题/L1-遗留问题.md) 未闭区：`A1b`/`A4` 归属 `T-ECO-*`、`A5` 归属 `T-L5-002`、`C1` 归属 `T-L0-007.2`（`gate: skip`）、`C2`/`F1` 深市待人立项、`D1`–`D3` 归人、`D2` 归属 `T-L3-003/004` 与 `T-ECO-002`——无一条归属本任务或解封于本任务 `done`）

## 假设与前提
- `A1` **`tests/integration/` 属「跨层套件」**（与 `tests/contracts` / `test_layering.py` 同档：任何代码变更都应随跑），因而是**主动接线**的对象——`.github/workflows/ci.yml` 的路径映射目前只认 `tests/l0` / `tests/l1`（新增 `tests/integration` 不进 targets），`tests/test_layering.py` 头注亦已声明「测试天然跨层装配」。若错的影响：本关卡在 CI 静默不执行，只有本地 ⑥ 全量覆盖（PR 门禁失效）。验证方式：改 `ci.yml` 后核对 `targets` 串含 `tests/integration`；并在 PR CI 日志确认该套件确实被收集。
- `A2` **GWT-5 原措辞与已交付的归档分区表相抵**——`BACKUP_PARTITIONS = (memory, config, chat_history, reflection)`，`execution_log` **永不进归档**（`T-L0-006` 假设 A1 / 02 §8.1，见 `src/st_agent/l0/backup/models.py:40` 与 `backup.py` 模块 docstring），而 SkillRun / 出网审计 / 越界留痕全落 `execution_log`。故 GWT-5 的「此前的 SkillRun 与审计记录可查」按字面**必失败**，按**真实分区口径**改判为：① 恢复后运行时可用（装配成功 + 官方 Pack 可列出）；② `config` 面用户数据可查（Skill 参数改动 / 工作流 / 批准账本 / 能力声明）；③ 显式断言「留痕不随备份走」——`execution_log` 是**设计上不进归档**（非静默丢失），且恢复后新执行可正常留痕。若错的影响：验收标准与实现相抵，关卡要么失败、要么被迫放宽到无语义。验证方式：读 `backup/models.py` / `backup.py` + 用例断言上述三条。
- `A3` **取数源用真实 `MarketDb`**（`data_cache/market.db` 的 sqlite 经 `Store` 加密落盘，离线可读）而非 Fake——这样 GWT-2 的「完整留痕」才含真实跨层数据流（L0 分区 → `MarketDb.query` → 官方执行器 → 流水线），且 `as_of` / `dataset_snapshot_id` 证据引用是 L0 真实产出。若错的影响：GWT-2 退回 Fake 注入，跨层成色下降（但结论仍成立）。验证方式：用例断言信封 `ok` 且 `as_of` 非空、证据引用为 `snap_<20 位十六进制>` 契约形态。
- `A4` **GWT-3 的「上游失败」用官方真实依赖链**：`sk_risk_alert` → `sk_delisting_risk_scan` → `sk_st_list_sync`（`pack.py` 种子已声明 `dependencies`）；令数据面缺失（`MarketDb` 未建库）→ 上游 `unavailable`（含 `last_updated_at`）→ 下游 `dependency_failed`，不另建自造依赖对。若错的影响：退回自建两个 Skill 承载，GWT-3 的「官方链路」成色下降。验证方式：用例断言两层状态串与 `last_updated_at` 存在。
- `A5` **GWT-4 越界用自建声明 Skill**：官方 Pack 自 `T-L1-009.3` 起一律 `permissions=()`，无法承载「声明内已批准但目标越界」用例，故注册一个声明 `net_access:<*.declared.example>` / `local_read:<data/declared/**>` 的样例 Skill 并批准，令其向声明外主机出网 / 读声明外路径；断言拦截 + `BehaviorViolation` 留痕 + 注入的 spy sender / reader **零调用**（底层未被触碰）。若错的影响：越界用例无处落，GWT-4 无法兑现。验证方式：用例断言 `spy.calls == []` 且 `SkillSandbox.violations()` 非空。
- `A6` **GWT-2 的「网关审计有记录」与实现相抵**：官方执行器只经**注入**的取数面读本地缓存、不调用沙箱三类出口（读缓存**不出网**），故官方那一次按 02 §6 必**零审计**。按真实口径落定为：官方那一次**显式断言零审计**（把「读本地缓存不出网」钉住），审计面由**声明 + 批准 `net_access`** 的样例 Skill 经 `ctx.gateway` 在**同一装配**上兑现（断言 `gateway.query(kind="data_fetch")` 恰一条、`initiator` 为该 Skill）。若错的影响：GWT-2 要么无法兑现、要么被迫放宽。验证方式：两个用例分别断言「官方运行后 `gateway.query() == ()`」与「出网运行后有恰一条 `ok` 审计」。
- **补充实证（实现期发现，已同步 [D-045](../../../决策日志.md)）**：拦截**信封**的 `reason` 面向人、**含**被访问资源串；A7 的「最小载荷」约束的是 `BehaviorViolation` **事件**载荷（只含 `skill_id` + 越界类别）。用例据此分别断言两处（信封里出现目标串、事件载荷里不出现）。

## 涉及契约
- [00-架构总览 §5](../../../../docs/技术架构-v2/00-架构总览.md) 端到端数据流（本关卡 GWT 的锚点）
- [01-平台共享契约](../../../../docs/技术架构-v2/01-平台共享契约.md) §5 ResultEnvelope / §8 时间锚点 / §10 权限 / §11 事件
- [02-L0](../../../../docs/技术架构-v2/02-L0-本地优先基座.md) §2 存储 / §4 LLM 端点 / §6 出网审计 / §8 备份
- [03-L1](../../../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md) §1 Skill Runtime / §1.5 执行沙箱

## 参考
- Story（What）：[PRD 索引](../../../../docs/PRD-v2-Agent/README.md)
- 里程碑定义：[AI-Coding项目管理方案 §7](../../../AI-Coding项目管理方案.md)
- 机制出处：集成关卡（[工作流.md](../../../工作流.md)）

## 备注
**不重复**各任务单测已覆盖的单元行为——只测装配关系与跨层数据流。用例放 `tests/integration/`；真实网络路径标 `@pytest.mark.live` 放 `tests/live/`，默认排除。实现细节不写此处，留给代码 / commit / 执行日志。
