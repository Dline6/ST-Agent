---
id: T-INT-005
parent: null
title: M4 集成关卡 · 反思演进与生态闭环
story: ../../docs/PRD-v2-Agent/README.md
arch: ../../docs/技术架构-v2/00-架构总览.md
arch_link: "[00 §5](../../../../docs/技术架构-v2/00-架构总览.md)"
priority: P0
milestone: M4
depends_on: [T-L6-001.1, T-L6-001.2, T-L6-001.3, T-L6-002.1, T-L6-002.2, T-L6-002.3, T-L6-003.1, T-L6-003.2, T-L6-003.3, T-ECO-001.1, T-ECO-001.2, T-ECO-002.1, T-ECO-002.2, T-INT-004, T-UI-004.1, T-UI-004.2, T-UI-004.3, T-UI-004.4]
status: done
decisions: [D-088]
verify: 全量 pytest **3404 passed / 0 failed / 14 deselected（live） / 682.86s（11:22）** · 范围套件（integration ∪ l6 ∪ eco ∪ layering ∪ tools ∪ contracts）**962 passed（194.48s）**，本批新增 32 例（test_m4_reflection_eco 30 GWT-1..10 离线端到端 · test_l6_adapters_live 2 live）· live **4 passed（33.49s）**——真端点下两个新适配器各真出网一次（非 skip）· `verify_docs.py --strict` 检查 1–9 全 0（264 文件 / 11320 链接 / 0 断链）· 生产入口真装配 M4 跑通一轮 `tick` · 表现层入口新立 T-UI-004（M4 归档待其 done）· 见执行日志 [T-INT-005]
---

# T-INT-005 · M4 集成关卡 · 反思演进与生态闭环

## 目标
把 L6 反思演进与生态分享装配进已可主动服务的系统，端到端跑通「反馈回流 → 反思报告 → 演进授权与变更流（含告知与回滚）」以及「导出 → 导入校验」两条闭环。

具体地：新增**生产组合根** `build_m4_runtime`（复用 M3 的 L0–L5 装配与事件总线，在其上叠 L6 八面 + ECO 三面），把各层**点名归本关卡**的四处未接线接上——[08 §3] 的训练理解端口与 `train` 去向注入（[`T-L6-002.1`](T-L6-002.1-训练对话协议.md) A1）、[08 §4] 的模式观察面注入（[`T-L6-002.2`](T-L6-002.2-主动提案与周报候选生成.md)）、[08 §5] 的 `ChangeApplied` / `ChangeRolledBack` ⇒ **L5 告知通道订阅**（[`T-L6-003.2`](T-L6-003.2-变更流与变更历史回滚.md)）、[08 §4] 的 A/B 授权面注入（[`T-L6-003.1`](T-L6-003.1-演进授权档位与风险分级清单.md)）；并令生产入口 `python -m st_agent` 真的装配 M4。证明 [00 §5] 第 7–8 步与 [09] 导出/导入流程在装配态逐段兑现。

