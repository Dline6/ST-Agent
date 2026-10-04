---
id: T-L4-001
title: 视角模型 Lens + 常设阵容（用户可增删）
story: ../../docs/PRD-v2-Agent/story-04-multi-lens.md
arch_link: "[06 §1](../../../../docs/技术架构-v2/06-L4-多视角推理.md)"
priority: P0
milestone: M2
depends_on: [T-SC-001, T-L1-001]
status: done
decisions: []
verify: 按范围 `pytest tests/contracts tests/test_layering.py tests/test_tools.py tests/integration tests/l4` → 416 passed（含 tests/l4 26 条新用例）；无 LLM 出网面（纯本地定义层），live 子集不适用；`verify_docs.py --strict` 通过；入库 `4acd50b` + [PR #72](https://github.com/Dline6/ST-Agent/pull/72)（`173b24f` 干净 squash）· 见执行日志 [T-L4-001] 2026-09-30
---

# T-L4-001 · 视角模型 Lens + 常设阵容（用户可增删）

## 目标

L4 多视角推理的**视角模型 Lens**（06 §1）与**官方预置 7 视角常设阵容**（用户可增删自定义、内置只能停用不可删）。本任务只交付 06 §1 的**定义层**：Lens 数据结构 + 阵容的读写门面 + 7 内置视角种子 + 命名中性化门 + 本地落盘。执行编排（§2）、交叉对照（§3）、Mini Debate（§4）、可视化（§5）、边界情形（§6）属 `T-L4-002/003/004`，**不在本任务**。

## 验收标准（Given-When-Then，从 Story 4 的视角阵容面 + 06 §1 抄）

- **GWT-1** Given 官方阵容初始化，When 读取，Then 恰有 7 个 `kind=builtin` 视角（机会/风险/基本面/情绪/流动性/宏观/合规），每条 `name` 过 01 §6 命名校验、`skill_bundle` 引用真实 `skill_id`、`judging_criteria`/`confidence_policy`/`lens_id` 齐。
- **GWT-2** Given 用户创建自定义视角，When `name` 命中禁用词表（人名「老张」/性格标签「激进派」）或 `description` 含第一人称（「我认为…」），Then 拒绝保存（抛校验错），不落盘、不进阵容。
- **GWT-3** Given 用户以中性命名与描述创建自定义视角，When 保存，Then `kind=custom` 落 `config` 分区、进阵容、可列出，`lens_id` 为 §1 合法 `lens` 前缀 ID。
- **GWT-4** Given 某自定义视角在阵容中，When 删除，Then 从阵容与盘上移除；Given 某**内置**视角，When 尝试删除，Then 拒绝（内置不可删，见 A2），只能停用（`enabled=False`，读面仍可见但标注停用）。
- **GWT-5** Given 新用户无任何自定义视角，When 查看阵容，Then 返回 7 官方视角（内置默认全部启用）；阵容门面提供「无自定义视角」判定供上层渲染空态入口（06 §6「创建你自己的视角」入口本身属 UI/L3，不在本任务）。

## 接口面

**输入（逐条点名上游任务 id + 具体 API/落盘位置）**：
- [`T-SC-001`]：[`contracts/identifiers.py`](../../../../src/st_agent/contracts/identifiers.py) 的 `LensId`（§1，前缀 `lens`，`LensId.generate()` 产本机 ID）· [`contracts/neutrality.py`](../../../../src/st_agent/contracts/neutrality.py) 的 `NeutralityGuard.check_name(name, *, description=None)` + `default_rulepack()`（命名/描述中性化唯一真相源，无关闭开关）
- [`T-SC-001`]：[`contracts/capability_types.py`](../../../../src/st_agent/contracts/capability_types.py) 的 `SkillDescriptor`（§2，`skill_bundle` 引用的目标形态）与 `LensOpinion`/`Stance`/`Confidence`（§3，本任务只产 Lens *定义*，不产 opinion；`confidence_policy` 的 high/medium/low 与 §3 `Confidence` 对齐）
- [`T-L1-001`]：[`l1/skills/registry.py`](../../../../src/st_agent/l1/skills/registry.py) 的 `SkillRegistry.get(skill_id)`（解析 `skill_bundle` 每项是否真注册；未注册 → `SkillNotFoundError`）· `list_all()`（可枚举可用 skill）
- [`T-L0-001`]：[`l0/storage/store.py`](../../../../src/st_agent/l0/storage/store.py) 的 `Store.put/get/delete/list_files("config", name)`（阵容落盘走 `config` 分区，目录前缀 `lens-roster/`，仿 `skill-registry/`）

**输出（本任务交付的公共 API 与落盘位置）**：
- 新建包 [`src/st_agent/l4/`](../../../../src/st_agent/l4)（L4 层首个模块）：
  - `l4/lens.py`——`Lens`（frozen pydantic，06 §1 六字段）+ `LensKind`（`builtin`/`custom`）+ `JudgingCriteria`（预留自然语言 + 规则表达式二入口）+ `ConfidencePolicy`（数据充分度 → high/medium/low）；构造期不变量：`skill_bundle` 每项为合法 `skill_id` 形态、`name` 非空
  - `l4/roster.py`——`LensRoster` 门面：`seed_builtin()`（幂等播种 7 内置）/ `list_enabled()` / `list_all()` / `add_custom(...)`（过中性化 + 校验 skill_bundle）/ `remove(lens_id)`（内置拒绝，见 A2）/ `set_enabled(lens_id, bool)` / `has_custom()`；落盘 `config` 分区 `lens-roster/<lens_id>.json`
  - `l4/builtin.py`——`BUILTIN_LENSES`：7 内置视角种子常量（`skill_bundle` 取 `l1/skills/pack.py` 现成 base，见 A1）
  - `l4/errors.py`——`L4Error`/`LensValidationError`/`LensNotFoundError`/`BuiltinLensError`（与 L1/L2 同构分级）

## 可关闭的遗留

无（已逐册读 L0/L1/L2/L3 四册未闭区，无任何条目的「归属」或「解封条件」指向 `T-L4-001`；`阻塞与未决.md` 唯一 open 条目 `Q-014` 影响任务为 `T-L2-003.1, T-L4-002`，不含本任务）。

## 假设与前提

- **A1**：7 内置视角的 `skill_bundle` 复用 `OFFICIAL_PACK` 现有 skill base 即可（机会←`sk_opportunity_mine`+`sk_unhat_eligibility_check`；风险←`sk_risk_alert`+`sk_delisting_risk_scan`；基本面←`sk_fundamental_screening`；情绪←`sk_sentiment_flow_analysis`+`sk_sector_heatmap`；流动性←`sk_data_aggregate`；宏观←`sk_data_aggregate`+`sk_sector_heatmap`；合规←`sk_delisting_risk_scan`+`sk_risk_alert`），**无需为内置视角新建 Skill**。**若错**：某些视角 `skill_bundle` 只能留空占位，`T-L4-002` 编排开工时要回填真实 skill。**验证**：`skill_bundle` 每项在 `SkillRegistry` 播种官方 Pack 后能 `get()` 成功；`pack.py` 12 个 base 逐一比对。
- **A2（人 2026-09-30 拍定）**：**内置视角只能停用（`enabled=False`）不可删除；自定义视角可增可删**。`Lens` 含 `enabled: bool` 字段。**若错**：若日后要求可删内置，需改 `remove` 语义 + 播种逻辑。**验证**：GWT-4 两用例（删内置被拒 / 停用生效 / 删自定义成功）。
- **A3**：本任务单叶不拆分（GWT 恰 5 条、只落 06 §1 一个小节、新增单层 `l4/`、无跨层装配）。**若错**（如实测发现 `judging_criteria` 二入口需独立契约登记）：拆 `.1`。**验证**：实现后若 `verify_docs.py --strict` 与测试无跨层缺口即成立。
- **A4**：`Lens` 定义结构**不需新登记进 01 契约**——01 §1 已登记 `lens_id`、§3 已登记 `LensOpinion`；06 §1 即 Lens *定义字段* 的架构真相源（01 不重复登记各层内部模型，与 SkillDescriptor 仅登记结构、Skill 侧扩展字段归 03 同例）。故**不触发铁律 8**（无 01/架构文档变更）。**若错**（若负责人认为 Lens 定义应升为跨层共享对象进 01）：先改 01 再改码，返工登记。**验证**：核对 01 §2/§3 是否把 Lens 定义列入——目前无；下游 `T-L4-002` 若跨层消费 `Lens` 再评估。

## 涉及契约（链接到具体小节）

- [06-L4 §1 视角模型 Lens](../../../../docs/技术架构-v2/06-L4-多视角推理.md)（字段表 + 常设阵容表）
- [01-平台共享契约 §1](../../../../docs/技术架构-v2/01-平台共享契约.md)（`lens_id`）· [§2](../../../../docs/技术架构-v2/01-平台共享契约.md)（`SkillDescriptor`）· [§3](../../../../docs/技术架构-v2/01-平台共享契约.md)（`LensOpinion`）· [§6](../../../../docs/技术架构-v2/01-平台共享契约.md)（中性化）
- Story：[multi-lens](../../../../docs/PRD-v2-Agent/story-04-multi-lens.md)

## 备注
实现细节不写此处，留给代码 / commit / 执行日志。
