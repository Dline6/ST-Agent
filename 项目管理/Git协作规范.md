---
title: Git 协作规范
type: process
priority: P1
consumed_by: [编码 Agent, 项目负责人]
---

# Git 协作规范

> 适用于本仓库全部提交与版本管理。与 [工程宪法.md](工程宪法.md)、[工作流.md](工作流.md) 同级——工程宪法说"怎么干"，本文件说"怎么入库"。

---

## 1 仓库布局

项目根即仓库根。Git 追踪范围分两大区：

```
/                           ← 仓库根
├── docs/                   ← 产品规格（PRD / 架构 / 库表 / API 手册）
├── 项目管理/               ← 执行与追踪层（宪法 / 工作流 / Git 规范 / 账本 / 日志 / tasks / tools）
├── src/                    ← 实现代码（待建立）
├── tests/                  ← 测试代码（待建立）
├── CLAUDE.md               ← Agent 冷启动入口
├── .gitignore
└── README.md               ← 项目总览（面向人类）
```

**原则**：只追踪「源文件 + 派生视图」。`docs/`、`项目管理/`、`src/`、`tests/` 全部纳入版本控制；本地运行产物、用户数据、缓存、密钥一律不入库（见 §2）。

---

## 2 .gitignore（数据主权红线）

本项目是「本地优先」产品——用户投资数据、密钥、凭据属于**绝对禁区**。以下模板覆盖本项目已知需要忽略的路径类别：

```gitignore
# ─── 本地用户数据（铁律：永不入库） ───
data/
*.db
*.sqlite
*.sqlite3
*.duckdb
.env
.env.*
!.env.example
credentials/
keys/
secrets/
*.pem
*.key
token_cache/

# ─── Python 运行时 ───
__pycache__/
*.py[cod]
*$py.class
*.egg-info/
dist/
build/
.venv/
venv/

# ─── IDE / OS 垃圾 ───
.vscode/
.idea/
*.swp
*.swo
.DS_Store
Thumbs.db
desktop.ini

# ─── 临时脚手架与测试产物 ───
*.tmp
tmp/
temp/
.pytest_cache/
htmlcov/
.coverage
coverage.xml

# ─── 日志输出（非项目管理三本日志） ───
logs/
*.log
```

**执行纪律**：

- 新增目录结构时若命中以上模式，**先检查 `.gitignore` 是否已覆盖**；若需要新增规则，以独立 commit `chore(repo): 补充 .gitignore 规则 <原因>` 提交。
- 如果不小心追踪了敏感文件：立即 `git rm --cached <file>`（从索引移除但保留本地），追加 `.gitignore`，commit；若涉及密钥/凭据泄漏，视为安全事件并走 [阻塞与未决.md](阻塞与未决.md) 登记。

---

## 3 分支模型（主干开发 + 短生命周期任务分支）

### 3.1 模型概览

```
main ──────────●──────────●──────────●──────── main 永远可构建
               \        / \        /
                \      /   \      /
                 feature-1     feature-2
```

- **main**（主分支）：唯一长期分支。HEAD 始终绿（`verify_docs.py --strict` + `pytest` 全通过），代表项目当前已交付状态。
- **任务分支**：从 main 切出、命名绑定任务 ID、完成即删。生命周期 ≤ 3 个工作日；若超过，先合 partial（保持 status ≠ done），分支继续。
- **无 develop / release / hotfix 常驻分支**。里程碑交付 = main 上打 tag（见 §6）。

### 3.2 分支命名

```
<type>/<T-ID>-<slug>
```

| 段 | 含义 | 示例 |
|---|---|---|
| type | `feat` / `fix` / `docs` / `chore` / `test` / `refactor` | 对应 commit 类型 |
| T-ID | 任务 ID，无任务号时用 `sc` 占位 | `T-L0-005` |
| slug | 1–4 词 kebab-case 英文摘要 | `baostock-cache` |

完整示例：`feat/T-L0-005-baostock-cache`、`docs/git-spec`、`fix/sc-broken-link-render`

### 3.3 分支操作约定