## 验收标准（Given-When-Then）
- **GWT-1 生产装配根可用**：Given 全新空目录 + 口令，When 经 `build_m4_runtime`，Then M3 面（L0–L5 + 事件总线）逐项不变地就位 **且** L6 八面（反思池 / 周报 / 训练协议 / 提案引擎 / A-B 实验 / 演进授权 / 变更流 / 出厂重置）与 ECO 三面（导出 / 导入校验 / 官方索引）**用真实依赖构造**（非注入 Fake）· 01 §7 各族已登记进统一门面 · 事件总线上可查各订阅者（L2 冲突队列 · L3 裁决承接 · L3 反馈采集 · L5 采纳 · L5 疲劳答复 · L6 反思池 · 变更告知面）· `chat` 与 `store` 同源。
- **GWT-2 反馈回流与去重归因**：Given 一次用户反馈经 L3 `record_feedback(...)`，When 落在总线上，Then L6 反思池**与** L5 疲劳面**并列消费**同一 `FeedbackRecorded`（互不干扰）· 同一 `feedback_id` 重复到达是同一事实的重放 ⇒ **幂等覆盖、不追加流水**（池子计数不变）· 载荷不合契约时逐订阅者**记因不吞**。
- **GWT-3 每周反思报告基于真实反馈数据并经 L5 渠道投出**：Given 装配后的池子与投递留痕，When `tick(now)` 到点，Then 报告第 1 段取材真投递留痕 + 真频控/疲劳计数、第 2 段取材真 L2 记忆面、第 4 段候选来自真提案引擎 → 四段齐备 → 经 L5 渠道面投出并**回写留痕**；两处空态（本周无触达 / 反馈数低于数据不足阈值）**分开判定、不硬凑**；同一周**只投一次**（从盘上留痕读，重启安全）。
- **GWT-4 演进提案走授权档位且自动变更显式告知（铁律 6）**：Given 一条提案，When `change_flow.submit`，Then 按档位判定——`collaborative`（默认）⇒ **立案待批准**、未生效；切 `autonomous` 且范围在低风险清单内 ⇒ **自动放行**；`manual` ⇒ **不立案**（只建议）；生效时产 `change_id` 并发布 `ChangeApplied`，**订阅面经 L5 通道显式告知**（`notified` 为真，非「未送达」）。
- **GWT-5 变更可回滚且回滚后状态自洽**：Given 一条已生效的演进变更，When `rollback(change_id)`，Then 以变更前生效取值**经门面回放**（本身也是一次变更、产生**新** `change_id`、发布 `ChangeRolledBack`）· 条目取值回到旧值 · 变更历史里新旧两条**都在** · 条目此前从未落值时**不回放**并如实说明（不写假值冒充恢复）。
- **GWT-6 出厂重置保留 Memory 且三次确认是硬门**：Given 已发生的演进变更，When `factory_reset.request(confirmations<3)`，Then **拒**（不留痕、不动任何值）；When 三次确认，Then 逆序回放演进变更 + A/B 实验转已停止（留痕保留）+ `evolution.authorization` 复位 `collaborative`，且 **`memory` 分区清单与逐文件内容逐一致**。
- **GWT-7 分享物导出 → 干净环境导入闭环**：Given 四类分享物可由既有门面取材，When 导出（`.stskill` / `.stflow` / `.stlens` / `.stmem`），Then 产出统一容器（`header` / `payload` / `manifest`，含**校验和**与**来源追溯链**）；When 在**干净装配**（新根、无该 Skill）导入，Then 格式校验通过 → 依赖解析 → 权限审核 → 用户批准 → **安装成功**，且导入物留**完整 provenance**（分享者 / 时间 / 校验和 / 出处链）。
- **GWT-8 恶意 / 越权导入被拦截**：Given 损坏文件（校验和不符 / 非本族 / 主版本不兼容），Then 第 1 段**明确报错、不安装**；Given 依赖缺失，Then **提示缺失 + 给出获取途径 + 不自动安装**；Given `.stmem` 载荷含 `private` / `sensitive` 节点或 `.stskill` 权限未全部批准，Then **拒绝安装**（越权不静默通过）。
- **GWT-9 训练对话与回访接线**：Given 组合根把 `TrainingProtocol` 注入总线 `trainings=`，When `train` 意图派发 → 概念性理解（**真实 LLM 适配器**）→ 用户确认后落账，Then 经注入记忆写入面**新增** `pattern` 节点（`source=user_stated`）+ 承载提案候选 + 落回访留痕；**下次对话**经 `chat.turn` 暴露**待回访项**并 `acknowledge`（不重复提及）；未注入理解端口时 `train` 去向 fail-closed + 点名。
- **GWT-10 到点驱动与生产入口切 M4**：Given `python -m st_agent` 默认装配，When 启动，Then 入口装的是 `build_m4_runtime` 且原生通知端口由入口注入；常驻循环只调 `runtime.tick(now)`（判定全在 `tick` 内）；`tick` 推进 M3 面 **且** 到点发布/投出周报。
- **GWT-11 全量回归与文档自检**：默认 `pytest` **全量**绿（集成关卡强制全量，见 [工作流](../../../工作流.md)「测试分层」）；`verify_docs.py --strict` 检查 1–9 全 0；关卡用例全部**离线**可跑（出网面由 rig 替身接管）；**动到真实 LLM 出网面**（新增两个 LLM 适配器）⇒ **必跑** `python -m pytest -m live tests/live/test_llm_live.py`，结果写进执行日志「验证」行（无端点 `skip` 须写明原因）。

