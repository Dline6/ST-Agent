---
id: T-ECO-002.2
parent: T-ECO-002
title: 官方 Skill 索引与生态边界
story: ../../docs/PRD-v2-Agent/story-10-skill-sharing.md
arch: ../../docs/技术架构-v2/09-生态与分享.md
arch_link: "[09 §4·§6](../../docs/技术架构-v2/09-生态与分享.md)"
priority: P1
milestone: M4
depends_on: [T-ECO-001.1, T-L0-004, T-L1-001]
status: done
decisions: [D-080]
verify: 只读索引（官方 Pack 更新 + 认证社区推荐，含描述/外部下载地址/校验和）· 浏览经 `EgressGateway.kind=index_browse` 留痕（销 L0 册 `A1b`）· 离线 / 无端点 / 条目非法三类显式失败、不返回半截索引 · 空状态判据 + 生态边界清单 · 新增 18 例 · 范围 **743** / 全量 **2744** 全绿 · `verify_docs --strict` 全过；留痕见 执行日志 `[T-ECO-002.2]`
---

# T-ECO-002.2 · 官方 Skill 索引与生态边界

## 目标
交付 [09 §4](../../docs/技术架构-v2/09-生态与分享.md) 的**只读官方 Skill 索引**（官方 Pack 更新信息 + 认证的社区 Skill 推荐，各含描述 + 外部下载地址 + 校验和）与 [09 §6](../../docs/技术架构-v2/09-生态与分享.md) 的**生态边界**落点（「不做」清单 + 空状态判据）。

**索引只给元数据、产品不经手文件**：浏览是只读动作，下载由用户直连外部地址（[09 §4](../../docs/技术架构-v2/09-生态与分享.md)），文件到手后回 [`T-ECO-002.1`](T-ECO-002.1-导入校验流水线.md) 的导入流水线——产品方不经手任何用户数据与 Skill 文件。

**索引浏览接入出网审计网关**是本叶的关键接线（[09 §4](../../docs/技术架构-v2/09-生态与分享.md) 的官方索引是 `index_browse` 这一出网类目的载体）：它是 [L0 册 `A1b`](../遗留问题/L0-遗留问题.md) 的解封条件所要求的那一问——「官方 Skill 索引浏览接入网关」。

## 验收标准（Given-When-Then）
- **GWT-1**：Given 一次索引浏览，When 拉取官方索引，Then 请求经 [`EgressGateway.execute("index_browse", host, initiator=…, purpose=…)`](../../src/st_agent/l0/net/gateway.py) 发出、按 [02 §6](../../docs/技术架构-v2/02-L0-本地优先基座.md) 留痕，且可由 `gateway.query(kind="index_browse")` 查回该条审计——**销 [L0 册 `A1b`](../遗留问题/L0-遗留问题.md)**
- **GWT-2**：Given 索引内容，When 浏览，Then 逐条给出**官方 Pack 更新**与**认证社区 Skill 推荐**（描述 + 外部下载地址 + 校验和），**只读、不下载**——产品不代理下载、不经手文件
- **GWT-3**：Given 索引条目携外部下载地址，When 用户取用，Then 该地址**只作展示与指引**（明示「下载走外部渠道，导入走本机校验」），本叶不发生任何文件获取
- **GWT-4**：Given 用户未导入任何第三方 Skill，When 取 Skill 库状态，Then 空状态判据成立，并给出「你的 Skill 库目前只有官方 Pack」+ 引导浏览索引（[story-10](../../docs/PRD-v2-Agent/story-10-skill-sharing.md) 空状态）
- **GWT-5**：Given 生态边界面，When 被消费，Then 明确列出**不做**的事项（中央商店 / 付费交易 / 自动更新订阅），与 [09 §6](../../docs/技术架构-v2/09-生态与分享.md) 逐条对应，措辞中性（[01 §6](../../docs/技术架构-v2/01-平台共享契约.md)）