1. **创建**：从 `main` HEAD 创建，立即 push 到远端（即使 solo 也建 remote 做备份）。
2. **rebase 策略**：分支存活超 1 天时，每日开工先 `git rebase main`（保持线性历史）。
3. **合入**：通过 PR/MR 合入（solo 也走 PR，给自己留审查痕迹）→ **Squash Merge**（每个任务一条提交落到 main）。
   - **启用 auto-merge（2026-09-26 起，取代此前的「等 CI 绿后手动 squash」）**：PR 建好后即
     `gh pr merge <n> --auto --squash --delete-branch`，CI 一绿自动合入并删分支；CI 尚未跑完时
     它挂起等待。若启用时检查**已全绿**，GitHub 会**立即**合入——这是 auto-merge 的正常语义，不是绕过门禁。
   - 代价要认：**CI 绿即落地，没有事后补检查的机会**，故 §5.2 的「人工 / Agent 自查部分」必须在**建 PR 之前**过完。
   - **squash 提交体取自分支的单条 commit**，故 §4.6 的 `Task:` trailer 与署名行照常随分支提交落到 main ——
     「一任务一条干净 commit」这条既有要求因此更要紧（多 commit 分支会丢掉这条保证）。
4. **删除**：合入后删除远端分支 + 本地 `git branch -d`（用 `--delete-branch` 时 gh 已代劳，本地只需
   `git fetch --prune` 清远端跟踪）。
5. **禁止**：force-push 到 main；rebase 已推送到远端的 main 历史。

### 3.4 与任务工作流的映射

| 工作流步骤 | Git 动作 |
|---|---|
| ③ Task 建任务文件 | `git checkout -b <type>/<T-ID>-<slug> main` |
| ⑤ Implement | 分支上多次 commit（WIP 允许，但 squash 后 main 只留一条干净记录） |
| ⑥ Verify & Log | 跑 `python tools/verify_docs.py --strict` + `python tools/render_ledger.py render` → 账本与日志变化一起 commit |
| ⑦ Sync（若涉及） | 同分支续 commit 或新分支 |
| 合入 | PR → `gh pr merge <n> --auto --squash --delete-branch`（CI 绿即自动合入）→ `render_ledger.py archive`（若里程碑满员）→ 单独 chore commit |

---

## 4 提交信息规范（Conventional Commits 中英混排 + 任务号）

### 4.1 格式

```
<type>(<scope>): <subject>
                                        ← 空行
<body>（可选，解释 WHY / 关键取舍）       ← 每行 ≤ 100 字符
                                        ← 空行
Task: <T-ID>                            ← 必填（无任务号写 Task: sc）
```

### 4.2 type 枚举

| type | 用途 | 示例 |
|---|---|---|
| `feat` | 新功能/新模块/新契约实现 | `feat(L0): 实现存储分区落盘加密` |
| `fix` | Bug 修复 | `fix(L1): 修正 Skill 描述符缺少返回类型` |
| `docs` | 文档新增/修订（含本目录活文档） | `docs(项目管理): 新增 Git 协作规范` |
| `refactor` | 重构，不改变外部行为 | `refactor(L2): 抽取 Memory Graph 序列化逻辑` |
| `test` | 新增/修改测试 | `test(L0): 补密钥掩码边界用例` |
| `chore` | 构建/工具/依赖/仓库维护 | `chore(repo): 补充 .gitignore 规则` |
| `ci` | CI/CD 配置 | `ci: add verify_docs pre-commit` |
| `style` | 纯格式（不影响逻辑） | `style: ruff --fix 全仓` |

### 4.3 scope 约定

- 架构层任务 → 层号：`L0`–`L6`、`SC`（shared contract）、`ECO`
- 项目管理文档 → `项目管理`
- 仓库级 / 跨层 → `repo`
- 多 scope → 省略或取主 scope

### 4.4 subject 规则

- **中文**描述具体变更内容，≤ 72 字符（含 scope）。
- 动词开头（实现/修正/新增/补充/重构/归档…），不加句号。
- 英文技术术语保留原文（如 Skill、Contract、verify_docs），不强译。

### 4.5 body 规则

