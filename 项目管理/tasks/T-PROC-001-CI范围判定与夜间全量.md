---
id: T-PROC-001
parent: ""
title: CI 范围判定收口与夜间全量
story: ../工作流.md
arch: ../Git协作规范.md
arch_link: ""
priority: P1
milestone: ""
depends_on: []
status: done
decisions: [D-140]
verify: 执行日志 [T-PROC-001]——范围判定 10 例 + PROC 层识别 + verify_docs --strict + 322 passed
---

# T-PROC-001 · CI 范围判定收口与夜间全量

## 目标

把 PR 门禁的变更范围判定收进可测模块，修掉两处误判（`packaging/README.md` 触发全量、
`项目管理/tools/**` 跑 817 条），并把「合入 main 后全量」换成「每日夜间全量」——PR 门禁强度不变。

## 验收标准（Given-When-Then）

- GWT-1：Given 文档-only 变更（含 `packaging/README.md`），When PR 跑 CI，Then `verify_docs` 通过且 pytest 不收集。
- GWT-2：Given 仅 `项目管理/tools/**` / `tests/test_tools.py` 变更，When PR 跑 CI，Then 只跑 `tests/test_layering.py tests/test_tools.py`，不跑跨层基表。
- GWT-3：Given 代码变更落在某层，When PR 跑 CI，Then 跑跨层基表 + 该层套件；契约 / pyproject / 打包脚本变更即全量。
- GWT-4：Given 合入 main，When CI 触发，Then 只跑 `verify_docs`；全量只在每日 03:17 的 schedule 与手动 dispatch 发生，失败自动登记 issue。
- GWT-5：Given 同一 PR 连续推送，When 新的一轮开始，Then 旧的一轮被取消（`concurrency.cancel-in-progress`）。

## 接口面

- 输入（消费的前置接口）：
  - `.github/workflows/ci.yml` 的 `check` job：`git diff --name-only` 的变更路径 + `github.event_name`。
  - 范围口径：[工作流 §测试分层](../工作流.md) 的「受影响范围映射」。
- 输出（本任务交付的公共 API / 落盘位置）：
  - `项目管理/tools/ci_scope.py`：`decide(event, paths) -> (run, targets)` 与 CLI（向 stdout 写 `run=` / `targets=` 两行，供 `$GITHUB_OUTPUT`）。
  - `.github/workflows/ci.yml`：`schedule` 触发（`17 19 * * *`，UTC 19:17 = 03:17 CST）、`concurrency`、`permissions: issues: write` 与夜间失败登记 issue 的步骤。
  - `项目管理/tools/render_ledger.py`：`ID_RE` 与 `LAYERS` 增 `PROC` 层（工程流程 / CI），置于 L6 之后、INT 之前。
  - `tests/test_tools.py`：范围判定 10 例 + PROC 层识别 1 例。

## 可关闭的遗留

无（逐册核过 L0 / L1 / L2 / L3 / L5 / L6 的未闭区，无「归属＝本任务」或「解封条件＝本任务 `done`」的条目）。

## 假设与前提

- **A1**：合入 main 后不再跑 pytest，跨层回归最晚在次日 nightly 或里程碑收口发现。若错的影响：main 上可能出现最长约 24 小时的未知回归；验证方式：nightly 失败开 issue，里程碑收口仍强制全量。
- **A2**：`packaging/**` 只认 `*.py` / `*.spec` 为构建脚本；其余文件（README 等）视为文档。若错的影响：新增的构建辅助文件不会被 CI 覆盖；验证方式：`ci_scope` 用例把两类分别钉住。
- **A3**：`PROC` 任务 `milestone` 置空（同 D-136 的悬空口径），不进就绪集、不参与里程碑关卡覆盖。若错的影响：账本多出一个活跃分区且该任务不会被 `archive` 移动；验证方式：`test_proc_layer_sits_before_int` + `render` 后账本无悬空依赖。

## 涉及契约

- [工作流 §测试分层](../工作流.md) —— 范围口径与 nightly 分工
- [Git协作规范 §5.2](../Git协作规范.md) —— PR 合入 gate
- `.github/workflows/ci.yml` —— 触发与范围判定
- `项目管理/tools/ci_scope.py` —— 判定实现
- `tests/test_tools.py` —— 规则回归

## 参考

- [D-140](../决策日志.md)（本批决策）
- [D-045](../决策日志.md)（`tests/integration` 进跨层基表的先例）

## 备注

2026-10-28 负责人确认「范围判定收口 + nightly」方案后开工。
