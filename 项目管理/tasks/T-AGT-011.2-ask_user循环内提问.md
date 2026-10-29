---
id: T-AGT-011.2
parent: T-AGT-011
title: ask_user（循环内提问，轻形态）
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §3.2](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M8
depends_on: [T-AGT-009.1]
status: done
decisions: [D-137, D-144]
verify: 2026-10-29 `[T-AGT-011.2]` · 范围套件 3276 passed / 2 errors（`test_shell` 本机环境性）· `verify_docs --strict` PASS · 未触联网面
---

# T-AGT-011.2 · ask_user（循环内提问，轻形态）

## 目标
循环发现缺参数时，以结构化问项终止并交还，而不是猜。

## 验收标准（Given-When-Then）
- GWT-1：Given 模型调用 `ask_user`，When 闸门放行，Then 循环终止并返回结构化问项（一问一参、取值域明确）。
- GWT-2：Given 用户回答，Then 可续跑（由 [`T-AGT-011.3`] 承接）。
- GWT-3：Given 问项超预算或无法收敛，Then 显式给出方向候选，不强行猜。

## 接口面
- 输入（消费的前置接口）：
  - `T-AGT-009.1`：会话身份 `session_id` 进派发载荷（[`l3/dispatch/bus.py`](../../src/st_agent/l3/dispatch/bus.py)）——`ask_user` 的交还归属于某个会话，续跑据此回到原会话。
  - `T-AGT-004.2` / `T-AGT-004.3`：`run_agent_loop` 的终止语义与 `TerminationReason`、`build_agent_output` 的终止→产出映射（[`l3/runtime/loop.py`](../../src/st_agent/l3/runtime/loop.py) / [`report.py`](../../src/st_agent/l3/runtime/report.py)）。
  - `T-AGT-008.2/.3`：内置原语安装与工具目录投影（同 `.1` 的输入面）。
  - `T-AGT-012.1`：运行期授权清单与闸门（同 `.1`）。
  - [05 §3.2](../../docs/技术架构-v2/05-L3-对话主入口.md)：澄清问项口径（一问一参、取值域明确、预算 ≤3、可跳过）——**只借用规则，不复用** `ClarificationQuestion` 类型（那个属派发前澄清，本叶属循环内提问）。
- 输出（本任务交付的公共 API / 落盘位置）：
  - 内置原语 `sk_ask_user`：声明体 `src/st_agent/l1/builtin/ask_user.py`，经 `BUILTIN_PRIMITIVES` 登记；`input_schema` 的 `questions` / `directions` 走对象输入通道。
  - 新终止原因 `needs_user_input`；`AskQuestion` 模型；`LoopOutcome.questions` / `directions` / `ask_note`（`l3/runtime/loop.py`）。
  - `AgentRunRecord.questions` / `directions` / `ask_note`（`l3/runtime/report.py`）；`describe_agent_run` 的 `questions` / `directions` 槽（`l3/render/describe.py`）。
  - 契约：01 §7 风险清单增 `skill.sk_ask_user = autonomous-ok`；05 §10 增终止原因「待用户输入」与问项口径；05 §4 回执面注明交还携带结构化问项。

## 可关闭的遗留
无（逐册核过六层遗留册未闭区，无「归属＝本任务」或「解封条件＝本任务 `done`」的条目）。

## 假设与前提
- **A1**：`needs_user_input` 的产出取 `ok` + **结构化描述**（[01 §5](../../docs/技术架构-v2/01-平台共享契约.md) 六态无「待输入」态，`empty` 不携载荷；照 `needs_confirmation` 交还并不能承载结构化问项）。若错：需新增六态或改 01 §5，返工面在产出装配与渲染。验证：`tests/l3/test_agent_ask.py` 断言信封 `ok` 且描述 `questions` 非空、渲染语义为正常态。
- **A2**：问项上限沿用 [05 §3.2](../../docs/技术架构-v2/05-L3-对话主入口.md) 的澄清预算（≤3）；模型给出超过 3 条时保留前 3 条，**其余显式记入** `ask_note`（不静默丢）。若错：用户拿到超预算问项，或丢项不可见。验证：超预算用例断言 `ask_note` 点名被截断的参数。
- **A3**：`sk_ask_user` 出厂标 `autonomous-ok`（纯控制动作，副作用仅是交还）。若错：出厂档下提问被闸门拦下，与「循环内可提问」矛盾。验证：`tests/l3/test_agent_gate.py` 用例 + 契约 01 §7。
- **A4**：问项形态 `{parameter, prompt, choices?, default?}`，与 `ParameterSpec` 的字段语义对齐但**不复用其类型**（循环问项无面板控件语义）。若错：表现层表单控件需另接。验证：`AskQuestion` 与 `ParameterSpec` 的字段对位用例。
- **A5**：问项形态非法（缺 `parameter` / `prompt`，或零问项且无方向候选）时**终止并显式说明**，不猜、不静默丢弃。若错：用户收到一份无法作答的空交还。验证：坏形态用例断言终止原因与说明文本。

## 涉及契约
- [05 §3.2](../../docs/技术架构-v2/05-L3-对话主入口.md) —— 问项来源与预算（≤ 3 问、可跳过）
- [05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md) —— 终止原因新增「待用户输入」

## 参考
- D-090 ⑦（本期不做挂起恢复——本叶走轻形态）

## 备注
**轻形态**：终止 + 结构化问项 + 续跑，不做挂起 / 恢复（那要异步 + 状态持久化）。