- 解释「为什么这么做」或「关键取舍」；琐碎 commit 可省略 body。
- 涉及接口/契约变更时，body 须指明影响范围与关联架构文件。

### 4.6 Task trailer

- **每条 commit 必须携带 `Task:` trailer**（Squash Merge 到 main 后，`git log --grep "Task: T-L0-005"` 即可回溯任务全链路）。
- 任务文件 `T-*.md` 的 `verify` 字段引用 commit hash（短 7 位），实现双向追溯。

### 4.7 示例

```
feat(L0): 实现 BaoStock 数据源缓存子系统

- 建立 baostock_client.py 封装拉取 + 增量同步
- 本地 SQLite 按 K线/财务/板块分域建表（参见 数据库设计 01-04）
- 复权视图用 SQL VIEW 推导，不落盘

Task: T-L0-005
```

```
docs(项目管理): 新增 Git 协作规范

覆盖仓库布局、.gitignore 数据主权红线、分支模型、
提交信息格式与任务号关联、verify_docs 卡点联动、标签发布。

Task: sc
```

---

## 5 卡点与守护（pre-commit + PR gate）

### 5.1 本地 pre-commit（推荐）

在仓库根 `.pre-commit-config.yaml` 配置 `verify_docs.py --strict`，作为 commit 前的最后一道自动检查：

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: verify-docs
        name: 文档完整性自检
        entry: python 项目管理/tools/verify_docs.py --strict
        language: system
        pass_filenames: false
