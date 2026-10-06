---
id: T-INT-004
parent: null
title: M3 集成关卡 · 主动触达投递闭环
story: ../../docs/PRD-v2-Agent/README.md
arch: ../../docs/技术架构-v2/00-架构总览.md
arch_link: "[00 §5](../../docs/技术架构-v2/00-架构总览.md)"
priority: P0
milestone: M3
depends_on: [T-L5-001.1, T-L5-001.2, T-L5-002.1, T-L5-002.2, T-L5-002.3, T-L5-003.1, T-L5-003.2, T-L5-003.3, T-UI-002.1, T-UI-002.2, T-UI-002.3, T-UI-002.4.1, T-UI-002.4.2, T-UI-003, T-INT-003]
status: done
decisions: [D-084]
verify: 全量 pytest **3103 passed / 0 failed / 12 deselected（live） / 682.29s ≈ 11:22** · 范围套件（l5 ∪ ui ∪ integration ∪ contracts ∪ layering ∪ tools）**1007 passed（2:39）**，本批新增用例 61 例（`test_m3_delivery` 26 GWT-1..9 离线端到端 · `test_sources` 20 · 入口 7 · `test_runtime` 4 · `test_daily_report` 3；另 test_layering 逐文件参数化 +1）· live：`test_llm_live.py` **2 passed**（真端点）；渠道 live **2 skipped**（无 SMTP/Webhook 端点，原因写明）· 生产入口端到端一轮 `tick` 真跑（日报生成 + 投出 + 留痕回写）· `verify_docs.py --strict` 检查 1–9 全 0（252 文件 / 9396 链接 / 0 断链）· 见执行日志 [T-INT-004]
---

# T-INT-004 · M3 集成关卡 · 主动触达投递闭环

## 目标
把 L5 主动触达装配进已可决策的系统，端到端跑通「信号产生 → 触达投递 → 用户反馈」的完整生命周期。

具体地：新增**生产组合根** `build_m3_runtime`（复用 M1/M2 的 L2/L3/L4 装配，在其上叠 L5 与事件总线），把 [01 §11] 登记却**从未接线**的 `SignalEmitted` 真实产生点接上（L1 定时监控 / L4 Deliberation，[D-082] ④ 点名归本关卡），把「到点生成日报 / 疲劳巡查 / 定时监控」的驱动与原生渠道端口接上，并令生产入口 `python -m st_agent` 真的装配 M3；证明 [00 §5] 第 4–7 步在装配态逐段兑现，且 [06 §6] 的触达联动不再悬空。

