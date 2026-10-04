---
id: T-INT-002
parent: null
title: M1 集成关卡 · 首次可对话
story: ../../docs/PRD-v2-Agent/README.md
arch: ../../docs/技术架构-v2/00-架构总览.md
arch_link: "[00 §5](../../docs/技术架构-v2/00-架构总览.md)"
priority: P0
milestone: M1
depends_on: [T-L0-016, T-L0-017.1, T-L0-017.2, T-L0-018.1, T-L0-018.2, T-L1-011, T-L2-001.1, T-L2-001.2, T-L2-001.3, T-L2-002.1, T-L2-002.2, T-L2-002.3, T-L2-003, T-L2-004.1, T-L2-004.2, T-L2-004.3, T-L3-001.1, T-L3-001.2, T-L3-001.3, T-L3-002.1, T-L3-002.2, T-L3-003.1, T-L3-003.2, T-L3-003.3, T-L3-004.1, T-L3-004.2, T-L3-005.1, T-L3-005.2, T-UI-001.1, T-UI-001.2, T-UI-001.3, T-INT-001]
status: doing
decisions: [D-065]
verify: 全量 pytest **2055 passed / 0 failed / 8 deselected**（463s）；`tests/integration/test_m1_chat.py` **9 条**（GWT-1..6 离线端到端）· `tests/live/test_llm_chat_live.py` **1 passed**（生产根默认理解器经真端点收敛意图、审计恰一条、明文不落盘）· `verify_docs.py --strict` 检查 1–9 全 0 · 真 HTTP 回环往返（401/对话/降级三态）· 红-绿已验（摘生产根内取数面 `bind` → GWT-2 `dependency_failed`）· 入库 `6f9f77d` + [PR #71](https://github.com/Dline6/ST-Agent/pull/71)（`8b1a81a`）· 见执行日志 [T-INT-002]
---

# T-INT-002 · M1 集成关卡 · 首次可对话

## 目标
把 M1 分散在各任务里的交付**装配成一条真能对话的端到端链路**——新增**生产组合根**（现仓库无任何生产装配根，L2/L3 全在测试 fixture 里拼）把 L0→L1→L2→L3 装配到**同一个 `Store`**，在回环服务上开**对话端点**，并证明 [00 §5 反向流](../../docs/技术架构-v2/00-架构总览.md)（用户发起：意图 → 澄清 → 任务派发 → 结果渲染 → 决策记录写回 L2）在装配态下逐段兑现。装配关系与跨层数据流是单任务 GWT（全注入 Fake 的单测）集体覆盖不到的部分——本关卡兜住它。

## 验收标准（Given-When-Then）
- **GWT-1 生产装配根可用**：Given 全新空目录 + 口令，When 经 `build_m1_runtime`（复用 `open_runtime` 装 L0/L1，再在同一 `store` 上层叠 L2 + L3），Then 官方 Pack 可列出 · L2 图谱可读写（`memory` 分区）· L3 会话/意图/派发总线/裁决/反馈各编排器**已用真实依赖构造**（`matcher`/`descriptors` 接 L1 `SkillRunner`/`SkillRegistry`，`runner`/`configs`/`adjudications` 注满 `DispatchBus`），非注入 Fake。
- **GWT-2 反向流端到端（query 去向）**：Given 已装配运行时（离线注入**确定性理解器**），When 用户输入一句话（`SessionStore` 落一条 user 消息）→ `understand` → `clarify` → `confirm` → 确认卡经用户确认 → `DispatchBus.dispatch`，Then 意图 `query` 走**注入的 L1 `SkillRunner`** 跑通一次官方 Skill、信封**原样透出**、`Trace` 携派发链、`explain` 去向可对同一 `trace` 调 `describe_trace` 出 `trace_timeline` 描述件并过 `ui` 中性化门。
- **GWT-3 配置草稿去向（configure）**：Given `configure` 意图的确认卡，When 派发，Then 经 `ConfigDraftProtocol.generate` 产出 `ConfigDraft`、`dual_channel_view` 出双通道视图。**如实标注**：草稿**落值**依赖的统一 `ConfigRegistryPort` 门面**在仓库不存在**（[L1 册 `E1`](../遗留问题/L1-遗留问题.md) 待人立项）——本关卡只证**生成 + 双通道**，不假装已落 `change_id`。
- **GWT-4 冲突裁决联动（memory_op 裁决支）**：Given 一次 `inferred` 写入命中既有节点触发冲突，When `ConflictQueue.event_for` 产出 `MemoryConflictDetected`、经关卡**在进程内直递** `ConflictAdjudicator.from_event`，Then 出裁决卡 → 用户 `submit(decision=accept)` → 经 `ConflictQueue.resolve` 落 L2（新增节点 + `evolves_from` 边）；非 `pending` 项不得重复裁决。
- **GWT-5 memory_op 偏好写入支（本关卡承接 [05 §9](../../docs/技术架构-v2/05-L3-对话主入口.md) / [D-062](../决策日志.md) 显式指派给本关卡的欠账）**：Given 用户在对话中**显式表达偏好**（`memory_op` 且无待裁决冲突），When 组合根的**薄对话门面**处理该意图，Then 经 `MemoryWriter.add_node`（`source=user_stated` 直写、不过白名单门，[04 §3.2](../../docs/技术架构-v2/04-L2-记忆图谱.md)）落 L2 并回带 `memory_node_id` 的信封；裁决支路径不变（门面只补偏好写入这一条边，不改 `DispatchBus`/`ConflictAdjudicator` 已交付代码）。
- **GWT-6 失败 / 空 / 不可用三态渲染**：Given `analyze` 去向（`T-L4-002` 未开工）与端点未就绪场景，When 派发 / 出信封，Then `analyze` **如实报未接线 + `owner=T-L4-002`**（不伪造）；三态经 `envelope_payload` 附 `render` 语义送达 `POST /api/chat` 的 JSON 响应，与 `GET /api/health` 同一条出站点。
- **GWT-7 真实链路（联网面强制项）**：本关卡组合根默认装配真实 `LlmIntentUnderstander`，动到**真实 LLM 出网面**——离线 GWT-1..6 进默认 `pytest`（注入确定性理解器 + stub 传输，CI 可重复）；另**必跑** `python -m pytest -m live tests/live/test_llm_live.py`（真端点走通发送器），结果写进执行日志**验证**行，无可用端点而 `skip` 要写明原因。
- **回归**：默认 `pytest` **全量**绿（集成关卡强制全量，见[工作流「测试分层」](../工作流.md)）；`verify_docs.py --strict` 检查 1–9 全 0；本关卡用例全部离线可跑（真实网络部分标 `live`）。

## 接口面
- **输入**（逐条点名上游任务 id + 具体 API / 落盘位置）
  - 装配入口：`T-L1-011` → `open_runtime` / `build_l1_runtime` / `L1Runtime`（`store`/`llm`/`runner`/`skills`/`gateway`/…）（[l1/runtime.py](../../src/st_agent/l1/runtime.py)）
  - L2 记忆面（全落 `memory` 分区，留痕/队列落 `execution_log`）：`T-L2-001.1` → `MemoryGraph(store)`；`T-L2-001.2` → `MemoryWriter(graph)`；`T-L2-001.3` → `MemoryReader(graph)`（`query(SliceQuery)`）；`T-L2-002.1` → `WritePolicy(store)`；`T-L2-002.2` → `ConflictQueue(graph, writer, policy)`（`detect`/`propose`/`resolve`/`event_for`）；`T-L2-002.3` → `ConfidenceModel(graph)`；`T-L2-003` → `MemoryDeleter(graph)`；`T-L2-004.1` → `OnboardingProtocol(graph, writer)`
  - L3 编排面（会话落 `chat_history`）：`T-L3-001.1` → `SessionStore(store)`（`append`/`assemble_context`）；`T-L3-001.3` → `CommandRegistry`；`T-L3-002.1` → `IntentProtocol(understander=, commands=, matcher=runner, descriptors=skills)` + `LlmIntentUnderstander(llm, "cloud-main")`（[l3/intent/protocol.py](../../src/st_agent/l3/intent/protocol.py)）；`T-L3-002.2` → `DispatchBus(runner=, configs=, adjudications=)`（[l3/dispatch/bus.py](../../src/st_agent/l3/dispatch/bus.py)）；`T-L3-003.1` → `ConfigDraftProtocol(descriptors=skills, workflow=WorkflowDraftBuilder(...))`；`T-L3-003.2` → `ConfigDraftHandling(descriptors=, registry=)`（`registry` 无真实门面 → fail-closed，见 GWT-3）；`T-L3-005.1` → `ConflictAdjudicator(queue=, graph=)`（[l3/conflict/adjudication.py](../../src/st_agent/l3/conflict/adjudication.py)）；`T-L3-005.2` → `FeedbackCollector(sink=None)`（L6 未开工 → 采集返回不送达）
  - L3 渲染面：`T-L3-004.1/.2` → `describe_trace` / `describe_context_card` / `describe_draft` / `describe_adjudication`（[l3/render/describe.py](../../src/st_agent/l3/render/describe.py)）
  - UI 出站点：`T-UI-001.2` → `serve` / `build_ui` / `UiApp`（`envelope_payload` 附 `render`；`api_description` 过 `NeutralityGate`）（[ui/server.py](../../src/st_agent/ui/server.py) / [ui/app.py](../../src/st_agent/ui/app.py)）；`T-UI-001.3` → `ui/registry.py` `slot_gaps`（[ui/registry.py](../../src/st_agent/ui/registry.py)）
- **输出**（本关卡交付的公共 API 与落盘位置）
  - `src/st_agent/app.py`（新）：`build_m1_runtime(root, passphrase, *, create, market_query=None, sender=None, transport=None, llm_env=None, dotenv_path=None, understander=None, now=None, **l1_kwargs) -> M1Runtime`；`M1Runtime` 携 `runtime`/`store` + L2/L3 各门面 + 薄对话门面 `chat`（`handle_turn`：输入→understand→clarify/confirm→dispatch，含 memory_op 偏好写入支）。**app 非第七层**，`build_m1_runtime` 复用 `open_runtime` 不复制 L0/L1 装配逻辑。
  - `src/st_agent/ui/**`（改）：`build_ui(..., chat=None)` 与 `UiApp.api_chat(body) -> dict`；`server.py` 增 `do_POST` 路由 `POST /api/chat`（`/api/*` 仍要令牌，同 `GET`）。`chat` 端口为鸭子类型，`ui` **不 import `app`**（否则破 `test_ui_is_client_only`）。
  - `tests/integration/rig_m1.py` + `test_m1_chat.py`（新）：M1 装配 rig（复用 `rig.py` 的延迟绑定 `MarketData` 与播种 SQL）+ GWT-1..6 端到端离线用例，进默认 `pytest`。
  - `tests/live/`：GWT-7 的真实 LLM 冒烟（既有 `test_llm_live.py` 已覆盖发送器；关卡经真 `LlmIntentUnderstander` 再走一次，或复用其结论并在日志注明）。
  - 口径接线：`tests/test_layering.py` 新增用例约束 `src/st_agent/app.py` 的组合根性质（见 A2）。

## 假设与前提
- **A1** 装配 `DispatchBus`/`IntentProtocol` 所需的 `matcher` / `descriptors` 端口，由 L1 现成的 `SkillRunner.match(query)` 与 `SkillRegistry.get(skill_id)` **直接满足**（[runner.py:152](../../src/st_agent/l1/runner/runner.py) / [registry.py:291](../../src/st_agent/l1/skills/registry.py) 的鸭子类型即 L3 注释所称「如 L1 的 SkillRunner / SkillRegistry」）。若错的影响：`query` 去向与澄清问项无源，GWT-2 退化。验证方式：装配断言 `bus` 的 `_runner is runtime.runner` 且一条 `configure`/`query` 派发跑通。
- **A2** `app` 是**顶层组合根、不是第七层**——不进 [`test_layering.py`](../../tests/test_layering.py) 的 `LAYER_ORDER`（否则把「不是层」反向编码，同 D-063 对 `ui` 的处理）。它**向下 import L0/L1/L2/L3** 属装配、合法；`ui` 不 import `app`（破 `test_ui_is_client_only`），改由 `build_ui(chat=...)` 注鸭子端口。若错的影响：装配根被误当层参与向下校验，或 `ui→app` 造成反向依赖。验证方式：新增用例断言「无层 import `st_agent.app`」「`app` 只向下依赖」，并跑 `test_only_downward_dependencies` / `test_ui_is_client_only` 全绿。
- **A3** **无事件总线**——`ConflictQueue.event_for` 产出 `MemoryConflictDetected`、`ConflictAdjudicator.from_event` 消费，但仓库无 `EventBus`/`publish`（01 §11 只定事件形态与产生方，投递机制未立）。本关卡**在进程内直递**事件对象以证明两端接口对得上（装配验证），**不造总线**（总线归属另待人立项，登记于收工「遗留」行）。若错的影响：跨进程投递未证——但反向流在单进程内本就由对话门面直调，不依赖 broker。验证方式：GWT-4 用例把 `event_for` 返回值直接喂 `from_event`，断言出卡。
- **A4** 离线 GWT 用**注入的确定性理解器**（脚本化产出 `IntentDraft`）而非真实端点——与单任务测试口径一致（[00 §5] 反向流的装配性，非理解能力本身），CI 无端点也恒绿；真实 LLM 出网由 `-m live` 子集兜（GWT-7）。`build_m1_runtime` 默认装真实 `LlmIntentUnderstander`，测试经 `understander=` 覆写为确定性实现。若错的影响：离线关卡依赖本机 `.env` 有无（CI 无 `.env` 会挂）。验证方式：离线用例传 `understander=fake` 且断言不触网；`live` 用例用真端点。
- **A5** `configure` 的**落值**支依赖统一 `ConfigRegistryPort` 门面，**不存在**（[L1 册 `E1`](../遗留问题/L1-遗留问题.md) 待人立项；`ConfigDraftHandling.accept` 的 `registry=` 缺省 fail-closed）。本关卡只证 `ConfigDraftProtocol.generate` + `dual_channel_view`，**不**把 `accept` 落成 `change_id`（不假装完成）。若错的影响：若误当作已接门面，会伪造「配置已生效」。验证方式：GWT-3 断言草稿与双通道视图产出、`accept` 走 `REGISTRY_ABSENT_REASON` 的显式未接线告知。
- **A6** 对话门面对**在途草稿/确认卡**按 `session_id` 持于**进程内**（`build_m1_runtime` 返回的运行时是长生命周期对象，`serve()` 单进程内多次 HTTP 调用共享它）——M1 单进程形态成立，跨进程/重启续接不在本关卡。若错的影响：多轮确认会丢在途态；但关卡用例同进程内完成 send→confirm→dispatch，不触发。验证方式：GWT-2 用一次对话门面往返即完成全链，不依赖持久在途态。

## 可关闭的遗留
- **无册内归属本任务项**（开工逐册核 [L0](../遗留问题/L0-遗留问题.md) / [L1](../遗留问题/L1-遗留问题.md) / [L2](../遗留问题/L2-遗留问题.md) / [L3](../遗留问题/L3-遗留问题.md) 未闭区：`A`–`F` 各段归属均非本任务；`L1 D2` 归 `T-L3-006`/`T-ECO-002`；`L1 E1`（统一配置门面）/ `L2 C1`（券商账单导入）/ `L3 A1`（Pack 承载快捷指令）**待人立项**、解封条件非本任务 `done`）。**但 [05 §9](../../docs/技术架构-v2/05-L3-对话主入口.md) / [D-062](../决策日志.md) 把 `memory_op` 偏好写入支的组合根装配显式指派给本关卡**——由 GWT-5 兑现（非册内条目，是任务/契约层的历史欠账，本批清偿）。

## 涉及契约
- [00-架构总览 §5](../../docs/技术架构-v2/00-架构总览.md) 端到端数据流（**反向流**——本关卡 GWT 的锚点）
- [01-平台共享契约](../../docs/技术架构-v2/01-平台共享契约.md) §4 Trace / §5 ResultEnvelope / §11 事件（`MemoryConflictDetected` / `FeedbackRecorded`）/ §12 UI 描述
- [04-L2 §3–§5](../../docs/技术架构-v2/04-L2-记忆图谱.md)（写入路径 / 冲突裁决 / 置信度）
- [05-L3 §3–§9](../../docs/技术架构-v2/05-L3-对话主入口.md)（意图 / 派发 / 配置草稿 / Generative UI / 冲突裁决与反馈）

## 参考
- Story（What）：[PRD 索引](../../docs/PRD-v2-Agent/README.md)
- 前置关卡：[T-INT-001](done/M0/T-INT-001-M0集成关卡骨架打通冒烟.md)
- 装配范式：[tests/integration/rig.py](../../tests/integration/rig.py)（延迟绑定取数面 + 真 Store）

## 备注
不重复单任务单测；只测装配关系与跨层数据流。离线用例进默认 `pytest`、真实网络标 `live`。实现细节留给代码 / commit / 执行日志。
