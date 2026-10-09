"""dev 面的示例内容：六态信封 + UI 描述。

用途只有一个：在浏览器里把**六个态各自的渲染**与**组件面的各条路径**（正常 /
未实现类型降级 / 中性校验阻断）逐条走通——M1 骨架叶不接真实数据（任务 `T-UI-001.2`
假设 `A5`、`T-UI-001.3` 同口径）。信封一律用 [01 §5] 的**合法工厂**构造、描述一律经
[01 §12] 的 `UiDescription` 构造，故它们同时也是契约形状的活样本；本模块不进发布构建。

**L3 六型经真描述件产出**（`T-L3-004.1` / `.2` / `T-L3-006` / `T-L4-004.2`）：`trace_timeline` /
`context_card` / `config_draft_card` / `conflict_adjudication_card` / `permission_approval_card` /
`divergence_map` 的走查样本由 [`l3.render`][r] 的六个描述件从**真视图数据**生成，而不是在这里手抄
一份槽结构——手抄的样本会在描述件改动后静默漂移，走查也就查不出东西。分歧图的两条走查分支更往前走了一段：
视图由**真** L4 链路（`CrossExaminer` 对照 → `DivergenceViewer` 投影）产出，故样本与线上同源。
窗口返回 `None` 的样本即不出现在页面上（不摆假样本）。

[r]: ../../l3/render/__init__.py
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from st_agent.contracts.capability_types import LensOpinion
from st_agent.contracts.identifiers import TraceId
from st_agent.contracts.registry_types import PanelField
from st_agent.contracts.result_envelope import EvidenceRef, ResultEnvelope
from st_agent.contracts.trace import ConclusionRef, Trace, TraceStep, digest_of
from st_agent.contracts.ui_description import UiDescription, new_description_id
from st_agent.l2.memory.reader import SliceQuery
from st_agent.ui.tables import Column, table_slots, table_text_kinds
from st_agent.l4.crosscheck import CrossExaminer
from st_agent.l4.deliberation import DeliberationResult
from st_agent.l4.divergence_view import DivergenceViewer
from st_agent.l4.lens import JudgingCriteria, Lens
from st_agent.l3.config.draft import ConfigDraft, OpenQuestion
from st_agent.l3.config.handling import PanelView
from st_agent.l3.config.registry import ChannelParam
from st_agent.l3.approval.panel import (
    ApprovalRequest,
    CapabilityApprovalPanel,
)
from st_agent.contracts.permissions import PermissionApproval
from st_agent.l3.conflict.adjudication import (
    ACTION_LABELS,
    ADJUDICATION_HEADER,
    ADJUDICATION_QUESTION,
    AdjudicationAction,
    ConflictAdjudication,
    ConflictSide,
)
from st_agent.l3.home.card import (
    CARD_TITLE,
    HOME_GREETING,
    HOT_TOPICS_PRODUCER,
    HOT_TOPICS_REASON,
    PROFILE_TAG_LIMIT,
    SECTION_TITLES,
    UNREAD_ALERTS_PRODUCER,
    UNREAD_ALERTS_REASON,
    CardSection,
    CardTag,
    CardTarget,
    ContextCard,
)
from st_agent.l3.render import (
    describe_adjudication,
    describe_approval,
    describe_context_card,
    describe_divergence_map,
    describe_draft,
    describe_trace,
)

__all__ = ["DESCRIPTION_KINDS", "SAMPLE_STATUSES", "sample_description", "sample_envelope"]

_NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone(timedelta(hours=8)))

SAMPLE_STATUSES: tuple[str, ...] = (
    "ok",
    "empty",
    "unavailable",
    "dependency_failed",
    "failed",
    "validation_failed",
)
"""六个状态（与 [01 §5] 的枚举同序）。"""


def _samples() -> dict[str, ResultEnvelope]:
    evidence = (EvidenceRef(kind="dataset_snapshot_id", ref="ds_sample0000000000000"),)
    return {
        "ok": ResultEnvelope.ok(
            {"summary": "示例载荷（骨架叶不接真实数据）"}, as_of=_NOW, evidence_refs=evidence
        ),
        "empty": ResultEnvelope.empty(
            "今日无满足条件的记录", as_of=_NOW, evidence_refs=evidence
        ),
        "unavailable": ResultEnvelope.unavailable(
            "数据源暂不可用", last_updated_at=_NOW - timedelta(hours=6), as_of=_NOW
        ),
        "dependency_failed": ResultEnvelope.dependency_failed(
            "上游依赖失败", log_ref="trace/sample-dependency"
        ),
        "failed": ResultEnvelope.failed("执行失败", log_ref="trace/sample-failure"),
        "validation_failed": ResultEnvelope.validation_failed("参数校验失败：取值超出声明范围"),
    }


def sample_envelope(status: str) -> ResultEnvelope | None:
    """按状态名取示例信封；未知状态名返回 ``None``（调用方回 400 / 404）。"""
    return _samples().get(status)


# ── UI 描述样本（01 §12） ─────────────────────────────────────────────────────

DESCRIPTION_KINDS: tuple[str, ...] = (
    "report_card",
    "table",
    "trace_timeline",
    "context_card",
    "config_draft_card",
    "config_draft_panel",
    "conflict_adjudication_card",
    "permission_approval_card",
    "divergence_map",
    "divergence_map_unanimous",
    "extra-slot",
    "reserved",
    "generated-violation",
    "data-violation",
)
"""组件面的走查路径（前十条正常渲染——含分歧图的两条分支：有冲突 / 全员一致；
第十一条走「未识别槽」降级、第十二条走未实现类型降级、后两条验证中性分栏）。"""


def _descriptions() -> dict[str, UiDescription]:
    return {
        **_handwritten(),
        **_from_l3(),
        "extra-slot": _extra_slot(),
    }


def _extra_slot() -> UiDescription:
    """一个**多出一个未识别槽**的描述——走查「显式降级、不猜测、不静默丢弃」（01 §12）。

    形态取自真描述件，只额外挂一个本版本渲染件不认识的槽（模拟产出方先行）。
    """
    description = describe_trace(_trace()).data
    return description.model_copy(update={
        "slots": {**description.slots, "future_field": {"hint": "本版本未实现该槽"}},
        "text_kinds": {**description.text_kinds, "future_field": "data"},
    })


def _handwritten() -> dict[str, UiDescription]:
    """通用件与中性分栏的样本（不依赖任何 L3 模型）。"""
    return {
        "report_card": UiDescription(
            description_id=new_description_id(),
            component_type="report_card",
            title="示例报告卡",
            slots={
                "sections": [
                    {"title": "概要", "lines": ["价格走势平稳", "成交温和"]},
                    {"title": "待观察", "lines": ["披露窗口临近"]},
                ]
            },
            text_kinds={"sections": "generated"},
            as_of=_NOW,
        ),
        "table": UiDescription(
            description_id=new_description_id(),
            component_type="table",
            title="示例表格",
            slots=table_slots(
                (
                    Column("code", "代码"),
                    Column("close", "收盘", kind="number"),
                    Column("pct_chg", "涨跌幅", kind="direction"),
                ),
                [
                    {"code": "sh.600000", "close": 7.53, "pct_chg": 1.24},
                    {"code": "sz.000001", "close": 11.20, "pct_chg": -0.86},
                    {"code": "sh.601988", "close": 4.05, "pct_chg": 0.0},
                    # 无行情：走「无数据」中性呈现，**不**冒充 0（也**不**读作「平」）
                    {"code": "sz.300750", "close": None, "pct_chg": None},
                ],
            ),
            # 列头是**生成文案**（过 §6）；行内容是**数据展示**（原样呈现，[D-053]）
            text_kinds=table_text_kinds(),
            as_of=_NOW,
        ),
        # 已登记、本期未实现 → 渲染面**显式降级**（01 §12），不猜测
        "reserved": UiDescription(
            description_id=new_description_id(),
            component_type="heatmap",
            title="示例热力图（本期未实现）",
            slots={"cells": [{"code": "sh.600000", "value": 0.12}]},
            text_kinds={"cells": "data"},
            as_of=_NOW,
        ),
        # 生成文案命中第一人称 → **阻断渲染**（回 validation_failed）
        "generated-violation": UiDescription(
            description_id=new_description_id(),
            component_type="report_card",
            title="我认为这只标的值得关注",
            slots={"sections": [{"title": "结论", "lines": ["建议继续观察"]}]},
            text_kinds={"sections": "generated"},
        ),
        # 同样的措辞出现在 data 槽（用户原话）→ **放行**（不整串复检，D-053）
        "data-violation": UiDescription(
            description_id=new_description_id(),
            component_type="report_card",
            title="对话历史回显",
            slots={"sections": [{"title": "原话", "lines": ["我认为这只标的值得关注"]}]},
            text_kinds={"sections": "data"},
        ),
    }


# ── L3 五型的走查样本（经真描述件产出） ────────────────────────────────────────


def _from_l3() -> dict[str, UiDescription]:
    """L3 六型：真视图数据 → 真描述件 → 描述（**不手抄槽结构**）。"""
    samples: dict[str, UiDescription] = {}
    for kind, envelope in (
        ("trace_timeline", describe_trace(_trace())),
        ("context_card", describe_context_card(_context_card())),
        ("config_draft_card", describe_draft(_draft())),
        ("config_draft_panel", describe_draft(_panel_view())),
        ("conflict_adjudication_card", describe_adjudication(_adjudication())),
        ("permission_approval_card", describe_approval(_approval_view())),
        ("divergence_map", describe_divergence_map(_divergence_view(unanimous=False))),
        ("divergence_map_unanimous", describe_divergence_map(_divergence_view(unanimous=True))),
    ):
        if envelope.status == "ok":
            samples[kind] = envelope.data
    return samples


# ── 分歧图的两条走查分支（06 §5–§6）：真 L4 投影 → 真 L3 描述件 ─────────────────


_SAMPLE_TOPIC = "是否关注 sh.600000"
_RUN_A = "run_" + "a" * 20
_RUN_B = "run_" + "b" * 20
_ANN_C = "ann_" + "c" * 20
_SNAP_D = "snap_" + "d" * 20


class _StubRoster:
    """dev 面的假阵容：只按 id 回固定视角（走查用，不进发布构建）。"""

    def __init__(self, lenses: tuple[Lens, ...]) -> None:
        self._by_id = {lens.lens_id: lens for lens in lenses}

    def get(self, lens_id: str) -> Lens:
        return self._by_id[lens_id]


def _sample_lens(name: str, bundle: tuple[str, ...]) -> Lens:
    return Lens(
        lens_id="lens_" + name.encode("utf-8").hex()[:16],
        name=name, description=f"关注{name}维度", skill_bundle=bundle,
        judging_criteria=JudgingCriteria(natural="以中性指标为准"),
        kind="builtin", enabled=True,
    )


def _opinion(lens: Lens, stance: str, refs: tuple[str, ...], index: int) -> LensOpinion:
    return LensOpinion(
        lens_id=lens.lens_id, stance=stance,
        key_reasons=(f"视角「{lens.name}」基于 {len(refs)} 条有效证据形成观点",),
        evidence_refs=refs, confidence="medium",
        skills_triggered=(), trace_id=f"tr_{index:020d}",
    )


def _divergence_view(*, unanimous: bool):
    """走查视图：`unanimous=False` 出冲突（含 Mini Debate 段），`True` 出全员一致分支。

    经**真**链路产出——[`CrossExaminer`] 对照 + [`DivergenceViewer`] 投影，故样本与
    线上同源，描述件改动会立刻反映到走查页（手抄样本做不到这点）。
    """
    opp = _sample_lens("机会视角", ("sk_opportunity_mine_v1.0",))
    sentiment = _sample_lens("情绪视角", ("sk_sentiment_flow_analysis_v1.0",))
    risk = _sample_lens("风险视角", ("sk_risk_alert_v1.0",))
    fundamental = _sample_lens("基本面视角", ("sk_fundamental_screening_v1.0",))

    if unanimous:  # 全员同向 → 06 §6「罕见的高度一致」提示；证据仍完整
        lenses = (opp, sentiment, fundamental)
        opinions = (
            _opinion(opp, "positive", (_RUN_A, _ANN_C), 1),
            _opinion(sentiment, "positive", (_ANN_C,), 2),
            _opinion(fundamental, "positive", (_ANN_C,), 3),
        )
    else:  # 机会/情绪同向成一致点，风险反向成分歧点（共享证据 → 触发 Mini Debate）
        lenses = (opp, sentiment, risk)
        opinions = (
            _opinion(opp, "positive", (_RUN_A, _ANN_C), 1),
            _opinion(sentiment, "positive", (_ANN_C,), 2),
            _opinion(risk, "negative", (_RUN_A, _SNAP_D, _RUN_B), 3),
        )

    roster = _StubRoster(lenses)
    result = DeliberationResult(
        topic=_SAMPLE_TOPIC, mode="deep",
        envelope=ResultEnvelope.ok([l.lens_id for l in lenses], as_of=_NOW),
        opinions=opinions, lens_ids=tuple(l.lens_id for l in lenses),
    )
    examined = CrossExaminer(roster=roster, now=lambda: _NOW).cross_examine(result)
    return DivergenceViewer(roster=roster).build(result, examined)


class _StubPermissionBook:
    """dev 面的假批准账本：只回显固定批准态，不触盘（走查用，不进发布构建）。"""

    def __init__(self, approvals: dict[str, tuple[PermissionApproval, ...]]) -> None:
        self._approvals = approvals

    def declare(self, key, permissions):  # noqa: ARG002 - 走查面只读回显
        return self._approvals.get(key, ())

    def approvals(self, key):
        return self._approvals.get(key, ())

    def approve(self, key, permission):  # noqa: ARG002
        return self._approvals[key][0]

    def reject(self, key, permission):  # noqa: ARG002
        return self._approvals[key][0]


def _approval_view():
    """审批面视图：一条已批准、一条仍待批（不合并成一句话，01 §10）。"""
    approvals = {
        "sk_demo_watch": (
            PermissionApproval(
                permission="local_read:<data/cache/**>",
                decision="approved",
                decided_at=_NOW,
            ),
            PermissionApproval(
                permission="net_access:<*.baostock.com>",
                decision="pending",
            ),
        ),
    }
    panel = CapabilityApprovalPanel(book=_StubPermissionBook(approvals))
    envelope = panel.open(
        ApprovalRequest(
            key="sk_demo_watch",
            source="skill",
            permissions=tuple(a.permission for a in approvals["sk_demo_watch"]),
        )
    )
    return envelope.data


def _trace() -> Trace:
    trace = Trace(trace_id=TraceId.of("tr_" + "2" * 20))
    trace = trace.append_step(TraceStep(
        step_type="data_fetch",
        ref="ds_sample0000000000000",
        input_digest=digest_of({"codes": ["sh.600000"]}),
        output_digest=digest_of({"rows": 2}),
        duration_ms=42,
        timestamp=_NOW - timedelta(minutes=3),
    ))
    trace = trace.append_step(TraceStep(
        step_type="memory_read",
        ref="mem_0000000000000001",
        input_digest=digest_of({"slice": "attention"}),
        output_digest=digest_of({"holdings": 1}),
        duration_ms=7,
        timestamp=_NOW - timedelta(minutes=2),
        degraded=True,
        note="降级：记忆切片经本地缓存命中",
    ))
    trace = trace.append_step(TraceStep(
        step_type="skill_run",
        ref="sk_run_0000000000000001",
        input_digest=digest_of({"keywords": "回购"}),
        output_digest=digest_of({"hits": 2}),
        duration_ms=1180,
        timestamp=_NOW - timedelta(minutes=1),
    ))
    return trace.conclude(ConclusionRef(kind="message", ref="msg_0000000000000001"))


def _context_card() -> ContextCard:
    def section(key: str, **fields) -> CardSection:
        return CardSection(key=key, title=SECTION_TITLES[key], **fields)

    return ContextCard(
        envelope=ResultEnvelope.ok([], as_of=_NOW),
        title=CARD_TITLE,
        greeting=HOME_GREETING,
        is_empty=False,
        sections=(
            section(
                "profile", state="ok",
                tags=(CardTag(label="银行", kind="sector",
                              source_node_id="mem_0000000000000001"),),
                total=1, limit=PROFILE_TAG_LIMIT,
            ),
            section("holdings", state="ok", items=("sh.600000",)),
            section("watchlist", state="empty", reason="记忆中没有关注池信息"),
            section("thesis", state="ok", items=("sh.600000：看好反转",)),
            section("unread_alerts", state="unavailable",
                    reason=UNREAD_ALERTS_REASON, producer=UNREAD_ALERTS_PRODUCER),
            section("hot_topics", state="unavailable",
                    reason=HOT_TOPICS_REASON, producer=HOT_TOPICS_PRODUCER),
        ),
        graph_target=CardTarget(
            kind="memory_graph",
            query=SliceQuery(task_type="chat", topic="", token_budget=None, view="graph"),
        ),
        as_of=_NOW,
    )


def _draft() -> ConfigDraft:
    return ConfigDraft(
        target="stock-watch",
        parameter_draft={"keywords": "回购"},
        understanding_summary="标的选择：sh.600000（已确认）\n条件：关键词（已给出）",
        open_questions=(
            OpenQuestion(param="window", prompt="参数 window：回看窗口",
                         default=30, source="missing"),
            OpenQuestion(param="interval", prompt="参数 interval：检查间隔",
                         default="每日", source="defaulted"),
        ),
    )


def _panel_view() -> PanelView:
    return PanelView(
        target="stock-watch",
        params=(
            ChannelParam(
                name="keywords", origin="declared", chat_text="参数 keywords：关注关键词",
                panel_field=PanelField(widget="text", label="keywords", help_text="关注关键词"),
            ),
            ChannelParam(
                name="window", origin="registry", chat_text="参数 window：回看窗口（天）",
                panel_field=PanelField(widget="number", label="window", help_text="回看窗口（天）"),
                default=30, scope="skill", config_id="cfg_sample000000000000",
            ),
        ),
        values={"keywords": "回购", "window": 30},
    )


def _adjudication() -> ConflictAdjudication:
    return ConflictAdjudication(
        conflict_id="cf_00000000000000001",
        kind="value_conflict",
        dimension="thesis",
        trace_id="tr_" + "2" * 20,
        proposed=ConflictSide(node_id=None, dimension="thesis",
                              fields={"subject": "sh.600000", "view": "看好反转"}),
        existing=ConflictSide(node_id="mem_0000000000000002", dimension="thesis",
                              fields={"subject": "sh.600000", "view": "看空"}),
        header=ADJUDICATION_HEADER,
        question=ADJUDICATION_QUESTION,
        actions=(
            AdjudicationAction(decision="accept", label=ACTION_LABELS["accept"]),
            AdjudicationAction(decision="reject", label=ACTION_LABELS["reject"]),
        ),
        directions=(),
        stance_undecided=True,
    )


def sample_description(kind: str) -> UiDescription | None:
    """按走查路径名取示例描述；未知名称返回 ``None``（调用方回 404）。"""
    return _descriptions().get(kind)
