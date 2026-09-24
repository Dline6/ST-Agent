# 产品目标与设计原则

> 本文档是全局权威：定义产品要达成什么、7 条不可妥协的设计原则、明确不追求的反原则、以及承载全局的关键假设。
> **冲突仲裁优先级**：本文档的设计原则 > [05-constraints.md](05-constraints.md) 的约束 > 各 story 内描述。任何 story 不得违背三条硬约束（本地优先、中性视角、可配置性优先）。
> 功能范围见 [README.md](README.md)；平台共享契约见 [10-platform-capabilities.md](10-platform-capabilities.md)。

## 产品目标（outcome-framed，非量化）

让个人投资者在 ST 场景下，用**一个真正属于自己、可离线运行、越用越懂他**的对话式 Agent 副驾，获得**及时、可解释、多视角、贴合个人偏好演化**的投资决策辅助，同时对**自己的数据、记忆、配置、Skill 集合拥有完全主权**。

> 本文档不含量化成功指标与考核口径（如回测目标值、技术指标判定标准等）。产品成效度量交由独立的《指标与度量方案》定义，不与产品需求混写（见 [README.md](README.md) 末尾占位清单）。

## 7 条产品设计原则（不可妥协）

### 原则 1 · 本地优先（Local-first）

所有用户数据（记忆、持仓、偏好、Skill 配置、反思日志）**必须本地存储**；产品可在完全离线状态下运行核心能力（LLM 调用除外，见下）；即使调用云端 LLM，也由用户自带 API Key，产品方不接触、不中转、不留存任何用户数据。
→ 落地于 [story-05 本地优先基座](story-05-local-first.md)。

### 原则 2 · 中性视角（Non-anthropomorphic）

多视角推理中的每个视角**必须使用中性、功能化命名**（如"机会视角""风险视角""流动性视角"），**不得拟人化**（不取人名、不设性格、不做情感表达、不用第一人称"我认为"），输出**结构化观点**而非"角色台词"。
→ 落地于 [story-04 多视角推理](story-04-multi-lens.md)。此原则同时是合规策略（避免"AI 荐股""AI 分析师"定性风险，见 [05-constraints.md](05-constraints.md)）。

### 原则 3 · 可配置性优先（Configurability-first）

所有能力必须提供**对话入口 + 结构化配置面板**双通道；用户既能"说人话"配置，也能"看到全部参数"精调；配置本身是产品的一等公民，可导出、可导入、可版本化。**当可配置性与可扩展性冲突时，优先保障可配置性。**
→ 作为共享契约落地于 [10-platform-capabilities.md](10-platform-capabilities.md)，贯穿所有 story。

### 原则 4 · 可解释性（Explainability）

每条结论可追溯完整推理链（用了哪些数据 → 哪些 Skill → 哪些视角 → 如何汇总）；用户可随时点开查看。
→ 落地于 [story-01 推理链可视化](story-01-chat-as-os.md)、[story-04 视角追问](story-04-multi-lens.md)。

### 原则 5 · 决策自主（User Sovereignty）

多视角分歧必须并列呈现，不合并、不掩盖、不给"统一建议"；系统提示风险与机会，但不替用户下单、不承诺收益。
→ 落地于 [story-04 分歧图](story-04-multi-lens.md)、[05-constraints.md 合规约束](05-constraints.md)。

### 原则 6 · 共同演化（Co-evolution）

产品应随用户使用而演进；每次演进必须透明告知、可撤销、可回滚到"出厂设置"。
→ 落地于 [story-09 Reflection Loop](story-09-reflection-loop.md)。

### 原则 7 · 低门槛可成长（Progressive Depth）

普通用户开箱即用（对话即可），进阶用户可编排 Skill、挂载 MCP、编辑记忆图谱；深度不设上限，但每一层都要有引导。
→ 落地于 [story-01](story-01-chat-as-os.md)（普通用户）、[story-06](story-06-skill-studio.md)/[story-08](story-08-mcp-hub.md)（进阶用户）。

## Anti-metrics（反原则，明确不追求）

- **不追求"推送条数最大化"**：为刷存在感而高频推送会制造噪音、破坏信任，反而降低留存。追求"推送被认可为有用"，而非推送量。（对应 [story-07](story-07-ambient-delivery.md)）
- **不包装"稳赚信号"**：ST 是高风险标的，任何宣称必涨/稳赚的信号都是误导且踩合规红线。（对应 [05-constraints.md](05-constraints.md)）
- **不追求"Agent 拟人化程度"**：拟人化会诱导情感依赖、削弱用户决策自主、并可能踩合规红线；本产品明确以中性视角呈现分析。（对应原则 2）
- **不追求"用户日均使用时长"**：这是注意力经济的坏指标；本产品追求"用户注意力预算内的高价值触达"，鼓励用完就走。（对应 [story-07](story-07-ambient-delivery.md)）
- **不追求"云端 MAU / DAU"**：本地优先产品的健康度不看云端活跃度，看"本地数据丰富度 + 用户主动打开频率 + 反思报告采纳率"。（对应原则 1）

## 关键假设（critical assumption）

> 在**本地优先 + 中性视角 + 可配置性优先**的三重约束下，「Chat-as-OS + Skills Runtime + Memory Graph + Multi-Lens Deliberation + Ambient Delivery + Reflection Loop」六层架构，能够把 ST 公告/舆情等非结构化信息，转化为对散户真正有用、可解释、可信赖、且愿意长期共同演化的多视角研判。

若这条不成立（即用户不愿"共同演化"、只想开箱即用），则整个范式需退回"表单化配置 + 预置功能"形态——这是应最先验证的假设。承载该假设的 Story 在 [README.md](README.md) 索引中以 ⭐ 标注（[story-01](story-01-chat-as-os.md)、[story-02](story-02-skills-runtime.md)、[story-03](story-03-memory-graph.md)、[story-04](story-04-multi-lens.md)、[story-09](story-09-reflection-loop.md)）。
