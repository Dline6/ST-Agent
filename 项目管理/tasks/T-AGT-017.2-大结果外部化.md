---
id: T-AGT-017.2
parent: T-AGT-017
title: 大结果外部化
story: ../../docs/PRD-v2-Agent/story-01-chat-as-os.md
arch: ../../docs/技术架构-v2/05-L3-对话主入口.md
arch_link: "[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md)"
priority: P2
milestone: M10
depends_on: [T-AGT-017.1]
status: done
decisions: [D-137, D-149]
verify: "执行日志 [T-AGT-017.2]；新增 12 例 + 全量 4910 passed；回读分片可拼接"
---

# T-AGT-017.2 · 大结果外部化

## 目标
把现在的「截断成指针」升级为「写盘 + 按需回读」。

## 验收标准（Given-When-Then）
- GWT-1：Given 超阈值结果，When 回填，Then 写盘并给指针，模型可按需回读。
- GWT-2：Given 未超阈值，Then 原样。
- GWT-3：Given 回读越界，Then 拦截。

## 接口面
- 输入（消费的前置接口）：
  - `T-AGT-017.1`（本批前一叶）——`WorkContext` 的惰性字段与循环改造；本叶不改变其行为。
  - `T-AGT-004.1`（done）——截断件 `TruncatedResult` 与 `RESULT_TRUNCATE_CHARS`（`src/st_agent/l3/runtime/context.py`）。
  - `T-AGT-004.2` / `T-AGT-008.2`（done）——`SkillRunner` 每次执行落 `execution_log/skill-run/<skill_run_id>.json`（`RUN_PREFIX`），记录含完整 `SkillRun.envelope`（`src/st_agent/l1/runner/models.py`）。
  - `T-AGT-008.2`（done）——内置原语注册面 `BUILTIN_PRIMITIVES` / `install_builtins()` 与注入上下文（`src/st_agent/l1/runtime.py` 的 `install_builtins(context=…)`）。
  - `T-AGT-012.1`（done）——风险分级清单 `DEFAULT_RISK_GRADING`（`src/st_agent/l6/authorization.py`）。
- 输出（本任务交付的公共 API / 落盘位置）：
  - 新内置原语 `sk_result_read`（`src/st_agent/l1/builtin/results.py` + `ResultAccess` 端口；`l1/runtime.py` 注入键 `"results"`）：按 `skill_run_id` + `offset` + `max_chars` 回读外置结果，越界 `validation_failed`。
  - `TruncatedResult` 增 `skill_run_id` / `next_offset` / `read_tool` 三字段；`_render_result` 渲染回读提示。
  - 契约：05 §10 的回读段、01 §5 的日志入口与回读口径、08 §5 的 `skill.sk_result_read → autonomous-ok`。
  - 不新增分区、不改备份范围；大结果仍以 `SkillRunner` 的记录为唯一落盘真相源。

## 可关闭的遗留
无（2026-10-30 逐册核过 [遗留问题](../遗留问题/README.md) 六册未闭区，无「归属＝本任务」或「解封条件＝本任务 `done`」的条目）。

## 假设与前提
- **A1**（「写盘」沿用既有 `execution_log/skill-run/<id>.json`，不复制字节、不新增分区）——前提：该记录已含完整 `envelope`，D-090 ⑧ 已定「不新建存储、指针指向真实记录」。若错的影响：若留存策略清理了记录，回读显式 `unavailable`（不伪造内容）。验证方式：回读用例读真记录；缺失记录用例断言 `unavailable`。
- **A2**（回读的是 `envelope.data` 的确定性 JSON 文本，offset 口径与截断预览同源）——前提：`_json`（`ensure_ascii=False, sort_keys=True`）在 L3 与 `sk_result_read` 两处一致。若错的影响：offset 对不上模型看到的预览。验证方式：把同一载荷分别截断与回读，断言分片拼接等于完整 JSON。
- **A3**（越界＝`offset < 0` 或 `offset > total_chars` → `validation_failed`；`offset == total` 返回空文本）——前提：与「分片续读」语义一致。验证方式：边界用例逐条断言。
- **A4**（`skill_run_id` 只接受契约形态 `run_<20 位小写十六进制>`，用 `SkillRunId` 校验，防路径穿越）——前提：模型给的 id 可能任意；直接拼路径可越出 `execution_log`。若错的影响：路径穿越 / 读到非记录文件。验证方式：非法 id 用例断言拒绝且不读盘。
- **A5**（`max_chars` 缺省 2000、上限 8000；`offset` / `max_chars` 非整数即 `validation_failed`）——前提：防止一次回读又撑爆上下文。验证方式：上限 / 非整数用例。
- **A6**（风险分级 `skill.sk_result_read → autonomous-ok`）——前提：只读本地既有记录、无副作用。验证方式：分级清单顺序用例。

## 涉及契约
- [01 §5](../../docs/技术架构-v2/01-平台共享契约.md) —— 日志入口与指针
- [05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md) —— 回填语义

## 参考
- `l3/runtime/context.py:64`（`RESULT_TRUNCATE_CHARS`）

## 备注
现状是「截断 + 指针，模型看不到内容只能再查一次」——本叶补上回读通路。