## 验收标准（Given-When-Then）
- **GWT-1 生产装配根可用**：Given 全新空目录 + 口令，When 经 `build_m3_runtime`，Then M2 面（L0–L4）逐项不变地就位 **且** L5 六面（预算 / 渠道 / 编排 / 日报 / 频控 / 疲劳）**用真实依赖构造**（非注入 Fake）· 五个 01 §7 族已登记进统一门面 · 事件总线上可查各订阅者（L2 冲突队列的 `events` 端口、L3 裁决承接面、L3 反馈采集面、L5 采纳面、L5 疲劳答复面）· `chat` 与 `read_targets` 同源。
- **GWT-2 信号生成·L1 定时监控（真实产生点）**：Given 已装配运行时 + 播种的市场库与到期 Skill 目标，When `tick(now)` 推进一次（L1 调度执行 `sk_risk_alert`），Then 规则面把该次运行的**逐条 alert** 映射为 `SignalEmitted` 并在总线上发布 → L5 **自动采纳**（`accepted` 非空）→ 投递留痕落盘；载荷字段（`dedup_key` 逐标的逐维度、`evidence_refs` 取该次 `skill_run_id`、`source_trace_id`＝调度器的 `trace_id`）逐一可核，文本过 [01 §6]。
- **GWT-3 信号生成·L4 Deliberation（06 §6 触达联动）**：Given 一次多视角 `analyze`（方向冲突），When 结果落定，Then 按 [06 §6] 产生一条**「多视角摘要」形态**的信号——`content_ref.lens_stances` 为**逐视角并列、不合并分歧**（[铁律 4]），`conclusion` 为中性陈述、非单一视角措辞；无冲突 / 无看空视角时不产信号（**不硬凑**）。
- **GWT-4 注意力预算与情境模式在装配态生效**：Given 装配后的预算面与当前情境模式，When emergency 与 routine 两类信号经同一跳到达，Then `BudgetVerdict` 分别给出「即时触达」与 `route="daily_report"`；后者**不单独推送**但记一条待汇总项（不丢弃、不静默），且 `verdict` 可查。
- **GWT-5 升级链跨渠道按序推进、每级留痕**：Given emergency 信号，When `advance(now)` 按未读超时逐级推进，Then desktop → email → im_webhook 逐级各落**一枚确定性 `delivery_id`** 的留痕（重放 / 补发得同一 ID、写同一路径）；链尽仍未读 ⇒ 结算为未确认并**转入日报汇总**。
- **GWT-6 去重 / 频控 / 疲劳在装配态生效**：Given 同 `dedup_key` 短时多次触发 + 格位节奏上限 + 逐类连续忽略，When 连续 `tick`，Then 合并为一条呈现（附触发次数）· 超节奏者排队进下一次日报 · 达阈值产生**询问信号**（经正常投递得可寻址 `delivery_id`）· 复问须再攒满一轮阈值（不骚扰）。
- **GWT-7 渠道不可用 / 离线走显式降级**：Given 云端渠道不可达（发送器拒）与原生 TTS 端口未接，When 投递，Then 逐渠道返回**显式三态**（成功 / 不可达 / 未接线）并降级到下一渠道，**不静默丢弃**；离线期云端渠道记 `pending_reconnect` 并可经开关补发（**审计关闭也照常补发**）。
- **GWT-8 反馈回流闭环**：Given 用户对某次推送的反馈，When 经 L3 反馈入口 `record_feedback(...)` → `FeedbackCollector(sink=总线)` → `FeedbackRecorded`，Then L5 疲劳面**订阅消费**该答复并按三种答复落值——「减少」收窄该级别渠道链（既有写面，可回溯）·「关闭」入静音清单且**频控改判为汇总进日报**（关闭单独触达 ≠ 丢弃）·「保持」只把该类连续计数归零；三条落点均可查。
- **GWT-9 L5 网关联动与出网审计**：Given 云端渠道投递，When 执行，Then 请求经 [02 §6] 网关的 `channel_delivery` 类目发出、审计开启时经 `gateway.query(...)` 可查；凭据**只经 `CredentialVault.use()`** 取明文（明文不进网关、不进审计）。
- **GWT-10 真实链路（联网面强制项）**：本关卡组合根默认装配真 `LlmIntentUnderstander` / L4 真端口 / 真渠道传输，**动到真实 LLM 出网面** —— 离线 GWT-1..9 进默认 `pytest`；另**必跑** `python -m pytest -m live tests/live/test_llm_live.py`，并交渠道 live 用例（真 SMTP / Webhook 端点由环境变量给；无端点 `skip` 且**写明原因**，不许默默略过）。
- **GWT-11 生产入口切 M3 与全量回归**：Given `python -m st_agent` 默认装配，When 启动，Then 入口装的是 `build_m3_runtime` 且原生通知端口由入口注入；默认 `pytest` **全量**绿（集成关卡强制全量，见 [工作流](../工作流.md)「测试分层」）；`verify_docs.py --strict` 检查 1–9 全 0；关卡用例全部离线可跑。

