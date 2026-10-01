---
title: L1 · 能力底座：Skills Runtime 与 MCP Hub
layer: L1
priority: P0
depends_on: [01-平台共享契约, 02-L0-本地优先基座]
consumed_by: [04-L2-记忆图谱, 05-L3-对话主入口, 06-L4-多视角推理, 07-L5-主动触达, 09-生态与分享]
stories: [PRD Story 2 — Skills Runtime, Story 6 — Skill Studio, Story 8 — MCP Hub]
---

# 03 · L1 能力底座：Skills Runtime 与 MCP Hub

承载 [Story 2](../PRD-v2-Agent/story-02-skills-runtime.md)、[Story 6](../PRD-v2-Agent/story-06-skill-studio.md)、[Story 8](../PRD-v2-Agent/story-08-mcp-hub.md)。L1 由四个子系统组成：**Skill Runtime**（注册/调度/执行）、**官方 Skill Pack**、**Skill Studio**（可视化编排）、**MCP Hub**（外部能力挂载）。共享类型（SkillDescriptor、ResultEnvelope、权限模型）见 [01-平台共享契约](01-平台共享契约.md)。

## 1. Skill Runtime

### 1.1 职责

注册与发现、调度执行、依赖解析、参数校验、版本管理、执行留痕、输出复用。Skill 本身**无状态**——用户状态只存于 L2 记忆与配置注册表（这是可分享性的前提，见 [09-生态](09-生态与分享.md)）。

### 1.2 执行流水线

一次 Skill 调用 `skill_run_id` 的标准流水线：

1. **解析**：意图来源（L3 对话 / Studio / Deliberation / 工作流 / 定时调度）→ 匹配 SkillDescriptor
2. **参数与输入确认**：`parameters` 的标量可配参数超出合理范围 → `validation_failed`（拦截 + 说明理由，不允许保存）；经对话触发时展示参数确认卡（用户可调或用默认值）。**对象类输入**（用户组合 / 风格偏好 / 标的池这类 `ParameterSpec` 承载不了的对象，01 §2 的三条通道之②）由**调用方**经 `inputs` 供给，按描述体 `input_schema` 核验（判定件同上 `contracts.schema_check`）——不合契约 → `validation_failed` + 违规点说明，**执行器不被调用**；`inputs` 只作用于**被请求的那个 Skill**，**不沿依赖链下发**。调用方从何取得该对象（如读 L2 记忆图谱）不在本层职责内——L1 不向上取
3. **依赖解析**：递归解析 `dependencies`；**DAG 约束**——检测到循环依赖即拒绝（保存工作流与运行时双重检测）；上游失败 → 下游 `dependency_failed`，禁止用错误数据继续
4. **权限检查**：按 01 §10 权限模型核对本次执行所需权限
5. **执行与留痕**：执行器产出先经描述体 `output_schema` 核验（01 §2 方言澄清，判定件 `contracts.schema_check`）——不合契约 → `failed` + 违规点说明，**不进入下一步**；合契约则包装 ResultEnvelope；记录 SkillRun（输入快照、输出、耗时、错误、依赖链）并挂到 `trace_id`
6. **输出登记**：成功输出登记为可引用结果（见 §1.3）；**未过步骤 5 核验的载荷一律不登记**（禁止把不合契约的载荷按 `ok` 传播给复用方）

### 1.3 输出复用（数据协同）

- 每个 SkillRun 输出可被后续 Skill / 视角 / 报告通过 `skill_run_id` 引用，避免重复计算
- 复用时必须校验 `as_of`（01 §8）：引用过期快照时上层自行决定是否重算；运行时提供「freshness 查询」接口——其数据来源为 L0 数据缓存的同步状态表（见 [数据库设计 §05](../数据库设计-BaoStock数据层/05-同步策略与新鲜度契约.md)，逐表水位与陈旧判据），不另建第二套口径

### 1.4 版本管理

- 官方 Pack 更新遵循 01 §9：展示变更日志 + 影响范围；用户逐 Skill 选择更新 / 跳过 / 锁定（`version_policy`）
- 契约变化（主版本变更）时，引用方（工作流、视角）自动标「待检查」，用户手动确认后升级——禁止自动破坏下游

