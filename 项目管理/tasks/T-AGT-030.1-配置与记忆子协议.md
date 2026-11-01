---
id: T-AGT-030.1
parent: T-AGT-030
title: 配置与记忆子协议
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P1
milestone: M11
depends_on: [T-AGT-029.1]
status: done
decisions: [D-154, D-160]
verify: 执行日志 [T-AGT-030.1]——配置草稿卡 / 裁决卡两动作接入循环，`card_ready` 交还首卡，五动作 autonomous-ok；范围 1461 passed（首轮并集 2794/1 的预算回归已修）、定向 90 passed、verify_docs --strict PASS、dev 面 config_draft_card 渲染正常；实现 commit 83072c7
---

# T-AGT-030.1 · 配置与记忆子协议

## 目标
`configure` 与 `memory_op` 变为循环可调动作：调用即产出配置草稿卡 / 裁决卡并交还确认；记忆红线不可绕过。

## 验收标准（Given-When-Then）
- GWT-1：Given 循环判断要改配置，Then 产出配置草稿卡并交还确认。
- GWT-2：Given 记忆写入，Then 出裁决卡，红线不可绕过。

## 接口面

**输入**

- `ConfigDraftProtocol`（`src/st_agent/l3/config/draft.py`，组合根 `_build_l3` 装配）：既有 `generate(confirmation, structure)` 的校验、`ConfigDraft` / `OpenQuestion` / `open_questions` 复用；本叶新增 `compose(...)` 作为**循环动作的合成面**（同一套校验；不要求派发前的意图确认卡——循环交还的草稿卡本身即确认面）。
- `ConflictAdjudicator`（`src/st_agent/l3/conflict/adjudication.py`，组合根 `_build_l3` 装配）：`card_for(conflict_id)` / `cards()` 产出 `ConflictAdjudication`；裁决写入仍只经 `submit(..., confirmed_by="user")`，本叶**不新增写入路径**。
- `describe_draft` / `describe_adjudication`（`src/st_agent/l3/render/describe.py`）：草稿 / 裁决卡 → `config_draft_card` / `conflict_adjudication_card` 描述件。
- `run_agent_loop`（`src/st_agent/l3/runtime/loop.py`）：控制动作拦截点（与 `sk_plan_update` / `sk_ask_user` / `sk_spawn_agent` 同型）。
- `install_builtins` / `BUILTIN_PRIMITIVES`（`src/st_agent/l1/builtin/install.py`）：把两条动作登记进**同一个**工具目录（描述体＝模型可见面）。
- 风险分级清单（`src/st_agent/l6/authorization.py` 的 `DEFAULT_RISK_GRADING`）：动作标识 `skill.sk_configure` / `skill.sk_memory_op`。

**输出**

- **工具目录**：新增内置描述体 `sk_configure` / `sk_memory_op`；直接调用回 `unavailable` + 点名循环，不在循环外另开执行路径。
- **L3 适配面**：新增 `src/st_agent/l3/runtime/subprotocols.py`——`SubprotocolPorts`（`configs` / `adjudicator` / `deliberations` / `trainings`）与 `handle(tool_name, arguments, *, trace) -> SubprotocolResult`；返回 `ResultEnvelope` 与可交还的卡片描述。
- **循环**：`run_agent_loop(..., subprotocols=...)`；`sk_configure` / `sk_memory_op` 成功产卡即终止，新终止原因 **`card_ready`**，`LoopOutcome.cards` 携带描述件（`UiDescription` 载荷）。
- **门面 / 表现层**：`InvestigationOutcome.cards`；`DialogFacade` 把首张卡放进响应的 `description`（既有 `ChatSurface._chat_turn_payload` 落助手轮 payload 并渲染），不新增端点。
- **风险清单**：两条动作**显式登记为 `autonomous-ok`**——只产卡 / 读卡、不落值；真正的写入（配置接受、记忆裁决）仍走既有用户确认路径。
- **契约**：[05 §3.1](../../docs/技术架构-v2/05-L3-对话主入口.md) 子协议动作表、[§5](../../docs/技术架构-v2/05-L3-对话主入口.md) 循环合成面、[§9](../../docs/技术架构-v2/05-L3-对话主入口.md) 裁决卡、[§10](../../docs/技术架构-v2/05-L3-对话主入口.md) 动作与 `card_ready`；[01 §7](../../docs/技术架构-v2/01-平台共享契约.md) 白名单键空间补两条动作标识。

## 可关闭的遗留
无（2026-11-01 逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区：L0 余 `C3`/`C5`、L5 余 `A1` 皆**人决**，L1–L3 与 L6 无未闭项；无归属本任务的条目，检查 8 = 0）。

## 假设与前提
- **A1**：两条动作**无副作用**（只产卡 / 读卡，不写配置、不写记忆），故显式登记 `autonomous-ok`；真正写入仍走既有确认路径。若错 → 闸门会拦下动作、卡产不出来，须改为「待确认步 + 续跑」形态或换装风险类。验证：`tests/l3/test_agent_subprotocols.py` 的闸门用例 + `tests/l6/test_runtime_authorization.py` 的清单断言。
- **A2**：`card_ready` 是**新的终止原因**（与 `finished` / `needs_confirmation` 并列），不可续跑；产出仍是一张运行表，卡片经响应 `description` 交还。若错（契约要求复用既有终止原因）→ 改映射与门面，不新增原因。验证：`tests/l3/test_agent_report.py` 的穷尽映射用例 + 集成用例。
- **A3**：`sk_configure` 走 `ConfigDraftProtocol` 的**循环合成面**——目标 / 取值 / 理解条目由模型调用给出，草稿卡即交还确认；派发前的「已确认意图卡」前置**不适用于**循环路径。工作流分支（`structure` 非空）没有聊天卡片，草稿交 Studio 画布（05 §5 既有口径）。若错 → 需在循环里先补一张意图确认卡（多一轮交还）。验证：`tests/l3/test_agent_subprotocols.py` 的 GWT-1 用例。
- **A4**：`sk_memory_op` 本叶只**列 / 取待裁决卡**（`card_for` / `cards`），不新开「提案写入」路径；「记忆写入 → 出裁决卡」由既有 L2 提案面产生、本动作承接呈现，红线（`confirmed_by="user"`）不动。若人裁要求动作本身可触发提案 → 补 `ConflictQueue.propose` 适配（不改裁决写入）。验证：GWT-2 用例断言动作不写、卡可交还。
- **A5**：两条动作按**控制动作**处理（拦截执行，不产 `skill_run` / Trace 步；与计划 / 提问原语同型），运行表以 `card_ready` 终止原因与说明留痕，卡片本体在助手轮 payload。若错 → 需把动作改为 L1 真执行并补留痕字段。验证：`LoopOutcome.steps` 为空、`termination == "card_ready"` 的断言。
- **A6**：多张待裁决卡时，响应 `description` 只承载**首张**（与既有 `analyze` / `memory_op` 派发面「展示首卡」一致），卡数在动作结果里显式给出，不静默丢。若人裁要求并列全卡 → 需新增组合描述件。
