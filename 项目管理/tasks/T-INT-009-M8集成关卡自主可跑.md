---
id: T-INT-009
parent: null
title: M8 集成关卡 · 自主可跑
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M8
depends_on: [T-AGT-008.1, T-AGT-008.2, T-AGT-008.3, T-AGT-009.1, T-AGT-009.2, T-AGT-009.3, T-AGT-010.1, T-AGT-010.2, T-AGT-011.1, T-AGT-011.2, T-AGT-011.3, T-AGT-012.1, T-AGT-012.2]
status: done
decisions: [D-137]
verify: 全量 4695 passed / 16 deselected / 2 errors（两例 tests/ui/test_shell.py 本机既有环境性，报错文本与前批一致）；tests/integration/test_m8_autonomy.py 6 例覆盖 GWT-1～4（出厂 0 次确认的 SQL 聚合、陈旧截止日进下一轮与缺失不产假证据、交还后续跑不重放前序、Trace 下钻 SQL 原文与 as_of）；真后端 + 脚本端点走查续跑块（交还 → 确认并继续 → 证据表；落盘 resumed_from 指回旧痕、sk_sector_heatmap 只执行一次）；verify_docs.py --strict PASS。见执行日志 T-INT-009 条目。
---

# T-INT-009 · M8 集成关卡 · 自主可跑

> **里程碑收口关卡**：`depends_on` 须覆盖本里程碑全部叶子（[检查 7](../tools/verify_docs.py)），由 `render_ledger.py render` 派生后核对。

## 目标
端到端验证：出厂配置下，循环能自主走通一条**它此前答不出**的本地数据问题。

## 验收标准（Given-When-Then）
- GWT-1：Given 出厂配置与已同步数据，When 用户提出「分析最近 ST 股票有哪些热门板块」，Then 循环自主完成：读上下文 → 判数据新旧 → SQL 聚合 → 出证据包，**读操作 0 次确认**。
- GWT-2：Given 数据陈旧或缺失，Then 显式标注并给出下一步，**不假装完成**。
- GWT-3：Given 某步被判需确认，Then 交还并可一键续跑。
- GWT-4：Given 全链路，Then Trace 含每一步的 SQL 原文、参数与 `as_of`。

## 接口面

- 输入（消费的前置接口，逐条点名上游任务 id + 具体 API / 落盘位置）：
  - **工具面（T-AGT-008.1 / .2 / .3）**：`src/st_agent/l1/builtin/install.py` 把四条内置原语登记进**同一** `SkillRegistry`；模型可见面取 `SkillRegistry.tool_catalog()`，实参拆通道取 `l1/skills/tool_catalog.split_arguments`。
  - **只读数据原语（T-AGT-010.1 / .2）**：`src/st_agent/l1/builtin/local_sql.py`（`sk_local_sql`，SQL 原文进 `SkillRun.params`、绑定参数走 `SkillRun.inputs["params"]`、`as_of` 在载荷）与 `data_status.py`（`sk_data_status`，逐任务 / 逐表 / 数据域截止 / 缺口）；底层 `l0/market/db.py::MarketDb.query` / `status_snapshot`。
  - **上下文（T-AGT-009.1 / .2 / .3）**：`composition/agent.py::_AgentInvestigator.investigate` 的会话身份透传、历史 / 记忆注入与 `l3/runtime/context.py::assemble_work_context` 的预算取舍。
  - **循环与留痕（T-AGT-011.1 / .2 / .3）**：`l3/runtime/loop.py::run_agent_loop`（控制原语、终止原因、`pending`）、`l3/runtime/report.py::record_agent_run` / `conclude_agent_run`（`agent-run/` 留痕）、`l3/runtime/continuation.py::resume_agent_run`、`l3/dispatch/bus.py::resume`、`composition/facades/chat.py::_turn`（`action="resume"` 与 `agent_continuation` 载荷）。
  - **出厂授权（T-AGT-012.1 / .2）**：`l6/authorization.py::DEFAULT_RISK_GRADING`（`sk_local_sql` / `sk_data_status` / `sk_plan_update` / `sk_ask_user` 标 `autonomous-ok`）与 `l6/runtime_authorization.py::DEFAULT_AGENT_TIER = "autonomous"`。
  - **装配面**：生产组合根 `src/st_agent/__main__.py::build_ambient_runtime` / `app.py::build_m5_runtime`；离线套件沿用 `tests/integration/rig.py` 的真 `Store` + 真 `MarketDb` 播种与 `rig_m5.py::ScriptedEndpoint`（只换 HTTP 发送口）。
