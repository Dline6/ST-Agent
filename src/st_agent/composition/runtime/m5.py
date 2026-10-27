"""M5 运行时：M4 面 + 受控自主与七面（工作区 / 能力 / 图谱 / 推理 / 触达 / 设置）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from st_agent.l0 import backup as l0_backup
from st_agent.l0.bootstrap.root import (
    RootBoot,
    bootstrap_path,
    platform_default_root,
)
from st_agent.l0.storage import Store
from st_agent.l0.sync_runner import DataSyncRunner
from st_agent.l1.events import EventBus
from st_agent.l1.runtime import (
    DOTENV_NAME,
    L1Runtime,
    open_runtime,
)
from st_agent.l1.studio import (
    DraftAcceptError,
    DraftIntake,
    DraftSessionError,
    DraftShapeError,
    TemplateLibrary,
    draft_from_dag,
    studio_empty_state,
)
from st_agent.l1.workflow import TrialRunner
from st_agent.l2.memory import (
    ONBOARDING_KIND,
    SHARE_CONFIRMATION,
    ConfidenceModel,
    ConflictQueue,
    FragmentImporter,
    MemoryDeleter,
    MemoryGraph,
    MemoryReader,
    MemoryShare,
    MemoryWriter,
    OnboardingProtocol,
    SliceQuery,
    WritePolicy,
    checked_node,
    memory_policy_family,
    new_node_id,
    official_onboarding_entry,
)
from st_agent.l3.commands import COMMAND_KIND, CommandRegistry, official_command_entry
from st_agent.l3.workspace import SOURCE_BY_COMPONENT_TYPE, WorkspaceFacade, WorkspacePinStore
from st_agent.l3.runtime import (
    ActionGate,
    AgentRunReport,
    LoopBounds,
    NativeToolCallProtocol,
    conclude_agent_run,
    investigate_family,
    run_agent_loop,
)
from st_agent.l6 import (
    DEFAULT_TIER,
    GRADING_CONFIG_ID,
    NO_AUTHORIZATION_REASON,
    NO_REGISTRY_REASON,
    RESET_CONFIRMATIONS,
    RISK_CLASSES,
    TIER_CONFIG_ID,
    TIERS,
    ChangeProposal,
    ChangeRun,
    EvolutionRiskRule,
    L6Error,
    L6Stack,
    PatternObservation,
    PendingChange,
    SkillDraft,
    StudioHandoffError,
    SuggestionDraft,
    TrainingDraft,
    build_l6,
    week_key,
    week_window,
)

from st_agent.composition.agent import (
    _AgentInvestigator,
)
from st_agent.composition.common import (
    EXPORTS_DIR_NAME,
    _LLM_ENDPOINT_ID,
)
from st_agent.composition.facades.capability import (
    McpFacade,
    SkillsFacade,
    StudioFacade,
    TrialSessions,
)
from st_agent.composition.facades.chat import (
    DialogFacade,
)
from st_agent.composition.facades.delivery import (
    DeliveryFacade,
)
from st_agent.composition.facades.memory import (
    GraphFacade,
    MemoryPositionsFacade,
)
from st_agent.composition.facades.reasoning import (
    DeliberationFacade,
)
from st_agent.composition.facades.reflection import (
    ReflectionFacade,
)
from st_agent.composition.facades.settings import (
    SettingsFacade,
)
from st_agent.composition.runtime.m1 import (
    M1Runtime,
)
from st_agent.composition.runtime.m2 import (
    M2Runtime,
)
from st_agent.composition.runtime.m3 import (
    M3Runtime,
    TickResult,
)
from st_agent.composition.runtime.m4 import (
    M4Runtime,
    build_m4_runtime,
)

BACKUP_DIR_SUFFIX = "-backups"
"""缺省备份归档目录的**后缀**（拼在存储根名之后）——目录落在存储根**之外**，
否则「完全清空」删掉整个存储根时会把备份一并删掉（[02 §8.3]）。"""


@dataclass(frozen=True)
class M5Runtime:
    """一次 M5 装配的全部句柄（M4 全套 + 循环端口；由 :func:`build_m5_runtime` 构造）。

    与 M4 的关系同 M3→M4 的做法：**裹**住 :class:`M4Runtime` 而非改造它——M4 是 M4 关卡的
    冻结交付物；而「L3 循环经真 L1 执行、经真 L6 授权、由组合根装配并接进 `investigate`
    去向」正是本关卡要证明的装配事实（[`T-INT-006`]）。

    ``investigations`` 是注入总线的循环端口（:class:`_AgentInvestigator`）——**装配痕迹**，
    供集成套件断言「循环端口真的在链上」（GWT-7），而非表现层的取数面。
    """

    m4: M4Runtime
    investigations: Any
    memory: MemoryPositionsFacade
    """记忆区的**持仓 / 关注读面**（组合根造好；表现层经鸭子端口 `memory` 消费）。

    行情源缺席时本面仍在（`positions()` 据实回 `available=False` + 原因），故表现层拿到的
    永远是一个可问的面，而不是 `None`——**「未接线」与「接了但行情不可用」是两回事**
    （[01 §5]：六态不可混用）。
    """

    trial_sessions: TrialSessions = field(default_factory=TrialSessions)
    """调试控制台的**进程内试跑会话表**（[`T-UI-015.3`]）。

    必须挂在运行时对象上（而不是每次取门面时新建）——否则「单步 → 暂停 → 继续」跨请求
    就丢了会话（见 :class:`TrialSessions`）。
    """

    data_sync: DataSyncRunner | None = None
    """数据总览与手动同步的**运行时唯一实例**（批次状态必须跨请求存活；[D-129]）。"""

    dotenv_path: Path | None = None
    """启动引导 ``.env`` 的实际路径（装配入口给出的值；[D-129]）。"""

    bootstrap_file: Path | None = None
    """存储根启动指针文件（缺省见 :func:`~st_agent.l0.bootstrap.root.bootstrap_path`）。"""

    # ── 与 M1–M4 同形的便捷取用 ──────────────────────────────────────────

    @property
    def m1(self) -> M1Runtime:
        return self.m4.m1

    @property
    def m2(self) -> M2Runtime:
        return self.m4.m2

    @property
    def m3(self) -> M3Runtime:
        return self.m4.m3

    @property
    def l6(self) -> L6Stack:
        return self.m4.l6

    @property
    def events(self) -> EventBus:
        return self.m4.events

    @property
    def store(self):
        return self.m4.store

    @property
    def chat(self) -> DialogFacade:
        return self.m4.chat

    @property
    def reflection(self) -> ReflectionFacade:
        return self.m4.reflection

    @property
    def ecosystem(self):
        return self.m4.ecosystem

    # ── M6 各面门面（薄；[`T-UI-011.1`]） ────────────────────────────────
    # 表现层经**同形鸭子端口**（`skills` / `mcp` / `graph` / `deliberation` / `delivery` /
    # `settings`）消费；`ui` 不 import 本模块，只按端口名取值。

    @property
    def skills(self) -> SkillsFacade:
        """L1 能力（Skill）面。"""
        runtime = self.m1.runtime
        return SkillsFacade(registry=runtime.skills, store=runtime.store)

    @property
    def mcp(self) -> McpFacade:
        """L1 能力（MCP）面。"""
        runtime = self.m1.runtime
        return McpFacade(
            servers=runtime.mcp_servers,
            permissions=runtime.mcp_permissions,
            mapper=runtime.mcp_mapper,
            machine=runtime.mcp_machine,
            skills=runtime.skills,
            remove_server=runtime.remove_mcp_server,
        )

    @property
    def studio(self) -> StudioFacade:
        """L1 Studio 面（[`T-UI-015.3`]）：自建工作流 / 模板库 / 试跑与进程内会话表。

        `open_template` 在此装配成闭包——它要同时够到 L1 的模板库与 **L6 的画布交接面**
        （`StudioHandoff.open_draft`），而这两处只有组合根同时持有（[铁律 7]：表现层只经
        鸭子端口消费，不认识任何一层）。
        """
        runtime = self.m1.runtime
        templates = TemplateLibrary(runtime.store, runtime.skills)

        def open_template(template_id: str) -> Any:
            """fork 一个模板 → 包成草稿 → 交既有画布会话（与提案来源**同一条**落画布路径）。"""
            studio = self.l6.studio
            if studio is None:
                raise StudioHandoffError(
                    "未接入 Studio 草稿接收面（DraftIntake），无法开画布（08 §4）"
                )
            dag = templates.fork(template_id)
            return studio.open_draft(f"tpl:{template_id}", draft_from_dag(dag))

        return StudioFacade(
            workflows=runtime.workflows,
            templates=templates,
            trials=TrialRunner(runtime.store, runtime.skills, runtime.runner.for_trials()),
            sessions=self.trial_sessions,
            skills=runtime.skills,
            open_template=open_template,
            home=studio_empty_state(runtime.store, runtime.skills),
        )

    @property
    def graph(self) -> GraphFacade:
        """L2 记忆图谱面。"""
        return GraphFacade(
            graph=self.m1.graph,
            reader=self.m1.reader,
            writer=self.m1.writer,
            sharing=MemoryShare(self.m1.graph),
            onboarding=self.m1.onboarding,
        )

    @property
    def deliberation(self) -> DeliberationFacade:
        """L4 多视角推理面。"""
        return DeliberationFacade(
            roster=self.m2.roster,
            deliberation=self.m2.deliberation,
            viewer=self.m2.viewer,
            analyze=self.m2.analyze,
            reader=self.m2.reader,
        )

    @property
    def delivery(self) -> DeliveryFacade:
        """L5 主动触达面（[`T-UI-017`] 补 `fatigue` / `feedback` / `store` 三处句柄）。

        `fatigue` 与 `delivery` 同取自 [`L5Stack`]；`feedback` 取 **L3 对话门面**的
        `record_feedback`（[01 §1]：`feedback_id` 产生方＝交互层）——疲劳答复与五动作反馈
        因此共用**同一条**反馈写通道；`store` 只为列出已生成的日报（`daily_report/` 前缀）。
        """
        l5 = self.m3.l5
        return DeliveryFacade(
            budget=l5.budget,
            policies=l5.policies,
            delivery=l5.delivery,
            reports=l5.reports,
            fatigue=l5.fatigue,
            feedback=self.chat.record_feedback,
            store=self.store,
        )

    @property
    def settings(self) -> SettingsFacade:
        """L0 存储 / 配置面（[`T-UI-018`] 补齐备份 / 网关 / 端点 / 凭据 / 目录五处句柄）。

        句柄全部向下取自 :class:`~st_agent.l1.runtime.L1Runtime`（[铁律 7]）：`store` 与
        `config_registry` 本就在，另四支（`gateway` / `endpoints` / `vault` / `convert_store`）
        此前**没有门面透出**——设置区需要它们，故在此一并过桥。`backup` 是 L0 的**模块**
        （备份 / 恢复 / 清空本体是模块级函数），门面只传引用、不包装。
        """
        runtime = self.m1.runtime
        root = self.store.root
        return SettingsFacade(
            config_registry=runtime.config_registry,
            store=self.store,
            gateway=runtime.gateway,
            backup=l0_backup,
            endpoints=runtime.endpoints,
            vault=runtime.vault,
            exports_dir=root / EXPORTS_DIR_NAME,
            backups_dir=root.parent / f"{root.name}{BACKUP_DIR_SUFFIX}",
            set_online=runtime.set_online,
            convert_store=Store.convert,
            llm_runtime=runtime,
            dotenv_path=self.dotenv_path,
            root_boot=RootBoot(
                path=self.bootstrap_file or bootstrap_path(),
                default=platform_default_root(),
            ),
            data_sync=self.data_sync,
        )

    @property
    def workspace(self) -> WorkspaceFacade:
        """工作区（钉住的持久组件）面（[`T-UI-013`]）。

        两半都在组合根收齐，表现层只经鸭子端口消费：

        - **落盘**——:class:`WorkspacePinStore` 落 L0 的 `config` 分区，钉住物因此走出
          对话流、跨会话留存；
        - **重解析来源表**——`context_card` 的取数口是 :meth:`DialogFacade.context_card`
          （只读记忆、不跑 Skill、不写盘），故它是本批**唯一**接上真解析器的型；其余型
          钉得住，但刷新如实回 `unavailable` + 点名（[01 §5]：不伪造一份「刷新过」的旧数据）。
          键取自 :data:`SOURCE_BY_COMPONENT_TYPE` 这同一处真相源，不在此再写一遍字面量
          ——[01 §12] 的「动作不进描述」要求键是**枚举值**而不是 URL。
        """
        return WorkspaceFacade(
            store=WorkspacePinStore(self.store),
            resolvers={SOURCE_BY_COMPONENT_TYPE["context_card"]: self.chat.context_card},
        )

    @property
    def commands(self) -> CommandRegistry:
        """快捷指令注册表（[05 §8]）——Chat 主界面 `/` 补全清单的取数口（[`T-UI-012.1`]）。

        直接透出 L3 维持的 `CommandRegistry`（含官方 Pack 的 `command` kind 整组替换），
        故前端经 `GET /api/chat/commands` 取真注册表、不另造一份枚举。
        """
        return self.m1.commands

    def tick(self, now: datetime | None = None) -> TickResult:
        """推进一轮主动服务与每周反思（同 :meth:`M4Runtime.tick`；M5 不新增节奏）。"""
        return self.m4.tick(now)

    def training_callbacks(self) -> tuple[Any, ...]:
        """待回访项读面（同 :meth:`M4Runtime.training_callbacks`）。"""
        return self.m4.training_callbacks()


def build_m5_runtime(
    root: Path | str,
    passphrase: str,
    *,
    market_query: Any = None,
    dotenv_path: Path | str | None = None,
    bootstrap_file: Path | str | None = None,
    **kwargs: Any,
) -> M5Runtime:
    """装配 M5 全栈（＝ M4 的 L0–L6 + ECO + **受控自主运行时**；[`T-INT-006`] 的**生产组合根**）。

    与 :func:`build_m4_runtime` 复用**同一段**装配，差别只有一处接线（在组合根、不改任何层）：
    经 `investigations_of` 缝装出循环端口并注入总线——故 `investigate` 去向从「未接入
    （fail-closed + 点名）」变为真链路（[05 §4]）。

    循环端口 = :class:`_AgentInvestigator`，其依赖全部**向下取用**（[铁律 7](../../项目管理/工程宪法.md)）：
    L1 的 `runner` / `skills`（工具目录）/ `endpoints` / `llm`（经 :class:`NativeToolCallProtocol`）、
    L3 的 `bounds`（双上界）、L6 的 `agent_authorization`（授权判据，经**鸭子端口**）。

    其余关键字透传 :func:`build_m4_runtime` 的同名项（渠道 / 原生端口 / 云端参数 / 时钟 /
    `llm_env` / `llm_post` 等）。**「端点能力启动探测」不需本根接线**——它住在 `build_l1_runtime`
    的引导装载分支里，装配 M5 时自然执行（[`T-AGT-002`]）。
    """
    holder: list[_AgentInvestigator] = []          # 装配缝唯一被调一次，取回端口句柄

    def investigations_of(runtime: L1Runtime, l6_stack: L6Stack, bounds: LoopBounds):
        """造循环端口（在此处而非调用方，因为这些句柄在 `open_runtime` 之后才存在）。"""
        port = _AgentInvestigator(
            store=runtime.store,
            runner=runtime.runner,
            skills=runtime.skills,
            bounds=bounds,
            protocol=NativeToolCallProtocol(
                llm=runtime.llm, endpoints=runtime.endpoints,
                endpoint_id=_LLM_ENDPOINT_ID,
            ),
            endpoint_id=_LLM_ENDPOINT_ID,
            authorization=l6_stack.agent_authorization,
        )
        holder.append(port)
        return port

    m4 = build_m4_runtime(
        root, passphrase, market_query=market_query, dotenv_path=dotenv_path,
        investigations_of=investigations_of, **kwargs
    )
    memory = MemoryPositionsFacade(reader=m4.m1.reader, market=market_query)
    data_sync = DataSyncRunner(
        store=m4.m1.store,
        gateway=m4.m1.runtime.gateway,
        watchlist=lambda: (
            memory.codes("holdings") + memory.codes("watchlist")
        ),
    )
    # 与 open_runtime 的取值口径一致：显式路径优先，缺省 `<cwd>/.env`（[T-L1-011]）。
    resolved_dotenv = Path(dotenv_path) if dotenv_path is not None else Path.cwd() / DOTENV_NAME
    return M5Runtime(
        m4=m4,
        investigations=holder[0],
        memory=memory,
        data_sync=data_sync,
        dotenv_path=resolved_dotenv,
        bootstrap_file=Path(bootstrap_file) if bootstrap_file is not None else None,
    )
