---
id: T-INT-006
parent: null
title: M5 集成关卡 · 受控自主闭环
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/00-架构总览.md
arch_link: "[00 §5](../../../../docs/技术架构-v2/00-架构总览.md)"
priority: P0
milestone: M5
depends_on: [T-AGT-001, T-AGT-002, T-AGT-003, T-AGT-004.1, T-AGT-004.2, T-AGT-004.3, T-AGT-005.1, T-AGT-005.2, T-AGT-006, T-AGT-007, T-L6-004.1, T-L6-004.2, T-UI-005.1, T-UI-005.2, T-INT-005]
status: done
decisions: [D-090, D-091, D-102]
verify: "全量 pytest 3886 passed / 16 deselected（846.91s）；live 子集 4 passed（真端点，无 skip）；verify_docs --strict 1–9 全 0 → 执行日志 [T-INT-006]"
---

# T-INT-006 · M5 集成关卡 · 受控自主闭环

## 目标

把 M5 分散在各任务里的交付**装配起来跑一次端到端**——[`T-AGT-001`](T-AGT-001-LLM工具调用通道.md) 的通道、[`T-AGT-002`](T-AGT-002-端点能力启动探测.md) 的能力档、[`T-AGT-003`](T-AGT-003-工具目录读面.md) 的目录、[`T-AGT-004`](T-AGT-004-循环运行时.md) 三叶的循环、[`T-AGT-005`](T-AGT-005-动作闸门接入.md) 的闸门、[`T-AGT-006`](T-AGT-006-第七类意图investigate与派发去向接线.md) 的意图与去向——接进 [`app.py`](../../../../src/st_agent/app.py) 的组合根，并在**生产入口** [__main__.py](../../../../src/st_agent/__main__.py) 生效。

单任务的 GWT 全是注入 Fake 的单测；**装配关系与跨层数据流**（L3 循环 → L1 执行 → L0 通道 → L6 授权 → L3 渲染）恰是它们集体覆盖不到的部分。

## 验收标准（Given-When-Then）

