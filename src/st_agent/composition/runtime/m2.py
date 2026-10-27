"""M2 运行时：M1 面 + L4，并把 `analyze` 去向接上真链路。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from st_agent.l1.runtime import (
    DOTENV_NAME,
    L1Runtime,
    open_runtime,
)
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
from st_agent.l3.dispatch import DispatchBus, DispatchOutcome
from st_agent.l4.analyze import AnalyzeService
from st_agent.l4.crosscheck import CrossExaminer
from st_agent.l4.deliberation import Deliberation
from st_agent.l4.divergence_view import DivergenceViewer
from st_agent.l4.ports import (
    LlmEvidenceReviewer,
    LlmOpinionSynthesizer,
    MarketDimensionCatalog,
)
from st_agent.l4.roster import LensRoster

from st_agent.composition.common import (
    _LLM_ENDPOINT_ID,
    _now,
)
from st_agent.composition.facades.chat import (
    DialogFacade,
)
from st_agent.composition.runtime.m1 import (
    M1Runtime,
    _L2Stack,
    _L3Stack,
    _as_kwargs,
    _bind_market,
    _build_l2,
    _build_l3,
)

@dataclass(frozen=True)
class M2Runtime:
    """一次 M2 装配的全部句柄（M1 全套 + L4 面；由 :func:`build_m2_runtime` 构造）。

    L4 面之所以**并列**在 :attr:`m1` 之外而非塞进 `M1Runtime`：`M1Runtime` 是 M1
    关卡的冻结交付物，改造它会让 M1 的用例与本对象互相牵连；M2 = M1 + L4 这层
    组合关系本身就是本关卡要证明的装配事实（[`T-INT-003`]）。
    """

    m1: M1Runtime
    roster: LensRoster
    deliberation: Deliberation
    examiner: CrossExaminer
    viewer: DivergenceViewer
    analyze: AnalyzeService

    @property
    def store(self):
        return self.m1.store

    @property
    def chat(self) -> DialogFacade:
        """对话门面（L3 侧；已含 `analyze` 去向的分歧图渲染支）。"""
        return self.m1.chat

    @property
    def reader(self) -> MemoryReader:
        return self.m1.reader

    @property
    def writer(self) -> MemoryWriter:
        return self.m1.writer

    @property
    def bus(self) -> DispatchBus:
        return self.m1.bus


@dataclass(frozen=True)
class _L4Stack:
    """L4 面（[`T-INT-003`]）：阵容 + 编排 + 对照 + 视图 + `analyze` 去向的装配件。"""

    roster: LensRoster
    deliberation: Deliberation
    examiner: CrossExaminer
    viewer: DivergenceViewer
    analyze: AnalyzeService


def _build_l4(
    runtime: L1Runtime,
    l2: _L2Stack,
    *,
    now: Any,
    market_query: Any,
    synthesizer: Any,
    reviewer: Any,
    catalog: Any,
    executor: Any = None,
) -> _L4Stack:
    """叠 L4（[06](../../docs/技术架构-v2/06-L4-多视角推理.md) 视角 → 编排 → 对照 → 视图）。

    三个鸭子端口**默认装真实现**（[`l4/ports`](../src/st_agent/l4/ports.py)，[`T-INT-003`] A3）：
    方向研判与证据重研判经 `runtime.llm` 出网、盲点目录读本地市场库。**离线关卡**经
    关键字参数覆写为确定性件（同 M1 关卡注入确定性理解器的口径）。
    """
    store = runtime.store
    # 视角阵容：7 内置幂等播种 + 用户可增删；`skill_bundle` 的存在性校验经 L1 注册表
    # （向下依赖合法，铁律 7）。
    roster = LensRoster(store, skills=runtime.skills)
    roster.seed_builtin()

    if synthesizer is None:
        synthesizer = LlmOpinionSynthesizer(runtime.llm, _LLM_ENDPOINT_ID)
    if reviewer is None:
        reviewer = LlmEvidenceReviewer(runtime.llm, _LLM_ENDPOINT_ID)
    if catalog is None:
        catalog = MarketDimensionCatalog(market_query, skills=runtime.skills)

    deliberation = Deliberation(
        roster=roster, runner=runtime.runner, reader=l2.reader, writer=l2.writer,
        synthesizer=synthesizer, executor=executor, now=now,
    )
    examiner = CrossExaminer(roster=roster, catalog=catalog, reviewer=reviewer, now=now)
    viewer = DivergenceViewer(roster=roster)
    analyze = AnalyzeService(
        deliberation=deliberation, examiner=examiner, viewer=viewer,
    )
    return _L4Stack(
        roster=roster, deliberation=deliberation, examiner=examiner,
        viewer=viewer, analyze=analyze,
    )


def _build_m2_pieces(
    root: Path | str,
    passphrase: str,
    *,
    create: bool = False,
    market_query: Any = None,
    sender: Any = None,
    transport: Any = None,
    llm_env: Mapping[str, str] | None = None,
    dotenv_path: Path | str | None = None,
    understander: Any = None,
    synthesizer: Any = None,
    reviewer: Any = None,
    catalog: Any = None,
    executor: Any = None,
    now: Any = _now,
    events: Any = None,
    feedback_sink: Any = None,
    deliberations_of: Any = None,
    **l1_kwargs: Any,
) -> tuple[L1Runtime, _L2Stack, _L3Stack, _L4Stack]:
    """M2 / M3 共用的装配段：打开运行时 → L2 → L4 → L3（[`T-INT-004`] 抽自 `build_m2_runtime`）。

    抽出它只为让 **M3 组合根复用同一段**（[`T-INT-004`] A1）：M2 侧传的实参逐条不变，
    故 [`build_m2_runtime`] 的行为与抽出前一致。

    :param events / feedback_sink: 事件面（M3 传真总线；M2 缺省 ``None`` ＝逐字节不变）。
    :param deliberations_of: 把 L4 的 `analyze` 端口**包一层**再注入总线的钩子
        （M3 用它接「Deliberation ⇒ 信号」的产生点）；缺省直接注入原件。
    """
    runtime = open_runtime(
        root, passphrase, create=create, market_query=market_query,
        sender=sender, transport=transport, llm_env=llm_env,
        dotenv_path=dotenv_path, **l1_kwargs,
    )
    _bind_market(market_query, runtime.store)
    l2 = _build_l2(runtime, now=now, events=events)
    l4 = _build_l4(
        runtime, l2, now=now, market_query=market_query,
        synthesizer=synthesizer, reviewer=reviewer, catalog=catalog, executor=executor,
    )
    deliberations = l4.analyze if deliberations_of is None else deliberations_of(l4.analyze)
    l3 = _build_l3(
        runtime, l2, now=now, understander=understander, deliberations=deliberations,
        events=events, feedback_sink=feedback_sink,
    )
    return runtime, l2, l3, l4


def build_m2_runtime(
    root: Path | str,
    passphrase: str,
    *,
    create: bool = False,
    market_query: Any = None,
    sender: Any = None,
    transport: Any = None,
    llm_env: Mapping[str, str] | None = None,
    dotenv_path: Path | str | None = None,
    understander: Any = None,
    synthesizer: Any = None,
    reviewer: Any = None,
    catalog: Any = None,
    executor: Any = None,
    now: Any = _now,
    **l1_kwargs: Any,
) -> M2Runtime:
    """装配 M2 全栈（＝ M1 的 L0–L3 + L4；[`T-INT-003`] 的**生产组合根**）。

    与 :func:`build_m1_runtime` 走**同一段** L2/L3 装配（本模块的私有栈构造器），
    差别只有两处：① 在 L2 之上叠 L4 面；② 把 L4 的 `AnalyzeService` 作为
    `analyze` 去向端口注入总线——[铁律 7](../../项目管理/工程宪法.md) 禁 L3 import L4，故
    该端口以**鸭子面**过界（总线只认其中的 `ResultEnvelope`）。

    :param synthesizer / reviewer / catalog: 三个 L4 鸭子端口的覆写口；缺省由
        :func:`_build_l4` 装真实现（`runtime.llm` 出网 / 读本地市场库）。
        **离线关卡**传确定性件，使 CI 不随本机 `.env` 有无而变（同 M1 A4 口径）。
    :param executor: 编排的并行执行器（鸭子类型 `map(fn, seq)`）——**测试可传同线程件
        保确定性**；缺省 `ThreadPoolExecutor`。
    """
    runtime, l2, l3, l4 = _build_m2_pieces(
        root, passphrase, create=create, market_query=market_query,
        sender=sender, transport=transport, llm_env=llm_env, dotenv_path=dotenv_path,
        understander=understander, synthesizer=synthesizer, reviewer=reviewer,
        catalog=catalog, executor=executor, now=now, **l1_kwargs,
    )
    return M2Runtime(
        m1=M1Runtime(runtime=runtime, **_as_kwargs(l2), **_as_kwargs(l3)),
        roster=l4.roster, deliberation=l4.deliberation, examiner=l4.examiner,
        viewer=l4.viewer, analyze=l4.analyze,
    )