### 1.5 执行沙箱

- Skill 执行在受限环境内进行：文件访问限于声明的 `local_read` 范围、网络限于声明的 `net_access` 模式、命令执行需 `exec_command` 审批
- 运行时行为越界 → 拦截 + `BehaviorViolation` 事件（01 §11）+ 警示「这个 Skill 行为异常」+ 提供禁用选项
- 沙箱是 [09-生态](09-生态与分享.md) 导入第三方 Skill 的恶意行为拦截基础
- **权限声明只覆盖 Skill 自身发起的这三类出口**（[01 §10](01-平台共享契约.md) 的语义范围）：经**注入的取数面**消费数据（如官方 Pack 的 `market_query`、L0 的文档取数面）不经这些出口，故**不声明权限**——官方 Pack 的 12 个 Skill 一律 `permissions=()`（同批已有用例锁死：声明为空时既不触发「未批准」硬门，也不削弱沙箱——空声明的会话语义是**更严**，越界一律拒）。
- **Skill 侧的「声明 → 逐项批准」落点**：批准态落 `config` 分区 `skill-permissions/<base>.json`，**键为 base**（能力身份，批准随能力跨版本存活；声明变化经 `declare` 对账）。与 MCP Server 侧的 `mcp-permission/<server_id>.json` **共用同一份簿记实现**（键形态与前缀不同，语义不各造一套）。无人值守的执行（定时调度、L5 投递）经注入的 `PermissionSource` 取「已批准」；**空账本即空集**，交沙箱 fail-closed——组合根只**读**这一事实、从不造它。

## 2. 官方 Skill Pack

首次安装即内置，无需下载。两 Bundle（名称沿用 PRD，Skill 清单为产品承诺的最小集，可增不可缺）：

> **官方 Pack 是类型化官方资源容器**（[01 §13](01-平台共享契约.md)）：Skill 描述体是其中的 `skill` kind；同一容器还承载 [01 §6](01-平台共享契约.md) 的中性化规则库（`rulepack`）、[04 §7](04-L2-记忆图谱.md) 的 Onboarding 问题清单（`onboarding_questions`）、[05 §8](05-L3-对话主入口.md) 的快捷指令种子（`command`）。容器与其统一装载入口归**本层**（`st_agent.l1.pack`），按 `kind` 分发到各消费方的 loader；L2 / L3 向容器注册 loader 属**向下依赖**（[铁律 7](../../项目管理/工程宪法.md)）。各消费方保留自有的内置缺省——Pack 缺席时行为与其内置缺省一致。

> **权限声明**：本 Pack 的 12 个 Skill 一律 `permissions=()`——它们只经**注入的取数面**消费数据，自身不发文件 / 网络 / 命令行为，故按 [01 §10](01-平台共享契约.md) 的语义范围**无权限可声明**（见 §1.5）。因此官方 Pack **无需逐项批准**即可执行；「声明 → 逐项批准」的落点在**导入物**（[09 §3](09-生态与分享.md)）与 MCP Server（§5.1）上。

### 2.1 公共认知 Bundle