- 输出（本任务交付的公共 API / 落盘位置）：
  - `tests/integration/rig_m8.py`——M8 关卡装配 rig（ST + 板块种子、可编程脚本端点）；**测试专用**，不进生产 API、不改落盘契约。
  - `tests/integration/test_m8_autonomy.py`——GWT-1～4 的跨层装配用例。
  - **无新公共 API、无新落盘契约**——关卡只验收既有交付面；走查或用例若暴露缺陷，修复落在上述既有文件内，不新增跨层接口。
  - 交付物 = [执行日志](../执行日志.md) `[T-INT-009]` 条目（含「验证」行与假设复核）＋ 本任务 `verify` 字段 ＋ `render_ledger.py render` 派生的账本状态。

## 可关闭的遗留

逐册扫 [遗留问题](../遗留问题/README.md) 未闭区（2026-10-29）：

- `L0` 册：`C3`（全历史回填未做）· `C5`（全市场码表未跑）——归属均为**人决**，非本任务可闭。
- `L1` / `L2` / `L3` / `L6` 册：未闭区为空。
- `L5` 册：`A1`（真实渠道链路无真跑证据）——归属＝人（需真实 SMTP / Webhook 凭据），非本任务可闭。

**册内无命中**——本关卡不领取任何册内条目。另承接一条**非册内**的验收补项：[`T-AGT-011.3`](T-AGT-011.3-待确认与待输入步骤的一键续跑.md) 执行日志的「界面」行把「续跑块真路径走查」明确留给本关卡——本关卡以真后端 + 本地脚本化端点走一遍「交还 → 续跑块 → 确认并继续」。

## 假设与前提

- **A1 · 出厂配置即可自主读**：运行期档缺省 `autonomous`，且共享清单已把 `sk_local_sql` / `sk_data_status`（及两条控制原语）标 `autonomous-ok`（[D-143](../决策日志.md) / [D-144](../决策日志.md)），故[GWT-1] 无需任何测试侧放宽。
  - 若错的影响：GWT-1 会以 `needs_confirmation` 交还，说明出厂口径未落地——须回到 [01 §7](../../docs/技术架构-v2/01-平台共享契约.md) / `DEFAULT_RISK_GRADING` 修口径，本关卡不靠放宽掩盖。
  - 验证方式：用例**不调用** `_make_autonomous` 一类放宽助手，直接走 `confirm_and_dispatch` 并断言两步 `ok`。
- **A2 · 生产根仍是 M5 组合根**：M6–M8 未新增 `M<8>Runtime`，`build_ambient_runtime` 仍是生产入口；关卡用它与 `build_m5_runtime` 各装配一次。
  - 若错的影响：关卡装配的不是生产形态，结论不可迁移。验证方式：GWT-1 末尾补一条 `build_ambient_runtime` 装配用例。
- **A3 · 种子能表达「ST 热门板块」**：`stock_industry`（最新快照）+ `k_line_daily.is_st` 足以做板块聚合，不新增表 / 视图 / 契约。
  - 若错的影响：GWT-1 的 SQL 失去真实性，退化为玩具查询。验证方式：在真 `MarketDb` 上执行同一 SQL，断言逐板块行数与涨跌幅符合种子。
- **A4 · 离线套件以脚本端点代表模型选步**：本机无可用真实 LLM 端点，关卡沿用 M5 交付的 `ScriptedEndpoint`（只换 HTTP 一跳，`LlmClient` / transport / 网关 / 探测全真）。故**模型决策质量不在本关卡范围**，GWT-2 的「给出下一步」只能验证「数据截止 / 缺失原因确实到得了模型与产出」，不能验证模型是否用了它。
  - 若错的影响：对「陈旧数据」的验收强度被高估。验证方式：断言下一轮 prompt 含数据域截止与失败原因；执行日志如实写明该边界，并跑 `-m live`（无端点则 skip 写明原因）。
- **A5 · 续跑块真路径需自备脚本端点**：dev 面既无 chat 桩也无 LLM 端点，走查以本地 HTTP 脚本端点替换线上那一跳（不改产品代码）；口令经 `--passphrase-env` 传入，存储根用临时目录。
  - 若错的影响：界面证据退化为载荷透传（`tests/ui/test_ui_chat_page.py` 已覆盖的部分），须如实写「未走查」。验证方式：走查记录启动命令、根路径与逐跳观测。
- **A6 · 两例 `test_shell` ERROR 为本机既有环境性**：`ShellError: 后端需要口令`——按既有判据排除，不诊断、不修；全量结果逐条比对报错文本，只在与前批一致时排除。
  - 若错的影响：可能漏掉混在同类名目下的新失败形态。验证方式：比对报错文本 + 单独复跑该文件确认形态不变。
- **未决**：无 open `Q-NNN`（[阻塞与未决](../阻塞与未决.md) 当前 0 open）。

## 涉及契约
- [05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md) —— 循环全链路

## 参考
- 调研报告 §13.6
- 本里程碑 13 个叶子

## 备注
关卡 `depends_on` 由 `render_ledger.py render` 派生后须覆盖 M8 全部叶子（[检查 7](../tools/verify_docs.py)）。