## 接口面
- **输入**（逐条点名上游任务 id + 具体 API / 落盘位置）
  - 装配入口：`T-INT-003` → `build_m2_runtime` / `M2Runtime` 与其私有栈构造器 `_build_l2` / `_build_l3` / `_build_l4`（[app.py](../../src/st_agent/app.py)）；`T-INT-002` → `build_m1_runtime` / `open_runtime` / `L1Runtime`（同文件 / [l1/runtime.py](../../src/st_agent/l1/runtime.py)）
  - L5 薄装配缝：`T-L5-002.3` → `build_l5(store, registry=, events=, channels=, reader=, now=) -> L5Stack`（[l5/runtime.py](../../src/st_agent/l5/runtime.py)）；`T-L5-001.1` → `adopt_signal` / `signal_event` / `SIGNAL_EVENT`（[l5/signal.py](../../src/st_agent/l5/signal.py)）
  - 渠道与编排：`T-L5-002.1` → `ChannelDispatcher` / `DesktopChannel(notify)` / `TtsChannel(synthesize)` / `EmailChannel(gateway, host=)` / `ImWebhookChannel(gateway, host=)`（[l5/channels.py](../../src/st_agent/l5/channels.py)）；`T-L5-002.2` → `DeliveryOrchestrator.dispatch / advance / mark_read / resend / ledger`（[l5/delivery.py](../../src/st_agent/l5/delivery.py)）
  - 日报 / 频控 / 疲劳：`T-L5-003.1` → `DailyReportBuilder.due / build / deliver`（[l5/daily_report.py](../../src/st_agent/l5/daily_report.py)）；`T-L5-003.2` → `FrequencyController.gate / admit`（[l5/frequency.py](../../src/st_agent/l5/frequency.py)）；`T-L5-003.3` → `FatigueMonitor.sweep / consume / answer`（[l5/fatigue.py](../../src/st_agent/l5/fatigue.py)）
  - 事件面：`T-L5-002.3` → `EventBus.publish / subscribe / subscribers`（[l1/events.py](../../src/st_agent/l1/events.py)）
  - 上游产生点：`T-L1-005` → `Scheduler.tick(now) / due_targets / run_forever` 与 `ScheduledRun`（`envelope` / `trace` / `skill_run_id` / `trace_id`，[l1/scheduler/](../../src/st_agent/l1/scheduler/)）；`T-L4-004.2` → `AnalyzeService.analyze` 产出的 `AnalyzeOutcome`（`envelope` / `result.opinions` / `map` / `view`，[l4/analyze.py](../../src/st_agent/l4/analyze.py)）；`T-L1-004` → 官方 Skill `sk_risk_alert` 的信封载荷（`alerts[]`，[l1/skills/official/ambient.py](../../src/st_agent/l1/skills/official/ambient.py)）
  - 反馈面：`T-L3-005.2` → `FeedbackCollector(sink=)` / `FeedbackEvent` / `FeedbackRecorded`（[l3/feedback/collector.py](../../src/st_agent/l3/feedback/collector.py)）
  - 凭据与出网：`T-L0-002` → `CredentialVault.use`（[l0/secrets/vault.py](../../src/st_agent/l0/secrets/vault.py)）；`T-L0-004` → `EgressGateway`（`channel_delivery` 类目已在白名单，[l0/net/gateway.py](../../src/st_agent/l0/net/gateway.py)）
  - 原生端口：`T-UI-002.2` → `ui.shell.notify.send_notification(title, body)`（由**生产入口**注入，[ui/shell/notify.py](../../src/st_agent/ui/shell/notify.py)）
- **输出**（本关卡交付的公共 API 与落盘位置）
  - [src/st_agent/app.py](../../src/st_agent/app.py)（改）：`_build_l2(..., events=)` / `_build_l3(..., events=, feedback_sink=)`（**只增**可选参数，缺省路径逐字节不变）· 新增 `_build_l5(runtime, l2, *, events, channels, now)` · 新增 `M3Runtime`（携 `m2` / `l5` / `events` / `rules`，并有 `m1` / `chat` / `store` 同形属性与 `tick(now)`）· 新增 `build_m3_runtime(root, passphrase, *, notify=None, synthesize=None, email_host="", webhook_host="", channels=None, now=_now, **kw) -> M3Runtime`。
  - [src/st_agent/l5/sources.py](../../src/st_agent/l5/sources.py)（新）：`MonitorRule` / `DEFAULT_MONITOR_RULES`（`sk_risk_alert` 等）· `signal_events_for_run(run, *, rules=)` · `DELIBERATION_RULE` · `signal_event_for_analysis(outcome, *, rule=)`——**纯函数**（同输入恒同事件序列，可离线复算）；文本过 [01 §6] 执行点 2，命中即显式抛错。
  - [src/st_agent/l5/errors.py](../../src/st_agent/l5/errors.py)（改）：增 `SignalEmissionError`。
  - [src/st_agent/l5/runtime.py](../../src/st_agent/l5/runtime.py)（改，**只增**）：`build_l5` 在接入真总线时把 `FeedbackRecorded` 订阅到疲劳面的 `consume`（未接总线 / 无疲劳面时不订阅，行为不变）。
  - [src/st_agent/__main__.py](../../src/st_agent/__main__.py)（改）：默认 `build_runtime=build_m3_runtime` · 注入 `notify=ui.shell.notify.send_notification` · 起**常驻驱动循环**（按 `--ambient-interval` 秒调 `runtime.tick(now)`，收尾时停线程）——循环只负责「何时 tick」，判定全在 `tick` 内。
  - [tests/integration/rig_m3.py](../../tests/integration/rig_m3.py)（新）+ `test_m3_delivery.py`（新）：M3 装配 rig（复用 `rig_m2`）+ GWT-1..9 端到端离线用例。
  - [tests/live/test_channel_delivery_live.py](../../tests/live/test_channel_delivery_live.py)（新）：真 SMTP / Webhook 端点（环境变量给）经网关投递（GWT-10）。
  - [tests/test_layering.py](../../tests/test_layering.py)（改）：`APP_ALLOWED_IMPORTS` 加 `l5`（[T-INT-003] 加 `l4` 的同型改动）。