## 接口面
- **输入**（逐条点名上游任务 id + 具体 API / 落盘位置）
  - 装配入口：`T-INT-004` → `build_m3_runtime` / `M3Runtime.tick` 及其私有栈构造器 `_build_l2` / `_build_l3` / `_build_l4` / `_build_l5`（[app.py](../../../../src/st_agent/app.py)）；`T-INT-002/003` → `build_m1_runtime` / `build_m2_runtime` / `open_runtime` / `L1Runtime`（同文件 / [l1/runtime.py](../../../../src/st_agent/l1/runtime.py)）
  - L6 薄装配缝：`T-L6-001.3` → `build_l6(store, *, registry=, events=, dispatcher=, orchestrator=, frequency=, fatigue=, reader=, advisor=, understander=, memory_writer=, observer=, authorizer=, now=) -> L6Stack`（[l6/runtime.py](../../../../src/st_agent/l6/runtime.py)）；`L6Stack` 字段 `pool` / `reports` / `training` / `proposals` / `experiments` / `authorization` / `change_flow` / `factory_reset` / `events` / `registry`
  - L6 各面：`T-L6-001.1` → `FeedbackPool.consume / counts / get`（[l6/pool.py](../../../../src/st_agent/l6/pool.py)）· `T-L6-001.2` → `WeeklyReportBuilder.due / build / publish / deliver / template`（[l6/weekly.py](../../../../src/st_agent/l6/weekly.py)）· `T-L6-002.1` → `TrainingProtocol.start / confirm / record / callbacks / acknowledge`（[l6/training.py](../../../../src/st_agent/l6/training.py)）· `T-L6-002.2` → `ProposalEngine.detect / evaluate / proposals`（[l6/proposal.py](../../../../src/st_agent/l6/proposal.py)）· `T-L6-002.3` → `ExperimentConsole`（[l6/experiment.py](../../../../src/st_agent/l6/experiment.py)）· `T-L6-003.1` → `EvolutionAuthorization.tier / set_tier / permits`（[l6/authorization.py](../../../../src/st_agent/l6/authorization.py)）· `T-L6-003.2` → `EvolutionChangeFlow.submit / accept / reject / defer / rollback / history / pending`（[l6/change.py](../../../../src/st_agent/l6/change.py)）· `T-L6-003.3` → `FactoryReset.request`（[l6/reset.py](../../../../src/st_agent/l6/reset.py)）
  - ECO 面：`T-ECO-001.2` → `ShareExporter(skills=, workflows=, lenses=, memory=, now=)`（[eco/export.py](../../../../src/st_agent/eco/export.py)）· `T-ECO-002.1` → `ShareImporter(store=, skills=, workflows=, lenses=, memory=, permissions=, now=)`（[eco/import_pipeline.py](../../../../src/st_agent/eco/import_pipeline.py)）· `T-ECO-002.2` → `OfficialIndex(gateway=, host=, fetch=)`（[eco/index.py](../../../../src/st_agent/eco/index.py)）
  - L5 面（周报投出 / 变更告知）：`T-L5-002.3` → `build_l5` / `L5Stack`（`dispatcher` / `orchestrator`=`delivery` / `frequency` / `fatigue` / `policies`，[l5/runtime.py](../../../../src/st_agent/l5/runtime.py)）· `ChannelDispatcher.deliver(chain, payload)`（[l5/channels.py](../../../../src/st_agent/l5/channels.py)）· `ChannelPolicies.get(level).channels`（[l5/channel_policy.py](../../../../src/st_agent/l5/channel_policy.py)）· `ChannelPayload`
  - L2 面（记忆取材 / 片段面）：`T-L2-001` → `MemoryReader` / `MemoryWriter.add_node`（[l2/memory/](../../../../src/st_agent/l2/memory)）· `MemoryShare(graph)` / `FragmentImporter(graph, writer, ...)`（[sharing.py](../../../../src/st_agent/l2/memory/sharing.py) / [importer.py](../../../../src/st_agent/l2/memory/importer.py)）
  - L1 门面（01 §7 / 技能库 / 出网）：`T-L1-012` → `ConfigRegistryFacade`（`register_family` / `set` / `entry` / `changes`，[l1/registry/facade.py](../../../../src/st_agent/l1/registry/facade.py)）· `L1Runtime.skills` / `workflows` / `skill_permissions` / `gateway` / `llm`（[l1/runtime.py](../../../../src/st_agent/l1/runtime.py)）
  - 事件面：`T-INT-004` → `EventBus.publish / subscribe / subscribers`（[l1/events.py](../../../../src/st_agent/l1/events.py)）；`ChangeApplied` / `ChangeRolledBack` 负载结构（[01 §11](../../../../docs/技术架构-v2/01-平台共享契约.md) / [l6/change.py](../../../../src/st_agent/l6/change.py)）
  - 训练理解 / 观察面数据源：`T-L3-001` → `SessionStore.sessions / load / chain / search`（[l3/chat/session.py](../../../../src/st_agent/l3/chat/session.py)）