## 接口面
- **输入**（逐条点名上游任务 id + 具体 API/落盘位置）：
  - [`EgressGateway.execute`](../../src/st_agent/l0/net/gateway.py) / `.query`（[T-L0-004](done/M0/T-L0-004-出网审计网关离线能力分级.md)）——索引拉取的出网通道与审计面；`index_browse` 已在其合法类目内（[02 §6](../../docs/技术架构-v2/02-L0-本地优先基座.md)），落 `execution_log/` 分段日志
  - [`SkillRegistry.list_all()`](../../src/st_agent/l1/skills/registry.py) / `pending_updates()`（[T-L1-001](done/M0/T-L1-001-SkillRuntime执行流水线沙箱输出复用版.md)）——「官方 Pack 更新信息」与空状态判据（按 `source=="official"` / `=="imported"` 分类）
  - [`ShareContainer`](../../src/st_agent/eco/container.py) 的 `checksum` 口径（[T-ECO-001.1](T-ECO-001.1-四类分享物统一容器与格式.md)）——索引条目的「校验和」与分享物校验和**同一个算法**（受赠方拿到文件后本地复核，两侧不能分叉）
  - 契约面：[`SemVer`](../../src/st_agent/contracts/registry_types.py)（条目版本）· [`ResultEnvelope`](../../src/st_agent/contracts/result_envelope.py)（网关以信封判不可用，本层据此显式报错、**不留半截数据**）· [01 §6](../../docs/技术架构-v2/01-平台共享契约.md)（措辞中性）
- **输出**（本叶交付的公共面与落点）：
  - [`src/st_agent/eco/index.py`](../../src/st_agent/eco/)：`IndexEntry`（kind：官方 Pack 更新 / 认证社区 Skill 推荐 · 名称 + 描述 · 外部下载地址 · 校验和 · 版本）· `OfficialIndex`（只读浏览面，拉取经注入的网关）· `ECOSYSTEM_BOUNDARY`（[09 §6](../../docs/技术架构-v2/09-生态与分享.md) 的「不做」清单常量）· `has_third_party()`（空状态判据，消费 `SkillRegistry.list_all()`）
  - [`src/st_agent/eco/errors.py`](../../src/st_agent/eco/errors.py) 增 `ShareIndexError`（索引不可用 / 条目非法；与 `ShareImportError` 分工）
  - 用例 [`tests/eco/test_index.py`](../../tests/eco/)
  - **落盘位置**：**无本机数据分区落点**——索引是只读元数据；出网留痕落 L0 既有的 `execution_log/` 分段日志（不新造分区）

## 可关闭的遗留
- [L0 册 `A1b`](../遗留问题/L0-遗留问题.md)（官方 Skill 索引浏览的审计调用点未接入，`Kind=index_browse`）→ **本叶关闭**：索引浏览接入 [`EgressGateway`](../../src/st_agent/l0/net/gateway.py) 的 `index_browse` 类目并留痕（GWT-1）；收工时按[册规](../遗留问题/README.md) 从未闭区移入册尾「已闭（备查）」并写关闭留痕。
- 其余逐册读 [L0](../遗留问题/L0-遗留问题.md) / [L1](../遗留问题/L1-遗留问题.md) / [L2](../遗留问题/L2-遗留问题.md) / [L3](../遗留问题/L3-遗留问题.md) 未闭区，无「归属＝本叶」者。