- **不交付**（显式标注，非欠账）：跨进程推送（常驻后端 → 可分离 UI 客户端）、日历驱动的情境模式自动切换（[07 §2] 明写日历面未交付）、真实 TTS 合成面（本仓无该面 ⇒ 显式不可用）——三者均已在架构文档里标为**本层之外**。

## 可关闭的遗留
- **册内归属本任务项：无**——开工逐册核未闭区：[L0 册](../遗留问题/L0-遗留问题.md)（未闭 `C3` / `C5` 归属＝**人决**，`D1`–`D3` 归属＝**人**）、[L1 册](../遗留问题/L1-遗留问题.md)（`A`–`E` 五段全闭）、[L2 册](../遗留问题/L2-遗留问题.md)（三段全闭）、[L3 册](../遗留问题/L3-遗留问题.md)（`A` 段空，仅「已闭」节把本关卡引为下游）；[阻塞与未决](../阻塞与未决.md) `Q` 段 **0 open**。
- **任务层遗留清偿**（点名本关卡的三处，均随本批兑现）：① [T-L5-002.3](T-L5-002.3-平台事件投递机制与L5装配缝.md) ④ / [D-082](../决策日志.md) ④ 点名的「`SignalEmitted` 的真实上游产生点（L1 定时监控 / L4 Deliberation）」→ 由本关卡的规则面 + 接线清偿；② [T-L5-003](T-L5-003-每日报告去重频控推送疲劳监控.md) 批次遗留④「真实渠道与**到点触发**的接线」→ 由渠道接线 + `tick(now)` 与入口常驻循环清偿；③ [T-L5-003.3](T-L5-003.3-推送疲劳监控与答复回流.md) 遗留③「答复承接的第二条通道（`answer(...)`）在 UI 反馈入口接线后应只作降级路径」→ 由 `FeedbackCollector(sink=总线)` → `FeedbackRecorded` → 疲劳面订阅消费这条主通道清偿。