锚点取 [00 §5 端到端数据流](../../../../docs/技术架构-v2/00-架构总览.md) 与 [05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 的九段契约，**不重复**单任务已覆盖的单元行为。

- **GWT-1 · 端到端多步自主查证**：Given 装配好的 M5 运行时 + 一个 Fake 端点（先要求调 A、见结果后要求调 B、再结束），When 用户走一次 `investigate` 全流程（意图 → 澄清 → 确认 → 派发），Then 产出为 `ok` 信封，两步**都真的经 L1 执行**，且链上**恰好两条 `skill_run` 步**。
- **GWT-2 · 闸门真的在链上**：Given 运行期档为 `collaborative`（缺省），When 同一次派发，Then **一步不执行**、产出显式说明下一步需确认；Given 档切到 `autonomous` 且动作落 `autonomous-ok` 范围，Then 执行。
- **GWT-3 · 跨层端口未反转**：Given 装配后的组合根，When 扫 import，Then `l3/runtime/**` **无** `st_agent.l6` import、`l3/**` **无** `st_agent.l4` import（`test_layering.py` 全绿），L6 授权面经**鸭子端口**注入。
- **GWT-4 · 能力档来自探测而非硬编码**：Given 端点在启动时被判为**不支持工具调用**，When 派发 `investigate`，Then `unavailable` + 显式降级告知；When 端点为支持，Then 正常跑——**两次断言取的是探测结果，不是 `LLM_CAPABILITY` 的缺省**。
- **GWT-5 · 留痕端到端可查**：Given GWT-1 的一次循环，When 取留痕，Then 可得 `agent_run_id` 的**步数 / 终止原因 / 上界**，并可经 `explain` 去向逐步展开完整 Trace。
- **GWT-6 · 既有链路不回归**：Given 装配后的运行时，When 跑 M0–M4 的既有端到端用例，Then 全绿——**既有六类意图与 L0–L6 数据流逐字节不变**。
- **GWT-7 · 生产入口生效**：Given [__main__.py](../../../../src/st_agent/__main__.py) 的默认组合根，When 启动，Then 探测已执行、循环端口已注入（**不是只在测试装配里成立**）。

## 接口面

- **输入**（逐条给上游任务 id + 具体 API / 落盘位置）：
  - [`T-INT-005`](../M4/T-INT-005-M4集成关卡反思演进与生态闭环.md)（done）交付的 `M4Runtime` / `build_m4_runtime`（组合根的既有形态；[`app.py`](../../../../src/st_agent/app.py)）。
  - [`T-AGT-001`](T-AGT-001-LLM工具调用通道.md)（done）的通道面：`LlmClient.invoke(..., tools=)` · `StreamEvent`（`tool_call` 态）· `ToolCall`（[`l0/llm/client.py`](../../../../src/st_agent/l0/llm/client.py) / [`models.py`](../../../../src/st_agent/l0/llm/models.py)）。
  - [`T-AGT-002`](T-AGT-002-端点能力启动探测.md)（done）的探测面：`EndpointCapability.supports_function_calling` 由启动探测写回（`_probe_endpoint_capabilities`，[`l1/runtime.py`](../../../../src/st_agent/l1/runtime.py)）· `build_l1_runtime(llm_post=…)` 的 HTTP 发送口注入缝（离线 rig 经它喂脚本化端点）。
  - [`T-AGT-003`](T-AGT-003-工具目录读面.md)（done）的目录面：`SkillRegistry.tool_catalog() -> tuple[ToolEntry, ...]`（[`l1/skills/registry.py`](../../../../src/st_agent/l1/skills/registry.py)）。
  - [`T-AGT-004.1/2/3`](T-AGT-004.2-循环驱动、协议端口与上界.md)（done）的循环面：`NativeToolCallProtocol(llm=, endpoints=, endpoint_id=)` · `run_agent_loop(task=, endpoint_id=, protocol=, runner=, tools=, gate=, max_steps=, max_llm_calls=) -> LoopOutcome` · `conclude_agent_run(store, outcome) -> AgentRunReport` · `load_agent_run(store, agent_run_id) -> AgentRunRecord`（[`l3/runtime/`](../../../../src/st_agent/l3/runtime)）。
  - [`T-AGT-005.1/2`](T-AGT-005.2-逐步闸门与终止交还.md)（done）的闸门面：`RuntimeAuthorization.decide(action_id) -> ActionDecision`（[`l6/runtime_authorization.py`](../../../../src/st_agent/l6/runtime_authorization.py)）经 `L6Stack.agent_authorization` 取；`ActionGate(decisions=…)`（[`l3/runtime/gate.py`](../../../../src/st_agent/l3/runtime/gate.py)）。
  - [`T-AGT-006`](T-AGT-006-第七类意图investigate与派发去向接线.md)（done）的去向面：总线鸭端口 `investigations.investigate(confirmation, *, values=None, now=None) -> 含 envelope 的载荷`（[`l3/dispatch/bus.py`](../../../../src/st_agent/l3/dispatch/bus.py)）+ `_build_l3(..., investigations=…)` 注射点（[`app.py`](../../../../src/st_agent/app.py)）。
  - [`T-AGT-007`](T-AGT-007-循环上界开放为配置项.md)（done）的读取缝：`LoopBounds(store).steps()` / `.llm_calls()`（[`l3/runtime/bounds.py`](../../../../src/st_agent/l3/runtime/bounds.py)），句柄经 `_L3Stack.bounds` → `M1Runtime.bounds`。
  - [`T-UI-005.1`](T-UI-005.1-后端画布读面与编辑转交.md) / [`T-UI-005.2`](T-UI-005.2-前端画布页与渲染件.md)（done）与 [`T-L6-004.1`](T-L6-004.1-L6侧翻译与Studio交接受体.md) / [`.2`](T-L6-004.2-组合根装配与表现层交接线.md)（done）：本任务**只断言不回归**，不动其交付面。
  - 契约：[00 §5](../../../../docs/技术架构-v2/00-架构总览.md) · [00 §1.1](../../../../docs/技术架构-v2/00-架构总览.md) · [05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md) · [05 §4](../../../../docs/技术架构-v2/05-L3-对话主入口.md) · [05 §3.2](../../../../docs/技术架构-v2/05-L3-对话主入口.md) · [02 §4](../../../../docs/技术架构-v2/02-L0-本地优先基座.md) · [01 §1](../../../../docs/技术架构-v2/01-平台共享契约.md) · [01 §5](../../../../docs/技术架构-v2/01-平台共享契约.md) · [01 §7](../../../../docs/技术架构-v2/01-平台共享契约.md)。
  - 先例（装配与替身范式）：`tests/integration/rig_m3.py` / `rig_m4.py` / `test_m4_reflection_eco.py`（真装配 + 脚本化出网面）· `tests/l1/test_runtime_llm_probe.py` 的 `ScriptedPost`（`llm_post` 替身，按脚本回「真工具调用 / 纯文本」）· `tests/l3/test_agent_loop.py`（真 `SkillRunner` + 注入协议 / 闸门）。
- **输出**（本任务交付的公共 API 与落盘位置）：
  - **新增** [`app.py`](../../../../src/st_agent/app.py) 的 M5 组合根三件：
    - `M5Runtime`（frozen）——裹 `m4: M4Runtime` + `investigations`（循环端口句柄），委托 `m1` / `m2` / `m3` / `l6` / `chat` / `reflection` / `ecosystem` / `events` / `store` / `tick`（同前几个 `MnRuntime` 的裹法）。
    - `InvestigationOutcome`（frozen）——总线 `investigate` 去向的**载荷形态**：`envelope`（总线只认它）+ `report: AgentRunReport` + `trace: Trace`（GWT-5 的 `explain` 展开面）。
    - `_AgentInvestigator`——把 `(confirmation, *, values, now)` 收敛为「取任务 → `run_agent_loop` → `conclude_agent_run`」的一次调用（授权口经 `ActionGate(authorization)`，上界经 `LoopBounds`）。
    - `build_m5_runtime(...)`——M5 **生产组合根**（复用 `_build_l2/_build_l4/_build_l5/_build_l6/_build_l3/_build_eco` 私有栈构造器；在 L6 与 L3 之间装配循环端口并注入总线）。
    - **只增** `_build_l3(..., bounds=None)` 可选参数（单实例 `LoopBounds`：既注册 `investigate.*` 族、又供循环取用）；**只增** `__all__` 的 `M5Runtime` / `build_m5_runtime`。
  - **只增** [`l3/runtime/protocol.py`](../../../../src/st_agent/l3/runtime/protocol.py)：`NativeToolCallProtocol.tool_calls_supported()` 在**端点未登记**时返回 `False`（fail-closed，不抛）——生产根未配端点时该去向回 `unavailable` 而非抛异常（见假设 A5）。
  - **只增** [`__main__.py`](../../../../src/st_agent/__main__.py)：`build_ambient_runtime` 换用 `build_m5_runtime`（GWT-7「生产入口生效」）。
  - 测试：**新增** `tests/integration/rig_m5.py`（真装配 rig：播种数据面 + 脚本化端点经 `llm_post` + 确定性意图理解器）· **新增** `tests/integration/test_m5_autonomy.py`（GWT-1..7）· **新增** `tests/live/test_llm_tools_live.py`（真端点下的工具调用链路，`@pytest.mark.live`）。
  - **不动**：总线路由表 / 各层既有交付面 / `tests/test_layering.py`（A2）。

## 可关闭的遗留

- 无。开工 ④ 逐册读各遗留册未闭区：L3 册 **0 条未闭**（`A1`–`A3` 全闭；`A3` 关闭记录里「把登记项喂给 `run_agent_loop` 归 `T-INT-006`」是**已闭条目内的前向指针**，非未闭项）· L6 册 `A` / `B` 两段空 · L1 册 `A`–`E` 五段空 · L0 册 0 条未闭 · L2 册 `A`–`C` 空 · L5 册 `A1` 属「待人（真实端点凭据，Agent 无法自造）」**非本任务范围**。基线 [`verify_docs.py`](../../../tools/verify_docs.py)`--strict` 检查 8 为 **0 处**，无「归属＝本任务」或「解封条件＝本任务 `done`」的未闭条目。

## 假设与前提

- **A1 · 端到端用**脚本化端点**（经 `llm_post` 注入）而非依赖真实云端**——前提：既有集成关卡的范式（`rig_m3.py` / `rig_m4.py` 注入替身）保证**离线可重复**；且 `llm_post` 只换 HTTP 发送口、**不换** `LlmClient` / transport / 网关 / 探测本身，故探测与工具调用链路仍是真的。真链路另由 live 子集覆盖。
  **若错的影响**：若端到端必须打真端点，用例会**不稳定且不可离线跑**，CI 也无法覆盖（GWT-1..6 失去可重复性）。
  **验证方式**：GWT-1..7 全部离线成立；GWT-7 只断言「探测真的跑过、循环端口真的注入」，不打真端点。
- **A2 · `APP_ALLOWED_IMPORTS` 不需扩充**——前提：M5 的源码落在 `src/st_agent/l0` / `l1` / `l3` / `l6`，**均在既有允许集 `{contracts,l0..l6,eco}` 内**；`T-AGT-*` 是任务前缀组，**不对应新的源码层**。
  **若错的影响**：若真需要新源码层，须动 [`tests/test_layering.py`](../../../../tests/test_layering.py) 与层表——**那是架构级返工**。
  **验证方式**：`test_layering.py` **不改**即绿（GWT-3）；已开工读码确认该文件 `APP_ALLOWED_IMPORTS` 含 `l0`–`l6` + `eco`。
- **A3 · live 子集的触发面覆盖本任务**——前提：[工作流](../../../工作流.md) §测试分层列的联网面触发点是 `src/st_agent/l0/llm/**` / `l0/net/gateway.py` / `l1/runtime.py` 的 LLM 装配段；本任务**不动**这三处（只经 `llm_post` 注入缝消费），但 GWT-7 的**真实路径**（未注入替身时）正落在它们之上，故仍须跑 live。
  **若错的影响**：若漏跑 live，**CI 绿灯不覆盖真实链路**（这正是该条存在的原因）。
  **验证方式**：收工的验证行必须含 live 子集结果；本机无端点而 skip 时**写明 skip 与原因**（**不许默默略过**）。
- **A4 · 循环的任务文本取自确认卡 `values["task"]`，回落 `confirmation.target`**——前提：`investigate` 按 [05 §3.2](../../../../docs/技术架构-v2/05-L3-对话主入口.md) **无意图级参数声明**（确认卡只有「意图」一条目），故用户原话由理解器落 `target`、或派发时经 `values` 覆盖；两者皆空即回 `validation_failed`（**不臆造**一个任务跑循环）。
  **若错的影响**：若真实 LLM 理解器不产出该字段，生产链路的 `investigate` 会回 `validation_failed`（**可见地失败**，不是静默跑错）——属 [05 §3.2](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 理解端口的取值面问题，非本装配面缺陷。
  **验证方式**：GWT-1 的 rig 经 `IntentDraft(intent="investigate", target="<用户原话>")` 走**真**理解→澄清→确认→派发全流程；缺失情形由用例钉住回 `validation_failed`。
- **A5 · 端点未登记时 `tool_calls_supported()` 返回 `False` 而非抛异常**——前提：[02 §4](../../../../docs/技术架构-v2/02-L0-本地优先基座.md) 的「缺省不得假定为真」——未登记的端点显然不声明支持工具调用，返回 `False`（fail-closed）即该口径的自然延伸；未如此时生产根在**未配 LLM 端点**的机器上派发 `investigate` 会抛异常（500）而非回 `unavailable`（违反 [05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 的「端点不可用 → fail-closed + 显式降级告知」）。
  **若错的影响**：若判为「不该动 T-AGT-004.2 的交付件」，则改由 M5 侧包一层守卫（等价语义、多一处包装）；**不影响 GWT 成立**，只影响落点选择。
  **验证方式**：GWT-4 的「不支持」支 + 新增一条「未登记端点」用例：派发 `investigate` 回 `unavailable` + `AGENT_UNAVAILABLE_NOTICE`，**不抛**；`tests/l3/test_agent_loop.py` 全绿。
- **A6 · 授权档位的「可自主」用例经 `set_grading` + `set_tier` 构造**——前提：清单 `DEFAULT_RISK_GRADING` 把 `skill.` 前缀恒判 `collaborative-required`（[`l6/authorization.py`](../../../../src/st_agent/l6/authorization.py)），故**任何**工具调用在缺省清单下都到不了 `autonomous-ok`；GWT-2 的「动作落 `autonomous-ok` 范围」只能由 rig 经 `EvolutionAuthorization.set_grading(...)` 造一条 `skill. → autonomous-ok` 的清单 + `agent_authorization.set_tier("autonomous")` 达成——**这恰是 [01 §7](../../../../docs/技术架构-v2/01-平台共享契约.md) 共享清单的设计用法**（清单可变、档位独立）。
  **若错的影响**：若负责人认为「`skill.` 恒须协作、不可经清单放宽」，则 GWT-2 的第二支需改写为「档位切换后仍交还确认」——**用例语义变更**。
  **验证方式**：GWT-2 两支断言分别取 `needs-confirmation`（缺省档）与执行（`autonomous` 档 + 放宽清单）；并断言清单与档位是**两条独立条目**（同 [D-090](../../../决策日志.md) ①）。
- **A7 · 生产入口的探测步骤在无 `llm_env` 时不产任何探测调用**——前提：探测住 `build_l1_runtime` 的**引导装载分支**（`transport is None and llm_config is not None`），无三键 ⇒ 该分支不跑 ⇒ `endpoints.list_endpoints()` 为空 ⇒ 探测循环零次（**不阻塞启动**）。
  **若错的影响**：若装配在无端点时失败或抛异常，生产入口会在未配 LLM 的机器上起不来——**严重回归**。
  **验证方式**：GWT-7 的用例以**无 `llm_env`** 起默认根，断言装配成功、循环端口已注入、`investigate` 回 `unavailable`（不抛）；另以**有脚本化 `llm_env` + `llm_post`** 起一次，断言探测真的跑了（端点能力档被写）。

## 涉及契约

- [00 §5 端到端数据流](../../../../docs/技术架构-v2/00-架构总览.md) · [00 §1.1 进程形态](../../../../docs/技术架构-v2/00-架构总览.md)
- [05 §10 自主查证循环](../../../../docs/技术架构-v2/05-L3-对话主入口.md) · [05 §4](../../../../docs/技术架构-v2/05-L3-对话主入口.md)
- [02 §4 LLM 调用抽象](../../../../docs/技术架构-v2/02-L0-本地优先基座.md) · [01 §5](../../../../docs/技术架构-v2/01-平台共享契约.md) · [01 §7](../../../../docs/技术架构-v2/01-平台共享契约.md)
- [铁律 7](../../../工程宪法.md)（跨层经鸭子端口）· [铁律 10](../../../工程宪法.md)（无留痕不关闭）

## 参考

- [D-090](../../../决策日志.md)（十项决策与冻结范围）· [D-091](../../../决策日志.md)（契约先行）
- 先例：[`T-INT-003`](../M2/T-INT-003-M2集成关卡多视角决策闭环.md) · [`T-INT-004`](../M3/T-INT-004-M3集成关卡主动触达投递闭环.md) · [`T-INT-005`](../M4/T-INT-005-M4集成关卡反思演进与生态闭环.md)
- 调研：[Agent 自主能力调研报告](../../../../docs/Agent自主能力调研报告.md) §11.2

## 备注

**live 子集**：本关卡动的正是 [工作流](../../../工作流.md) §测试分层点名的**真实 LLM 出网面**（`l0/llm/**` + `l1/runtime.py` 的 LLM 装配段），故收工时除跨层套件外**必须**跑 `python -m pytest -m live tests/live/test_llm_live.py`（及本任务新增的工具调用 live 用例），结果写进执行日志的**验证**行——本机无可用端点而 skip 时，把 skip 与原因一并写明。CI 恒排除 `live`，**绿灯不覆盖真实链路**。