## 假设与前提
- **A1 · 官方索引的载体＝经 L0 网关拉取的只读资源**（2026-10-05 ④ 对齐拍定）——[09 §4](../../docs/技术架构-v2/09-生态与分享.md) 原措辞（「可随官方 Pack 更新的规则资源」）与 `index_browse` 这一等出网类目、[L0 册 `A1b`](../遗留问题/L0-遗留问题.md) 的点名存在张力，本批取「经网关拉取」并已把口径写进 [09 §4](../../docs/技术架构-v2/09-生态与分享.md)（[铁律 8](../工程宪法.md)，改码之前）。若错（改判本地 Pack 资源）：返工面＝本叶浏览面 + `A1b` 的处置（`index_browse` 将永无调用点）。验证方式：GWT-1 的审计断言（走网关且 `query(kind="index_browse")` 查得回）+ 离线/审计关闭两种显式分支用例。
- **A2 · 索引条目的「校验和」与分享物校验和同一算法**——前提：[09 §4](../../docs/技术架构-v2/09-生态与分享.md) 的索引条目含校验和，其用途是「受赠方核对拿到的文件」，而文件正是 [T-ECO-001.1](T-ECO-001.1-四类分享物统一容器与格式.md) 的容器（`sha256` hex 64，[`eco.checksum`](../../src/st_agent/eco/checksum.py) 单一实现）。若错（索引用另一套摘要）：返工面＝条目形态与核对面。验证方式：同一容器在两侧算出的校验和逐字符相等。
- **A3 · 索引内容不落用户数据分区**——前提：索引是产品方维护的只读元数据，非用户数据（不进备份 / 导出 / `data_cache`）。若错：返工面＝落点与备份口径。验证方式：浏览后断言 `Store` 各数据分区未见新文件（出网留痕除外，那是 L0 既有面）。
- **A4 · 「新用户未导入任何第三方 Skill」＝无一 `source=="imported"` 的 Skill**——前提：导入物在 [`SkillRegistry`](../../src/st_agent/l1/skills/registry.py) 中以 `source="imported"` 落库（[09 §5](../../docs/技术架构-v2/09-生态与分享.md)）。若错：返工面＝空状态判据。验证方式：空库与含导入物两种情形的判据用例。
- **A5 · 索引更新频率与恶意 Skill 检测规则库的维护机制不在本叶**——[09 §4](../../docs/技术架构-v2/09-生态与分享.md) 已明记这两项属 **PRD 待澄清项**（架构上「均为可随官方 Pack 更新的规则资源」）。本叶只交付**载体与浏览面**，不含该维护机制；不阻塞本叶（④ 对齐时复核是否登记未决）。

## 涉及契约
- [09 §4 官方 Skill 索引 / §6 生态边界](../../docs/技术架构-v2/09-生态与分享.md)——本叶的 What/How 来源
- [02 §6 出网审计网关](../../docs/技术架构-v2/02-L0-本地优先基座.md)（`index_browse` 类目与留痕；**默认可关**，关闭时留可判的「未开启」态）
- [01 §6 中性化](../../docs/技术架构-v2/01-平台共享契约.md)（展示措辞）· [01 §9 版本化规范](../../docs/技术架构-v2/01-平台共享契约.md)（条目版本）· [01 §1 标识体系](../../docs/技术架构-v2/01-平台共享契约.md)（**不新增 ID 类**）
- [05 §6 / 01 §12](../../docs/技术架构-v2/01-平台共享契约.md)（若索引浏览要成组件，走既有 UI 描述契约，**本叶不新增组件类型**）

## 参考
- Story（What）：[skill-sharing](../../docs/PRD-v2-Agent/story-10-skill-sharing.md)（GWT「浏览官方 Skill 索引」/ 空状态「你的 Skill 库目前只有官方 Pack」；Out of Scope 中央商店 / 付费 / 自动更新订阅）
- 决策：[D-044](../决策日志.md)（ECO 提 P1 + 层位置于 L4 之后）
- 上游交付方／取材面：[T-L0-004](done/M0/T-L0-004-出网审计网关离线能力分级.md) · [T-L1-001](done/M0/T-L1-001-SkillRuntime执行流水线沙箱输出复用版.md) · [T-ECO-001.1](T-ECO-001.1-四类分享物统一容器与格式.md)
- 同批：[`T-ECO-002.1`](T-ECO-002.1-导入校验流水线.md)（父任务 [`T-ECO-002`](T-ECO-002-导入校验流水线官方Skill索引生态边界.md) 的另一叶）

## 备注
本叶只做索引导航与边界面；导入校验归 `.1`。实现细节留给代码 / commit / 执行日志。