## 假设与前提
- **A1** 组合根仍落在 [`app.py`](../../src/st_agent/app.py)：新增 `build_m3_runtime` / `M3Runtime`，`_build_l2` / `_build_l3` 以**只增可选参数**吸收事件面，并令 [`test_layering.py`](../../tests/test_layering.py) 的 `APP_ALLOWED_IMPORTS` 加 `l5`（[T-INT-003] A1 的同型改动）。**若错**（要求另立组合根模块）：`app.py` 与层扫描用例返工、M3 装配面重做。**验证**：`test_app_is_composition_root_only` 全绿且新增断言「`app` 可装配 `l0–l5`」。
- **A2** 上游信号**产生规则住 L5**（`l5/sources.py` 的声明面 + 纯函数），L1 / L4 **零改动**；理由是 `SignalEmitted` 的负载结构本就由订阅方（L5）定义并校验（[01 §11]，[T-L5-001.1] 先行登记），规则放同处可保「一种形态一份真相」。**若错**（要求规则住产出层 L1 监控面 / L4 出口）：两个层模块返工 + 需动 [03] / [06] 契约小节。**验证**：GWT-2 / GWT-3 端到端用例；且 `tests/test_layering.py` 全绿（L5 只向下读 L1/L4 的鸭子面）。
- **A3** 到点驱动＝组合根给**显式时刻的纯函数** `M3Runtime.tick(now)`，**常驻循环住生产入口**（`__main__.py`）；判定（到点 / 越限 / 达阈值）全在 `tick` 内，故可离线复算（与 [07 §4–§6] 的纯函数口径一致）。**若错**（要求接进 L1 `Scheduler` 目标集）：须扩 `ScheduleTarget` 模型并为日报 / 疲劳各造一类目标，触 [03 §6] 契约与 L1 既有用例。**验证**：GWT-2 / GWT-4 / GWT-6 均以固定 `now` 复算；入口用例断言循环只调 `tick`。
- **A4** 原生通知端口由**生产入口**注入（`ui.shell.notify.send_notification`）——`app` 不得 import `ui`（层序表无 `ui`，[D-060] ⑤），而 [`__main__.py`] 是唯一允许同时 import `app` 与 `ui` 的模块（[T-UI-002.1]）。**若错**（要求组合根自持平台命令实现）：会在 `ui/shell/notify.py` 之外造第二份平台分支实现，两处必然漂移（[D-082] ③ 已否决该支）。**验证**：GWT-11 断言入口装配句柄；`test_layering.py` 的 `APP_ALLOWED_IMPORTS` 保持不含 `ui`。
- **A5** 云端渠道的**主机参数**（SMTP host / Webhook host）在 [01 §7] **无落值面**（渠道偏好族只登记「级别 → 有序渠道链」），故由 `build_m3_runtime` 的关键字给出、缺省空串 ⇒ 该渠道 **`health().available is False` 并点名缺口**，**不臆造端点**。**若错**（要求为该面新增配置登记）：触 [01 §7]（[铁律 8] 序），改动面扩大。**验证**：GWT-7 断言未给主机时渠道走显式不可用而非"假装送达"。
- **A6** 渠道 live 用例的端点取**环境变量**（`ST_AGENT_SMTP_*` / `ST_AGENT_WEBHOOK_URL`），无端点即 `skip` 并写明原因；**不**引本地 SMTP 替身进程。**若错**（要求以替身进程做真链路）：新增依赖与进程管理，且 CI 恒排除 `live` 使其收益有限。**验证**：GWT-10 的 live 用例在本机按有无端点分别给出 pass / skip（原因写进执行日志「验证」行）。

## 涉及契约
- [00-架构总览 §5](../../docs/技术架构-v2/00-架构总览.md) 端到端数据流（**第 4–7 步**——本关卡 GWT 的锚点）· [§1.1](../../docs/技术架构-v2/00-架构总览.md) 进程形态（后端常驻为主体）
- [01-平台共享契约 §1](../../docs/技术架构-v2/01-平台共享契约.md) 标识（`signal_id` / `delivery_id` / `feedback_id` 产生方）· [§5](../../docs/技术架构-v2/01-平台共享契约.md) `ResultEnvelope` · [§6](../../docs/技术架构-v2/01-平台共享契约.md) 中性化校验 · [§7](../../docs/技术架构-v2/01-平台共享契约.md) 配置元模型 · [§11](../../docs/技术架构-v2/01-平台共享契约.md) 事件清单与 `SignalEmitted` 负载 / 投递口径
- [07-L5 §1–§7](../../docs/技术架构-v2/07-L5-主动触达.md)（信号 / 预算 / 渠道 / 升级链 / 日报 / 去重频控 / 疲劳；**§1 本轮补「产生规则与产生点」**）
- [06-L4 §6](../../docs/技术架构-v2/06-L4-多视角推理.md) 触达联动（**本轮补落地口径**）
- [05-L3 §9](../../docs/技术架构-v2/05-L3-对话主入口.md) 反馈采集点 · [02-L0 §6](../../docs/技术架构-v2/02-L0-本地优先基座.md) 出网网关与审计 · [03-L1 §6](../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md) 定时调度

## 参考
- Story（What）：[story-07](../../docs/PRD-v2-Agent/story-07-ambient-delivery.md)
- 前置关卡：[T-INT-003](done/M2/T-INT-003-M2集成关卡多视角决策闭环.md)
- 装配范式：[tests/integration/rig_m2.py](../../tests/integration/rig_m2.py)（生产组合根 + 确定性注入）
- 上游裁定：[D-082](../决策日志.md)（产生点归本关卡）· [D-083](../决策日志.md)（L5 三叶口径）

## 备注
不重复单任务单测；只测**装配关系与跨层数据流**，渠道投递用 Fake 适配器离线跑，真实 SMTP/Webhook 标 `live`。实现细节留给代码 / commit / 执行日志。