| Skill | 职责 | 关键输出 |
| --- | --- | --- |
| `st-list-sync` | 同步交易所 ST/*ST 名单，识别新增/移除 | 名单变更 diff |
| `delisting-risk-scan` | 识别退市高危信号 | 可解释的触发原因（证据引用） |
| `unhat-eligibility-check` | 动态评估摘帽条件满足情况 | 条件清单 + 满足度 |
| `sector-heatmap` | 行业属性标记 + 板块热力图 + 风险评级 | 热力图数据 + 评级 |
| `sentiment-flow-analysis` | 情绪与资金流向分析（中性化，承接 v1 A3） | 高换手/高热度预警 |
| `fundamental-screening` | 基本面筛选与安全边际评估（承接 v1 A4） | 机构优选池 |

### 2.2 主动服务 Bundle

| Skill | 职责 |
| --- | --- |
| `stock-watch` | 多标的盯盘：关键词/财报日/异动/换手率触发条件可配。**关键词**条件读 L0 信息面的**公告域**（回看窗口 30 日，由声明参数 `keywords` 提供词表）；公告域数据面未就绪或未提供关键词时，该条件**显式列入 `unevaluated_conditions` 并写明原因**，不假装通过（见 [02 §5](02-L0-本地优先基座.md) 与 [D-029](../../项目管理/决策日志.md)） |
| `data-aggregate` | 数据聚合：表格卡/趋势图/简报输出 |
| `risk-alert` | 风险预警：退市倒计时/流动性枯竭等维度可扩展 |
| `opportunity-mine` | 机会挖掘：风格 + 板块偏好过滤，复用公共认知输出 |
| `portfolio-stress-test` | 组合压力测试：规则化情景推演 |
| `strategy-design` | 策略设计与回测：选股→信号→策略→回测四步链路 |

`strategy-design` 的特殊契约：**信号校验器**必须实现「未来函数」检测——用户设计的信号若依赖未来数据，校验即报错并指出问题；回测口径由用户设定，输出用户口径的回测报告。

**对象类输入的取值面（2026-09-28 定案，[D-056](../../项目管理/决策日志.md)）**：下表三处输入是**对象**，`ParameterSpec` 装不下（01 §2 的三条通道之②），经**运行期 `inputs`** 由**调用方注入**——声明已在各描述体的 `input_schema`：

| Skill | `inputs` 键 | 形态 | 有值时的口径 | 无值时的降级（**可解释默认口径**） |
| --- | --- | --- | --- | --- |
| `portfolio-stress-test` | `portfolio` | `object`（`{codes: [stock_id…], source?: str}`） | 只按组合内标的推演，`portfolio_source` 记该对象的 `source` | 本地缓存最新交易日在市标的**等权**，`portfolio_source = "local-cache-equal-weight"` |
| `opportunity-mine` | `preference` | `object`（`{sectors?: [str…], themes?: [str…], risk?: str, source?: str}`） | 候选按 `sectors`（查 `stock_industry`）/ `themes`（匹配 `reasons`）过滤，`risk` 仅随载荷回显；`preference` 记该对象 | `preference = "none"`（原优选池按分数排序） |
| `strategy-design` | `universe` | `string`（逗号分隔的 `stock_id` 表，同 `stock-watch` 的 `keywords` 串约定） | 回测窗口**只纳入**该池标的 | 全市场等权（同今）；报告另出 `universe_source` |

**口径**：上表「有值」一栏的数据由**调用方**读 L2 记忆图谱后汇成（记忆侧取值面：`attention.holdings` / `.watchlist` / `.sector_preferences` / `.theme_interests`、`identity.risk_preference`）——L1 **不** import L2、不向上取（[铁律 7](../../项目管理/工程宪法.md)）。取不到相关记忆即**回落默认口径**并**显式标注来源**，**不冒充用户输入**（[D-028](../../项目管理/决策日志.md)）。`strategy-design` 的**信号定义**不走此通道（记忆六类节点无信号定义一类）：其唯一来源仍是上游载荷 `signal_spec`，`report.signal_check` 记其**来源**（提供方 `skill_id` / 未提供）。

## 3. 工作流模型（Skill Studio 的数据基础）

### 3.1 WorkflowDAG schema

| 字段 | 说明 |
| --- | --- |
| `flow_id` / `name` / `description` | 标识（`flow_id` 形态为 `wf_<注册名>_v<主>.<次>`，见 [01 §1](01-平台共享契约.md)；`name` 过中性化校验） |
| `nodes[]` | 每节点：引用 skill_id、参数绑定（含常量与上游输出引用） |
| `edges[]` | 数据流连线，携带类型契约（连线时校验输入输出 schema 匹配，不匹配提示「这里需要一个 XX 类型的输入」） |
| `groups[]` | 子流程分组（可整体作为复合 Skill 导出） |
| `schedule` | 触发方式：定时 / 事件 / 手动 |
| `version` | 版本历史（多版本共存、diff、一键回滚） |

### 3.2 复合 Skill

用户可将工作流保存为复合 Skill（「命名的工作流」）：对外暴露统一输入输出契约 + 参数，进入 Skill 库；导出为 `.stflow`/`.stskill`（见 [09-生态](09-生态与分享.md)）。复合 Skill 依赖的原 Skill 缺失时，导入方走依赖提示流程。

**落地口径**：

| 面 | 口径 |
| --- | --- |
| 标识 | `skill_id` 由工作流 base 派生——`wf_<名>_v<主>.<次>` → `sk_<名>_v<主>.<次>`（版本随 `version`）；重名即拒（更新走版本发布，不静默覆盖） |
| 输入输出契约 | 默认由**未连线端口**推导（`type` 恒为 `object`）；`properties` 取**节点限定点号键** `<node_id>.<prop>`——可无损回指节点与端口，跨节点同名不冲突 |
| 已连线判据 | 与 §3.1 的粗粒度节点到节点边模型对称：入端口「已连」= 该节点有入边 **或** 有同名绑定（`literal` / `ref`）；出端口「已连」= 该节点有出边 **或** 被其他节点以 `ref` 引用 |
| 显式覆盖 | 可显式给出 `input_schema` / `output_schema`，以显式为准；其 `properties` **键集必须与推导集相等**、同名键的 `type` 必须相等，其余关键字（`required` / 描述等）随显式——不一致即拒，不静默取其一 |
| 参数暴露 | 节点参数是内部绑定细节，**不**自动上浮；由 `exposed_params` 显式声明（`<node_id>.<参数名>`）才进入 `SkillDescriptor.parameters`，与其余 Skill 同走双通道改参 |
| 能力字段 | `offline_level` 取所引用 Skill 的最差（全 `full`→`full`；任一 `none`→`none`；否则 `degraded`）、`permissions` 取并集——复合体只声明其部件所能支持的面 |

依赖缺失时**不落半成品**：物化（注册进 Skill 库）以依赖齐全为前提并显式失败；用户在依赖未安装期间不丢工作，因为工作流定义本身已由 §3.1 的持久化承载，只是复合 Skill 的物化等待依赖就绪。

## 4. Skill Studio 子系统

承载 [Story 6](../PRD-v2-Agent/story-06-skill-studio.md)：

- **双通道**：可视化画布编辑 + 对话式生成。对话生成接口接收 L3 的「工作流草稿对象」（见 [05-L3 §5](05-L3-对话主入口.md)），落到画布后可视化微调
- **调试协议**：任何工作流可试跑——单步执行、输入注入、逐步输出快照、暂停/继续/中止；每次试跑留存历史可对比（落地口径见下方「调试协议落地口径」）
- **校验**：保存时执行成环检测（DAG 约束）+ 契约匹配校验 + 中性化命名校验（01 §6）
- **模板库**：官方预置工作流模板（每日 ST 简报 / 退市风险扫描 / 策略回测流水线等），可 fork 后修改
- **空状态**：无自建工作流时展示模板库 + 「用一句话描述你想要的工作流」入口

**编辑面落地口径**：

| 面 | 口径 |
| --- | --- |
| 编辑会话 | `CanvasEditor` = 内存编辑会话（草稿态**不落盘**）；每次写操作返回 `EditResult`（是否生效 + 新 DAG 视图 + 校验结论 + 人可读提示）。落盘只发生在「接受」 |
| 校验复用 | 编辑期与保存期是**同一判定件的两次调用**，不另造第二套：连线契约走 [01 §2](01-平台共享契约.md) 的 `check_link`、全图走 `validate_dag`、成环走 `validate.find_cycle`（与 `dependency_graph` / `topological_order` 并列的公共判定件） |
| 连线拦截范围 | `connect` 只拦**本连线造成**的问题——① 该边落在环上（拒，携环路径）② `check_link` 不匹配（拒，携「这里需要一个 XX 类型的输入」式说明）。草稿**既有**的语义问题不阻断后续编辑、只在 `EditResult` 中回显；被拒时画布状态不变 |
| 删节点 | 删除一个节点即**连带移除它的全部引用**——入射 / 出射连线、`ref` 参数绑定、分组内的成员位。依据：§3.1 的依赖图 = 边 ∪ `ref` 绑定（两者取并），删后依赖图不留悬空引用 |
| 分组 | `group` / `ungroup` 只动 `groups[]`，不改节点与连线；分组不参与依赖图 |
| 草稿接收契约 | 工作流草稿 = [05-L3 §5](05-L3-对话主入口.md) 四字段 + 工作流专用 `workflow_draft` 子对象（`name` / `description` / `nodes` / `edges` / `groups` / `schedule`，可含 ASCII `flow_name`）——身份字段 `flow_id` / `version` **不在草稿中**，由接收方派生 |
| 非法草稿边界 | **形状**非法（缺 `name` / 非法 `node_id` / 缺必填字段 / 无法派生注册名 / 违规携带 `flow_id` 或 `version` / 含无法识别的字段，即构造期即拒）→ 显式拒、**画布不开**；**语义**不合规（成环 / 契约不匹配 / 命名未中性化 / Skill 未注册 / 参数未声明）→ **照开画布**，违规清单随首次 `EditResult` 带回 |
| 注册名派生 | `flow_id` 的 `<注册名>` 取草稿的 ASCII `flow_name`；缺省时由展示名 `name` slug 化（小写、非 `[a-z0-9]` 转 `_`、去首尾 `_`）——slug 为空（如纯中文名）即拒并提示补 `flow_name`。画布期用临时身份 `wf_<注册名>_v0.0` 表「未发布」 |
| 接受 | 把画布期身份换成首版 `wf_<注册名>_v1.0`，经 `WorkflowStore.save` 落 `config` 分区 `workflow/<flow_id>.json`；base 已存在 → **拒**（改既有工作流走 `WorkflowStore.publish_version`，不属本流程） |
| 变更留痕 | 「接受」产生一条 `ChangeRecord`（[01 §7](01-平台共享契约.md)）并落 `execution_log` 分区 `workflow-change/<change_id>.json`——与 §5.3 的 `mcp-hub-change/` 同构（同分区不同前缀），`change_id` 即回滚单位 |
| 微调 / 拒绝 | 「微调」返回**同一个** `CanvasEditor` 句柄（会话不分裂）；「拒绝」丢弃会话、**不落盘**，`reason` 仅随返回值透传（反馈采集归 [08-L6 §1](08-L6-反思演进.md)） |

草稿态与落盘态的边界**只有一条**：编辑期一律不落盘，「接受」是唯一落盘点——由此「拒绝」天然无副作用、「微调」天然可无限次、无论编辑多久都不产生版本。

**调试协议落地口径**：

| 面 | 口径 |
| --- | --- |
| 试跑记录标识 | `trial_id`（[01 §1](01-平台共享契约.md) 契约 ID，本机生成 `trial_<uuid4 前 20 位>`）；落 `execution_log` 分区 `workflow-trial/<trial_id>.json`——与 §1.2 的 `skill-run/` 同分区**不同前缀**，试跑记录不被误当生产执行，两者互不串扫 |
| 试跑门禁 | 只拦**不可执行**类结构问题（成环 / 自环 / 重复节点标识 / 连线或引用指向不存在的节点）；其余发现（Skill 未注册、`skill_id` 形态非法、连线契约不匹配、参数未声明）不拦，落到节点级以失败信封装载——调试期正是要暴露它们 |
| 会话驱动 | 步进式状态机（**非**线程中断）：`idle → running｜paused → completed｜failed｜aborted`；`step` 推进一步，`run_to_end` 逐节点推进（每步前查暂停 / 中止标志），暂停与中止都生效在**节点边界**——进程被杀不在覆盖范围（崩溃恢复不属试跑语义） |
| 逐节点执行 | 经 §1.2 的完整流水线**逐节点**执行（同一 `trace_id`，逐节点追加 `skill_run` 步）；参数解析：`literal` 取字面值、`ref` 从上游节点输出信封的 `data` 按 `path` 取值——路径不存在即该节点失败，**不静默置空** |
| 输入注入 | 按节点注入（`{node_id: {参数名: 值}}`）：**覆盖**该节点参数绑定中的 `literal` 值，快照标注该值来源为注入；对 `ref` 绑定或节点未声明的参数名**不予注入并显式拒绝**（不静默丢弃） |
| 值来源标注 | 每个节点输入快照**逐参数**标注来源：注入 / 上游引用 / 字面量 / Skill 默认值 |
| 失败短路 | 节点信封非 `ok` / `empty` → 其**下游节点一律不执行**并标 `dependency_failed`（写明上游节点），试跑记录状态 `failed` |
| 输出复用面 | 试跑**不登记**输出复用（§1.3）——彩排产物不进生产复用面，正式执行不会取用试跑结果 |
| 历史对比 | 两次试跑记录按 `node_id` 对齐，逐节点列出输出差异（新增 / 消失 / 不同）；比对只读留存、不重跑 |

试跑与正式执行的差别**仅在**「可单步 / 可暂停中止 / 留试跑历史 / 不登记复用」四点——权限口径照旧（经 §1.5 的沙箱与已批准权限集合），不因调试而放宽。

**模板库落地口径**：

| 面 | 口径 |
| --- | --- |
| 模板标识 | `template_id` **不进** [01 §1](01-平台共享契约.md) 契约 ID 表——模板是随包内置的纯数据常量，只在 L1 进程内寻址（不持久化、不分享、不跨会话），不满足 §1 对 ID 的「可本地持久化」要求；跨层只用 fork 产物的 `flow_id` |
| 种子形态 | 官方模板以**纯数据种子**随包内置（形态与 §2 的 `OFFICIAL_PACK` 同构：字段 kwargs 式、可增不可缺、**不含身份字段** `flow_id` / `version`），不落用户存储、不依赖网络（宪法 1 本地优先） |
| 装载校验 | 种子在**装载期**即经 `checked_dag` + `validate_dag` 构造并校验（含 §6 中性化）；种子有错即装载失败，不留到运行期静默 |
| 浏览面 | `list` 逐条给 名称 / 说明 / 节点数 / 引用 Skill 清单 / **缺失 Skill 清单**——依赖提示与 §3.2 复合 Skill 依赖缺失走**同一判定件** `missing_dependencies`，不另造 |
| 预览面 | `load` 返回模板的**只读预览**，身份取临时位 `wf_<注册名>_v0.0`（`0.0` 表未发布，与编辑面画布期临时身份同口径） |
| fork | 产**用户自有副本**：base 由种子的 ASCII `flow_name` 派生；与既有 base 冲突即**加序号**（`_2` / `_3` …），**不拒**——模板名由系统派生、用户对 base 无选择权（与编辑面「接受」的重名即拒口径**有意不同**，见下） |
| fork 落盘 | **不落盘**——fork 产物即「来自模板的草稿」，交编辑面编辑、「接受」才是落盘点（与编辑面同一条边界，故天然带 `ChangeRecord` 变更留痕） |
| 空状态 | `StudioHome` = 模板清单 + 一句话入口 + `is_empty`；判据取 `WorkflowStore.list_all()` 为空（**未创建任何工作流**），模板清单**恒返回**（非空时亦然）；不外抛异常、不返回空对象 |

模板库与编辑面共用**同一条草稿态边界**（编辑期不落盘、「接受」是唯一落盘点），两处唯一的有意差异是**重名的处置**：编辑面的 base 由用户 / LLM 给出，重名即拒是有效反馈；模板的 base 由系统派生，用户无话语权，故加序号。

## 5. MCP Hub 子系统

承载 [Story 8](../PRD-v2-Agent/story-08-mcp-hub.md)。产品内置 MCP Client，允许用户挂载任意 MCP Server：

### 5.1 传输与注册

- 支持 stdio（本地进程）与 HTTP/SSE（远程）两种传输；**默认只允许本地 stdio**，远程 Server 需显式开启并确认「数据会离开本机」警示
- Server 注册表：添加/删除/启用/禁用；添加时连接测试 + 权限申请展示（「这个 Server 想做什么」）+ 逐项批准
- **删除即回收**：`remove_server` 连同该 Server 的映射记录与派生 Skill 一并清除（经组合根接线的回收回调，见 §5.2）

### 5.2 tool → Skill 自动映射

- MCP Server 的每个 tool 自动注册为 Skill（`source: mcp-mapped`），SkillDescriptor 元数据从 MCP schema 派生
- Server 禁用 → 其全部 Skill 立即从可用列表移除，进行中调用中止
- tool 从 Server 消失 → 该 tool 的映射标失效（`vanished`，含变化说明）并从可用列表滤除；tool 回归即自愈为 `active`（映射与描述体保留，可逆）
- tool 契约变化 → 提示用户重新映射（映射关系版本化，遵循 01 §9）

**回收落地口径**：

| 面 | 口径 |
| --- | --- |
| 触发 | 经 `remove_server` 的**可选**回调 `on_server_removed`（缺省 `None` 即行为逐字节不变），由组合根接 `McpSkillMapper.recycle_server`——包内依赖保持 `mapping → registry` 单向，不让注册表反向 import 映射 |
| Server 显式移除 | **硬回收**：删该 Server 全部映射记录 + 反注册其派生 Skill（base 的**全部版本**，含 `skill-update/<base>.json` 待检查标记）；`skill_id` 由 `(server, tool)` 确定性派生，重新挂载即重新注册 |
| tool 从 Server 消失 | **软失效**：映射标 `vanished`（必带变化说明）并从可用列表滤除；记录与描述体保留，tool 回归即自愈为 `active`——瞬态少列一个 tool 不得造成不可逆损失 |
| 范围边界 | 只回收 `source=mcp-mapped` 的派生 Skill；不动 `execution_log` 审计（append-only）；`mcp-lifecycle` 状态记录**不在**本回收动作的范围（其清理属组合根移除序列的**另一步** `McpHubStateMachine.recycle_server`——当前态在 Server 移除后已无意义，留着即成孤儿，见 `T-L1-006.3`）；`sandbox-disabled` 旗标不经回收动作清除，改由**注册侧自愈**兜底——`skill_id` 由 `(server, tool)` 确定性派生、重挂即复用同一 id，故新注册的 Skill 一律不继承旧禁用旗标 |

硬回收 / 软失效的分野取**动作的持久性**：`remove_server` 是用户显式且持久的决定，回收是其预期终态；tool 消失可能是 Server 侧瞬态，硬删不可逆且丢版本历史，故软失效并可自愈。反注册粒度取 base（单版本删除会让 `get_latest` / `list_versions` 语义碎裂）。

### 5.3 状态机与降级

Server 生命周期状态：`connected` / `disconnected` / `reconnecting` / `permission_pending` / `disabled`。崩溃 → 明确提示 + 自动重连（次数可配）+ 可降级到官方数据源 Skill 兜底。**远程** Server 的网络请求经 L0 网关登记（[02-L0 §6](02-L0-本地优先基座.md)），在网络活动面板可见；本地 stdio Server 经 stdin/stdout 管道与 L1 通信，其子进程自身出网**不在**审计范围（已知边界，见 [02-L0 §1](02-L0-本地优先基座.md)）。

### 5.4 权限模型

沿用 01 §10 统一权限模型：MCP Server 按能力粒度声明（读文件/网络/执行命令），首次调用前逐项批准；远程 Server 额外标注数据外发风险。

## 6. 定时调度

- `stock-watch`、`data-aggregate`、`sector-heatmap`、每日报告（[07-L5](07-L5-主动触达.md)）、每周反思（[08-L6](08-L6-反思演进.md)）等均依赖调度器
- 调度器属 L1 基础设施：按 WorkflowDAG 的 `schedule` 与各 Skill 的频率参数触发；调度触发产生的执行同样走 §1.2 完整流水线（留痕、Trace、ResultEnvelope）
- 断网期间到期的调度任务：离线可执行者照常执行（用最后快照并标注），不可执行者在网络恢复后按用户配置补跑或跳过