- **输出**（本关卡交付的公共 API 与落盘位置）
  - [src/st_agent/app.py](../../../../src/st_agent/app.py)（改）：`_build_l3(...)` / `_build_m2_pieces(...)` **只增** `trainings=` / `callbacks=` 可选参数（缺省路径逐字节不变）· 新增 `_build_l6(runtime, l2, l5, *, events, training_understander, observer, now) -> L6Stack` · 新增 `_build_eco(runtime, l2, l4, *, index_host, index_fetch, now) -> _EcoStack` · 新增 `_ChangeNotifier`（`ChangeApplied` / `ChangeRolledBack` ⇒ L5 通道投出中性告知）· 新增 `LlmTrainingUnderstander` / `LlmPatternObserver`（真实 LLM 适配器，住组合根——L6 层不 import `l0.llm`）· `DialogFacade` 只增可选 `callbacks` 端口与 `turn()` 的 `callbacks` 字段 · 新增 `M4Runtime`（携 `m3` / `l6` / `eco` / `events`，属性 `m1` / `m2` / `l5` / `store` / `chat`，方法 `tick(now)` 叠周报到点）· 新增 `build_m4_runtime(root, passphrase, *, ..., training_understander=None, observer=None, index_host="", index_fetch=None, now=_now, **kw) -> M4Runtime`。
  - [src/st_agent/__main__.py](../../../../src/st_agent/__main__.py)（改）：默认 `build_runtime=build_m4_runtime` · 注入 `notify=ui.shell.notify.send_notification`（同 M3 口径）。
  - [tests/integration/rig_m4.py](../../../../tests/integration/rig_m4.py)（新）+ `test_m4_reflection_eco.py`（新）：M4 装配 rig（复用 `rig_m3`）+ GWT-1..9 端到端离线用例。
  - [tests/test_layering.py](../../../../tests/test_layering.py)（改）：`APP_ALLOWED_IMPORTS` 加 `l6` / `eco`（[T-INT-004] 加 `l5` 的同型改动）。
  - [tests/live/test_l6_adapters_live.py](../../../../tests/live/test_l6_adapters_live.py)（新）：两个真实 LLM 适配器经真端点冒烟（缺端点 `skip` 并写明原因）。
  - **落盘位置**：全部沿用既有分区——`reflection/`（池 / 周报 / 训练 / 提案 / 实验 / 变更 / 重置）、`config/`（01 §7 条目与变更留痕）、`skills/` / `workflows/` / `lenses/`（导入安装）。**不新增分区、不新增事件名、不新增 01 §1 的 ID 类**。
- **不交付**（显式标注，非欠账）：新增 UI 端点（反思中心 / 变更历史时间线 / 导入导出面板 / 演进授权设置页的**表现层入口**）——`ui` 只经回环面收 `chat`，新增只读端点属表现层能力，**已另立 [`T-UI-004`](T-UI-004-反思中心与生态面的表现层入口.md)**（本批 ④ 对齐拍定；本关卡 `depends_on` 同批补入它，故 **M4 归档待 `T-UI-004` `done`**）；跨进程推送；官方索引的**真实端点**（`index_fetch` 缺省 ⇒ 显式 `unavailable`，端点属用户侧 ⇒ 归**人**）；真实 SMTP / Webhook 渠道真跑（属 [L5 册 `A1`](../../../遗留问题/L5-遗留问题.md)，归属＝人）。