```

> 若项目使用 Python 虚拟环境，entry 改为绝对路径或 source 后再调用。pre-commit 不可用时，以「收工 checklist」（§5.3）人工兜底。

### 5.2 PR 合入 gate（solo 也走 PR）

**机器强制部分**（[`.github/workflows/ci.yml`](../.github/workflows/ci.yml)，push 与 PR 均触发；建议在仓库设置里勾选分支保护的「Require status checks to pass」，这样 `main` 始终绿不再依赖自觉）：

1. `python 项目管理/tools/verify_docs.py --strict` 通过——0 断链 / 0 悬空依赖 / 0 环 / 账本同步 / 无过期措辞 / 假设·接口面·集成关卡完整。
2. `python -m pytest` 全绿（默认排除 `live` 标记；含里程碑集成关卡用例）。

**人工 / Agent 自查部分**（CI 判不了语义）：

3. `python tools/render_ledger.py status` 就绪集正确——PR 中涉及的任务文件 `status` 字段与实际进度一致。
4. Commit message 格式校验（可选：配 commitlint 或简单 grep 正则 `^(feat|fix|docs|refactor|test|chore|ci|style)(\(.+\))?: .+$`）。

**合入方式（2026-09-26 起）**：PR 建好后即 `gh pr merge <n> --auto --squash --delete-branch`，CI 绿后自动合入，**不必守着 CI**（此前惯例是等 CI 绿再手动 squash，已废止；见 [决策日志 D-011](决策日志.md)）。因 auto-merge 一旦满足条件即落地，上列「人工 / Agent 自查部分」3、4 两条必须在**建 PR 之前**过完。

> CI 只跑离线部分：真实网络 / 长跑用例标 `@pytest.mark.live` 放 `tests/live/`，本地按需 `pytest -m live` 单跑，不进 CI。baostock 是可选依赖（`pip install -e ".[market]"`），CI 不装——其缺失路径本身有测试覆盖。

### 5.3 收工 checklist（每次准备 commit / push 时过一遍）

- [ ] `.gitignore` 无误追踪（`git status` 里无 `.db` / `.env` / `__pycache__` / 密钥文件）
- [ ] `verify_docs.py --strict` 绿
- [ ] 若改了任务状态：跑过 `render_ledger.py render`，账本无 diff 未提交
- [ ] 执行日志有对应条目（done 任务含验证行）
- [ ] Commit message 包含 `Task:` trailer 且格式合法
- [ ] 分支基于最新 main（`git log --oneline main..HEAD` 无 behind）

---

## 6 标签与里程碑发布

### 6.1 里程碑 tag

任务账本的每个里程碑 `M<n>` 全部 `done` 并 archive 后，在 main 上打 **annotated tag**：

```bash
git tag -a "M<n>-<slug>" -m "Milestone: <中文描述>"
git push origin "M<n>-<slug>"
```

示例：`git tag -a "M0-foundation" -m "里程碑：契约+L0+L1 基础设施完成"`

### 6.2 版本 tag（面向交付）

当需要发布可安装版本时，遵循 **SemVer**（`v<major>.<minor>.<patch>`）：

| 位 | 递增条件 |
|---|---|
| major | 不兼容的接口/契约变更（如平台共享契约 breaking change） |
| minor | 新增功能向后兼容 |
| patch | Bug 修复 |

Tag 打在 main HEAD，annotated + GPG signed（本地 solo 可选）。

### 6.3 CHANGELOG

每个 milestone tag 合入时由 `render_ledger.py archive` 后补生成 `CHANGELOG.md` 条目（或手动从 `git log <prev-tag>..<new-tag> --format=...` 导出）。格式 Keep a Changelog。

---

## 7 特殊场景处理

### 7.1 文档与代码同步变更

PRD / 架构文档修订与代码实现**在同一 PR 内完成**（宪法第 9 条），commit type 用主导变更的 type：

- 先改契约再改代码 → `feat(SC): ...` 带 `docs(技术架构): 同步 01-平台共享契约` 在同一 PR 的另一条 commit 里（squash 后合为一条，body 里交代双改）。

### 7.2 大量文件归档

`render_ledger.py archive` 移动 `tasks/*.md` → `tasks/done/M<n>/` 时：

```
chore(项目管理): 归档里程碑 M<n> 已完成任务

- tasks/T-<...>*.md → tasks/done/M<n>/（12 files）
- 任务账本.md 已刷新（render 重新生成）

Task: sc
```

单独 chore commit，不与功能 commit 混。

### 7.3 临时调试脚本

调试期间产生的临时 `.py` / `.sql` 放入 `tmp/`（已在 `.gitignore`），**绝不 commit**。收尾用 `host_safe_delete` 清理。

### 7.4 Force-push 与历史重写

- **禁止 force-push 到 main**。
- 个人任务分支在 PR 创建前可随意 `git push --force-with-lease`。
- 若 main 上发现误提交敏感文件（密钥/用户数据），视为安全事件：立即通知 → 用 `git filter-branch` 或 BFG Repo-Cleaner 清除 → force-push（此时例外）→ 轮换所有泄漏凭据。

---

## 8 Git 配置建议（一次性）

```bash
# 仓库级设置（新 clone 后执行）
git config user.name  "<你的名字>"
git config user.email "<你的邮箱>"
git config core.editor vim
git config pull.rebase true          # 分支 pull 默认 rebase，避免无意义 merge commit
git config rebase.autostash true     # rebase 时自动暂存未提交改动
git config log.date iso8601          # git log 显示带时区的 ISO 时间
```

---

## 9 速查卡片

| 我想… | 怎么做 |
|---|---|
| 开一个新任务 | `git checkout -b feat/T-L0-005-baostock-cache main` |
| 提交本地变更 | `git add -A && git commit` → 按 §4 格式写 message |
| 同步 main 最新 | `git fetch origin && git rebase origin/main` |
| 推分支并建 PR | `git push -u origin <branch>` → 在远端创建 PR → `gh pr merge <n> --auto --squash --delete-branch` |
| 确认账本同步 | `python tools/render_ledger.py render` → `git diff --stat` 看账本有无变化 |
| 看 CI 结果 | GitHub 仓库 Actions 页 / PR 页的 checks（PR gate 机器部分，见 §5.2） |
| 跑真实网络用例 | `python -m pytest -m live`（默认被 `addopts` 排除，不进 CI） |
| 打里程碑 tag | 归档完成 → `git tag -a "M0-foundation" -m "..."` → push |
| 回溯某任务所有 commit | `git log --all --grep "Task: T-L0-005"` |
| 找回误删文件 | `git log --diff-filter=D --summary` 找到删除 commit → `git checkout <commit>~1 -- <path>` |
