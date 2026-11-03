---
id: T-AGT-022.2
parent: T-AGT-022
title: 事件模型与 JSONL 追加日志
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P0
milestone: M11
depends_on: [T-AGT-022.1]
status: done
decisions: [D-154]
verify: "执行日志 [T-AGT-022.2]；新增 9 例；范围套件 1407 passed；commit abbd421"
---

# T-AGT-022.2 · 事件模型与 JSONL 追加日志

## 目标
按 D-154 B5 定义运行事件，带单调序号追加写 JSONL，供 SSE 重连与崩溃后回放。

## 验收标准（Given-When-Then）
- GWT-1：Given 一次运行，Then 事件按发生顺序追加，序号单调递增。
- GWT-2：Given 运行结束，When 读取 JSONL，Then 能完整回放该 run。
- GWT-3：Given 事件类型不在枚举内，Then 显式失败，不静默丢弃。

## 接口面

**输入**（本任务消费的上游接口）
- `st_agent.l3.runtime.run_agent_loop`——四段循环的实现（`src/st_agent/l3/runtime/loop.py`）；本任务给它增可选 `emit` 回调，在既有四段（出动作 / 过闸门 / 执行 / 回填）的**不变点**发事件。
- `T-AGT-022.1` 的 `agent_run_id` 与 `RunEventLog` 的建仓时机——开跑铸号之后、循环开始之前。
- `Store`（`execution_log` 分区，`put` / `get` / `list_files`）——事件的唯一落盘通道（不新开分区、不绕过清单与加密）。

**输出**（本任务交付的公共 API 与落盘位置）
- `st_agent.l3.runtime.RunEventType` / `RUN_EVENT_TYPES`——D-154 B5 的 11 类事件枚举（`run_started` / `model_turn` / `plan_updated` / `step_started` / `step_finished` / `tool_search` / `sub_agent` / `gate_pending` / `ask_user` / `text_delta` / `run_finished`）。
- `st_agent.l3.runtime.RunEvent`——单条事件：`seq`（≥1 单调递增）/ `event` / `agent_run_id` / `at` / `payload`。
- `st_agent.l3.runtime.RunEventLog`——**追加写**句柄：`RunEventLog(store, agent_run_id, *, now=None)`、`emit(event_type, payload)`（未知类型 → `AgentRuntimeValidationError`，不写行）、`events()`、`ref`。
- `st_agent.l3.runtime.event_log_ref_of(agent_run_id)`——`agent-run-events/<agent_run_id>.jsonl`。
- `st_agent.l3.runtime.read_run_events(store, agent_run_id, *, after_seq=0)`——按序回放（供 `T-AGT-023.1` 的 SSE 断线重连 / 崩溃后回放）；坏行 / 序号不单调 → `AgentRuntimeValidationError`。
- `run_agent_loop(..., emit=Callable[[str, Mapping[str, Any]], None] | None)`——循环侧发事件；`None` 即不产生事件（存量调用方零改动）。
- **落盘位置**：`execution_log` 分区下的 `agent-run-events/<agent_run_id>.jsonl`（每行一条 `RunEvent` JSON；与 `agent-run/<id>.json`、`skill-run/<id>.json` 同分区、不同前缀）。

## 可关闭的遗留
无（2026-11-01 逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区，无「归属＝本任务」或「解封条件＝本任务 done」的条目；检查 8 兜底）。

## 假设与前提
- **A1**（事件只承载客观字段：任务、动作、状态、计划、结果、用量；**不含模型中间推理与中间轮文本**）——若错的影响：违反 [05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md) 的「过程可见边界」红线，需回退事件载荷。验证方式：GWT-1 的事件载荷断言 + 一道把中间轮文本序列化后扫描不得出现的守卫。
- **A2**（JSONL 追加经 `Store.get` + `Store.put` 读改写；存储按文件加密，无原生 append）——若错的影响：单 run 事件量很大时追加成本成 O(n²)，需要另立分段日志（可参考 L0 审计的分段先例）。验证方式：以单 run 的典型事件量（十到数十条）测追加与回放；性能边界写进执行日志。
- **A3**（续跑中「确认步」在循环体之前执行，其 `step_*` 事件不在本事件流内；该步已有 `skill_run` 留痕）——若错的影响：SSE 回放续跑时缺首步，需要 `T-AGT-023.1` / `T-AGT-024.1` 补发或扩展事件源。验证方式：本叶只对**首次运行**断言首条 `run_started`、末条 `run_finished`；续跑的事件边界写进任务与执行日志的已知边界。
- **A4**（子 Agent 的内部事件不并入父流；父流只发一条 `sub_agent` 摘要）——若错的影响：子链的实时可见性缺失，需要把 emit 下沉到子循环并另开事件流。验证方式：GWT-1 只断言父流含 `sub_agent` 摘要；子链可见性归后续「子 Agent 运行面板」复核。
- **A5**（`text_delta` 在无流式协议的现状下，以「结束轮整段文本一条」呈现）——若错的影响：前端按增量拼接的逻辑需要等到真实流式协议落地才成立。验证方式：事件类型与载荷形态先钉住；切片流式归 `T-AGT-023.2` 前端接入时复核。