## 可关闭的遗留
- **册内归属本任务项：无**——开工逐册核未闭区：[L6 册](../../../遗留问题/L6-遗留问题.md) **0 条未闭**（`A1` / `A2` 已由 [T-L6-002.2](T-L6-002.2-主动提案与周报候选生成.md) / [T-L6-002.1](T-L6-002.1-训练对话协议.md) 关闭）· [L5 册](../../../遗留问题/L5-遗留问题.md) `A1` 归属＝**人**（真实端点凭据）· [L0 册](../../../遗留问题/L0-遗留问题.md) 未闭项归属＝**人决 / 人** · [L1 册](../../../遗留问题/L1-遗留问题.md) / [L2 册](../../../遗留问题/L2-遗留问题.md) / [L3 册](../../../遗留问题/L3-遗留问题.md) 未闭项归属均非本任务；[阻塞与未决](../../../阻塞与未决.md) `Q` 段 **0 open**。
- **任务层遗留清偿**（点名本关卡的四处，均随本批兑现）：① [T-L6-002.1](T-L6-002.1-训练对话协议.md) A1 / 备注「真实 LLM 适配器与组合根注入 + 回访在对话里的渲染」→ 由 `LlmTrainingUnderstander` + `trainings=` 注入 + `DialogFacade` 回访面清偿；② [T-L6-002.2](T-L6-002.2-主动提案与周报候选生成.md) 备注「模式观察面的组合根注入」→ 由 `LlmPatternObserver` 注入清偿；③ [T-L6-003.2](T-L6-003.2-变更流与变更历史回滚.md) 下游「L5 告知通道对 `ChangeApplied` 的订阅」→ 由 `_ChangeNotifier` 清偿；④ [T-L6-003.1](T-L6-003.1-演进授权档位与风险分级清单.md) 下游「把授权面注入 `build_l6(authorizer=…)`」→ 由组合根缺省接上本层授权档位面清偿（`build_l6` 缺省已如此，本批以**端到端用例**钉住）。
- **本批新登记**（不属本任务、已定归属）：表现层入口（反思中心 / 变更历史 / 导入导出 / 授权设置页）→ **新立 [`T-UI-004`](T-UI-004-反思中心与生态面的表现层入口.md)**（P1 / M4，本关卡 `depends_on` 同批覆盖它，故 M4 归档待其 `done`）；官方索引真实端点 `index_fetch` → 归**人**（端点属用户侧）。

## 假设与前提
- **A1 · 组合根仍落在 [`app.py`](../../../../src/st_agent/app.py)**（2026-10-07 ④ 对齐拍定）：新增 `build_m4_runtime` / `M4Runtime` 与两个 LLM 适配器，`_build_l3` 以**只增可选参数**吸收训练端口与回访面，并令 [`test_layering.py`](../../../../tests/test_layering.py) 的 `APP_ALLOWED_IMPORTS` 加 `l6` / `eco`（[T-INT-004] A1 的同型改动）。**若错**（要求另立组合根模块）：`app.py` 与层扫描用例返工、M4 装配面重做；且 `app.py` **不得** import 非层兄弟模块（`test_app_is_composition_root_only` 会判 `_layer_of=None`），故新模块须另走层表口径。**验证**：`test_app_is_composition_root_only` 全绿且新增断言「`app` 可装配 `l0–l6` + `eco`」。
- **A2 · 两个真实 LLM 适配器**（训练理解 / 模式观察）**住组合根、不落 L6**（2026-10-07 ④ 对齐拍定）：[T-L6-002.1](T-L6-002.1-训练对话协议.md) A1 的机器守卫断言「`src/st_agent/l6/**` 不 import `l0.llm` / `l0.net`」，故适配器**不能**住 L6；组合根是仅有的同时可 import `l0.llm` 与 L6 鸭子面的位置（先例＝`_SignalEmittingAnalyze` 住 `app.py`）。**若错**（要求适配器住 L6）：须废掉该 grep 守卫并改 [08 §7] 红线的机器钉法，返工面＝L6 全层。**验证**：`tests/l6/test_pool.py` 的 no-egress 守卫不改即仍绿；新适配器用例经注入 `LlmClient` 替身离线覆盖 prompt / 解析。
- **A3 · 变更告知经 L5 渠道面直接投递，不新增 L5 面**（2026-10-07 ④ 对齐拍定）：`_ChangeNotifier` 订阅 `ChangeApplied` / `ChangeRolledBack`，用 [`ChannelPolicies.get(level).channels`](../../../../src/st_agent/l5/channel_policy.py) 取有序链、构造 `ChannelPayload` 调 `ChannelDispatcher.deliver`（同周报 `deliver` 的口径）。**若错**（要求 L5 新增「系统告知」一类面）：触 [07 §3] 契约（[铁律 8] 序）与 L5 既有用例。**验证**：GWT-4 断言生效后 `notified` 为真且渠道路由逐级留痕。
- **A4 · 到点驱动＝组合根给显式时刻的纯函数 `M4Runtime.tick(now)`，常驻循环住生产入口**（[T-INT-004] A3 的同型）：周报「一周一次」判据取**盘上留痕**（重启安全），同日报口径。**若错**（要求接进 L1 `Scheduler` 目标集）：须扩 `ScheduleTarget` 模型 + 触 [03 §6] 契约。**验证**：GWT-3 / GWT-10 以固定 `now` 复算；同一周第二次 `tick` 不重投。
- **A5 · 训练理解 / 模式观察两个 LLM 适配器触真实出网面 ⇒ 必跑 live 子集**（[工作流](../../../工作流.md)「测试分层」节末）：离线用例经 `LlmClient` 替身覆盖 prompt / 解析 / 降级；真链路另立 `tests/live/test_l6_adapters_live.py`（缺端点 `skip` 并写明原因）。**若错**（要求离线也真调）：CI 会随本机 `.env` 有无而变。**验证**：live 用例在本机按有无端点分别 pass / skip，结果写进执行日志「验证」行。

## 涉及契约
- [00-架构总览 §5](../../../../docs/技术架构-v2/00-架构总览.md) 端到端数据流（**第 7–8 步**——本关卡 GWT 的锚点）· [§1.1](../../../../docs/技术架构-v2/00-架构总览.md) 进程形态（后端常驻为主体）
- [08-L6 §1–§7](../../../../docs/技术架构-v2/08-L6-反思演进.md)（反馈池 / 周报 / 训练对话 / 主动提案与 A-B / 演进授权与变更流 / 回滚与出厂重置 / 三条红线）
- [09-生态与分享 §1–§6](../../../../docs/技术架构-v2/09-生态与分享.md)（容器格式 / 导出 / 导入校验 / 官方索引 / 来源追溯 / 生态边界）
- [01-平台共享契约 §6](../../../../docs/技术架构-v2/01-平台共享契约.md) 中性化校验 · [§7](../../../../docs/技术架构-v2/01-平台共享契约.md) 配置元模型与变更留痕 · [§9](../../../../docs/技术架构-v2/01-平台共享契约.md) 版本语义 · [§10](../../../../docs/技术架构-v2/01-平台共享契约.md) 权限模型 · [§11](../../../../docs/技术架构-v2/01-平台共享契约.md) 事件清单与投递口径
- [04-L2 §3.2 / §8](../../../../docs/技术架构-v2/04-L2-记忆图谱.md)（写入只增不改；片段导出 / 导入与隐私分级）· [05-L3 §4 / §9](../../../../docs/技术架构-v2/05-L3-对话主入口.md)（`train` 去向 + 反馈采集点）· [07-L5 §3–§5](../../../../docs/技术架构-v2/07-L5-主动触达.md)（渠道 / 投递链 / 报告投出）
- [02-L0 §6](../../../../docs/技术架构-v2/02-L0-本地优先基座.md) 出网网关（官方索引浏览的 `index_browse` 类目）

## 参考
- Story（What）：[story-09](../../../../docs/PRD-v2-Agent/story-09-reflection-loop.md) · [story-10](../../../../docs/PRD-v2-Agent/story-10-skill-sharing.md)
- 前置关卡：[T-INT-004](../M3/T-INT-004-M3集成关卡主动触达投递闭环.md)
- 装配范式：[tests/integration/rig_m3.py](../../../../tests/integration/rig_m3.py)（生产组合根 + 确定性注入）
- 上游裁定：[D-085](../../../决策日志.md)（L6 首批）· [D-086](../../../决策日志.md)（L6 第二批）· [D-087](../../../决策日志.md)（L6 第三批）

## 备注
不重复单任务单测；只测**装配关系与跨层数据流**，出网面（LLM / 渠道 / 索引）用 Fake 适配器离线跑，真实 LLM 端点标 `live`。实现细节留给代码 / commit / 执行日志。
