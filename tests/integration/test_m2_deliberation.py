"""`T-INT-003` · M2 集成关卡：多视角决策闭环（MVP）的跨层装配用例。

只测**装配关系与跨层数据流**（单任务行为已由各自套件覆盖，见各任务 备注）：本关卡的
GWT 锚在 [00 §5 反向流](../../docs/技术架构-v2/00-架构总览.md) 的 Deliberation 支——
意图 → 澄清（问模式）→ 确认 → 派发 → 视角并行 → 交叉对照 → 分歧图渲染 → 决策记录写回 L2。

全部离线（出网面由 `rig_m2` 的脚本件替身接管）；真实 LLM 端点在 `tests/live/`（GWT-9）。
"""

from __future__ import annotations

import http.client
import json
from pathlib import Path

import pytest
from rig import ROOT_NAME
from rig_m2 import M2Rig, seeded_m2

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.ui.security import TOKEN_HEADER

#: 现场种子下**有结论**的视角（其余因本地数据面缺该域而 `insufficient-data`，
#: 见 `rig_m2` 的播种口径）——「全员一致」用例只留这几个参与。
_LENSES_WITH_DATA = ("风险视角", "合规视角", "宏观视角", "流动性视角")

#: 合并结论类字段的黑名单（[06 架构级红线](../../docs/技术架构-v2/06-L4-多视角推理.md)、铁律 4）。
_MERGED_VERDICT_FIELDS = (
    "verdict", "recommendation", "advice", "summary", "conclusion_text",
)


@pytest.fixture()
def rig(tmp_path: Path) -> M2Rig:
    return seeded_m2(tmp_path / ROOT_NAME)


def _analyze(rig: M2Rig, text: str = "分析下 sh.600000 是否值得关注", **dispatch_kwargs):
    """走一遍「输入 → 理解 → 澄清 → 确认 → 派发」。"""
    turn = rig.m2.chat.post(text)
    assert turn.needs_confirmation is True
    outcome = rig.m2.chat.confirm_and_dispatch(turn.session_id, **dispatch_kwargs)
    return turn, outcome


# ─────────────────────────────── GWT-1 装配根 ───────────────────────────────

def test_gwt1_m2_root_assembles_l0_to_l4_on_one_store(rig: M2Rig) -> None:
    """`build_m2_runtime` 把 L0→L1→L2→L3 **+ L4** 装到**同一个 `Store`**，各件用真实依赖构造。"""
    m2 = rig.m2
    assert len(m2.m1.runtime.skills.list_all()) > 0, "官方 Pack 应可列出（L1 装配成功）"
    # 单一 Store 属主：L2 图谱与 L1 运行时共用同一个 store；L4 阵容亦落该 store
    assert m2.m1.graph.store is m2.m1.runtime.store
    assert m2.roster._store is m2.m1.runtime.store  # noqa: SLF001（装配断言）
    # 阵容已播种 7 内置视角（06 §1 常设阵容）
    assert len(m2.roster.list_enabled()) == 7
    assert {l.kind for l in m2.roster.list_all()} == {"builtin"}
    # L4 各件接的是 L1/L2 真面，非注入 Fake
    assert m2.deliberation._runner is m2.m1.runtime.runner      # noqa: SLF001
    assert m2.deliberation._reader is m2.m1.reader              # noqa: SLF001
    assert m2.deliberation._writer is m2.m1.writer              # noqa: SLF001
    assert m2.deliberation._roster is m2.roster                 # noqa: SLF001
    # `analyze` 去向已接活，且总线见到的正是组合根装配的编排件（鸭子面）
    assert m2.m1.bus._deliberations is m2.analyze               # noqa: SLF001


# ─────────────────────────────── GWT-2 analyze 去向来路 ───────────────────────────────

def test_gwt2_mode_question_comes_from_the_intent_level_declaration(rig: M2Rig) -> None:
    """`analyze` 无 target Skill，其「模式」问项取自**意图级参数声明**（[05 §3.2]；
    [06 §2.1](../../docs/技术架构-v2/06-L4-多视角推理.md) 的「询问模式」由此成立）。"""
    turn = rig.m2.chat.post("分析下 sh.600000 是否值得关注")
    questions = {q.name: q for q in turn.clarification.questions}
    assert set(questions) == {"mode"}, "analyze 应恰有一条模式问项"
    assert questions["mode"].choices == ("quick", "deep")
    assert questions["mode"].default == "deep"
    # 主题由理解产物流入确认卡取值（澄清侧不重造主题）
    assert turn.confirmation.values["topic"] == "sh.600000 是否值得关注"
    assert turn.confirmation.values["mode"] == "deep", "跳过即取默认值"


def test_gwt2_analyze_route_runs_the_whole_deliberation_chain(rig: M2Rig) -> None:
    """派发 → 视角并行 → 交叉对照 → 视图投影，信封与载荷原样透出（不重包、不改 status）。"""
    turn, outcome = _analyze(rig)
    assert outcome.wired is True and outcome.intent == "analyze"
    assert outcome.envelope.status == "ok", outcome.envelope.reason
    assert outcome.owner is None, "已接入去向不再点名归属任务"

    analysis = outcome.analysis
    assert analysis is not None
    assert analysis.topic == "sh.600000 是否值得关注" and analysis.mode == "deep"
    assert len(analysis.result.opinions) == 7, "深度模式 = 全部启用视角"
    assert len(analysis.result.traces) == len(analysis.result.opinions)
    assert len(analysis.view.rows) == 7, "矩阵行覆盖全部参与视角"
    # 派发链取自对照链（06 §3 的 aggregation 步），不是空链
    assert outcome.trace is not None
    assert [s[0] for s in outcome.trace.replay()] == ["aggregation"]
    assert rig.sender.calls == [], "官方执行器只读注入的本地缓存，不应出网"


def test_gwt2_quick_mode_narrows_the_roster(rig: M2Rig) -> None:
    """澄清回答 `mode=quick` → 只跑快速组合（06 §2.1 的 `FAST_COMBO`）。"""
    turn = rig.m2.chat.post("分析下 sh.600000")
    outcome = rig.m2.chat.confirm_and_dispatch(turn.session_id, answers={"mode": "quick"})
    assert outcome.envelope.status == "ok", outcome.envelope.reason
    names = [rig.m2.roster.get(o.lens_id).name for o in outcome.analysis.result.opinions]
    assert set(names) <= {"机会视角", "风险视角", "基本面视角"}
    assert len(names) == 3


# ─────────────────────────────── GWT-3 阵容可增删 ───────────────────────────────

def test_gwt3_custom_lens_joins_the_roster_and_the_run(rig: M2Rig) -> None:
    """自定义视角经组合根入库、命名过中性化校验，并**真的**出现在后续编排里。"""
    from st_agent.l4.errors import BuiltinLensError, LensValidationError

    # 违规命名（性格标签）→ 拒绝
    with pytest.raises(LensValidationError):
        rig.m2.roster.add_custom(
            name="激进派", description="看涨就买", skill_bundle=(),
            judging_criteria={"natural": "以涨幅为准"},
        )
    # 合法命名 → 入库；空 bundle ⇒ 该视角照常成行、标注数据不足
    lens = rig.m2.roster.add_custom(
        name="自建观察视角", description="按自定准则观察标的",
        skill_bundle=(), judging_criteria={"natural": "以自定准则为准"},
    )
    assert lens.kind == "custom" and rig.m2.roster.has_custom()

    _, outcome = _analyze(rig)
    rows = {row.lens_id: row for row in outcome.analysis.view.rows}
    assert lens.lens_id in rows and rows[lens.lens_id].stance == "insufficient-data"

    # 内置只能停用不可删
    builtin = rig.m2.roster.list_enabled()[0]
    with pytest.raises(BuiltinLensError):
        rig.m2.roster.remove(builtin.lens_id)
    rig.m2.roster.set_enabled(builtin.lens_id, False)
    _, outcome2 = _analyze(rig)
    assert builtin.lens_id not in {o.lens_id for o in outcome2.analysis.result.opinions}


# ─────────────────────────────── GWT-4 证据可回溯 + 追问 ───────────────────────────────

def test_gwt4_evidence_refs_are_traceable_and_ask_back_expands_the_lens(rig: M2Rig) -> None:
    """每视角的证据引用为 §1 ID 串；矩阵行的 `trace_id` 即**追问接口**（06 §5）。"""
    _, outcome = _analyze(rig)
    analysis = outcome.analysis
    with_opinion = [o for o in analysis.result.opinions if o.stance != "insufficient-data"]
    assert with_opinion, "本案应有视角拿到有效数据"
    for opinion in with_opinion:
        assert opinion.evidence_refs, "有结论的视角必须带证据引用"
        assert all(
            ref.startswith(("run_", "mn_", "snap_", "ann_"))
            for ref in opinion.evidence_refs
        ), "证据引用须为 01 §1 的 ID 字符串形态"

    row = next(r for r in analysis.view.rows if r.lens_id == with_opinion[0].lens_id)
    described = rig.m2.chat.lens_trace_of(analysis, row.trace_id)
    assert described.status == "ok", described.reason
    assert described.data.component_type == "trace_timeline"
    # 锚点不存在 → 显式空态，不臆造一条链
    missing = rig.m2.chat.lens_trace_of(analysis, "tr_" + "0" * 20)
    assert missing.status == "empty"


# ─────────────────────────────── GWT-5 只质询不合并 ───────────────────────────────

def test_gwt5_mini_debate_questions_without_merging(rig: M2Rig) -> None:
    """方向冲突对出**结构化**冲突分析；触发与不触发两态都留痕（06 §3–§4、铁律 4）。"""
    _, outcome = _analyze(rig)
    analysis = outcome.analysis
    conflicts = analysis.map.conflicts
    assert len(analysis.map.disagreements) == 2, "本案两对方向冲突"

    triggered = [c for c in conflicts if c.premise_related]
    assert len(triggered) == 1, "共享 skill_bundle 的那对必触发"
    entry = triggered[0]
    assert entry.shared_skills, "前提相关的取值面是共享 Skill"
    assert entry.sides[0].only_refs or entry.sides[1].only_refs, "含双方证据差集"
    assert entry.reviews, "含一轮证据重评估"
    assert all(r.validity == "valid" for r in entry.reviews), "脚本重研判件真的被消费"

    # 未触发者在 notes 里**显式**记明原因，不静默略过（GWT-4）
    assert any("未触发 Mini Debate" in note for note in analysis.map.notes)


def test_gwt5_no_merged_verdict_anywhere_in_the_chain(rig: M2Rig) -> None:
    """全链路任一层对象都**没有**合并结论字段（`hasattr` + `model_dump` 双向钉住）。"""
    _, outcome = _analyze(rig)
    analysis = outcome.analysis
    targets = (analysis, analysis.result, analysis.map, analysis.view)
    for obj in targets:
        for field in _MERGED_VERDICT_FIELDS:
            assert not hasattr(obj, field), f"{type(obj).__name__} 出现合并结论字段 {field!r}"
            assert field not in obj.model_dump(), f"{type(obj).__name__} 的载荷含 {field!r}"


# ─────────────────────────────── GWT-6 回环服务边界 ───────────────────────────────

def _post(running, body: dict, *, token: str | None = None):
    conn = http.client.HTTPConnection(running.host, running.port, timeout=5)
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers[TOKEN_HEADER] = token
    conn.request("POST", "/api/chat", body=json.dumps(body), headers=headers)
    resp = conn.getresponse()
    payload = json.loads(resp.read())
    conn.close()
    return resp.status, payload


def test_gwt6_divergence_map_survives_the_loopback_gate(rig: M2Rig) -> None:
    """分歧图描述件经 `POST /api/chat` 出站点：两视图槽齐、过中性化门（真 HTTP）。"""
    from st_agent.ui.server import serve

    with serve(dev=False, chat=rig.m2.chat) as running:
        status, d = _post(
            running, {"action": "post", "text": "分析下 sh.600000 是否值得关注"},
            token=running.token,
        )
        assert status == 200 and d["needs_confirmation"] is True

        status, d2 = _post(
            running, {"action": "dispatch", "session_id": d["session_id"]},
            token=running.token,
        )
        assert status == 200 and d2["status"] == "ok"
        description = d2["description"]
        assert description is not None and description["status"] == "ok", description
        data = description["data"]
        assert data["component_type"] == "divergence_map"
        assert data["slots"]["matrix"], "矩阵视图槽不得为空"
        assert data["slots"]["network"], "证据网络视图槽不得为空"
        assert data["slots"]["stance_labels"], "立场词表由 L3 下发（前端不携词表）"


# ─────────────────────────────── GWT-7 特殊情形 ───────────────────────────────

def test_gwt7_empty_roster_yields_an_empty_branch(rig: M2Rig) -> None:
    """全部视角停用 → 编排 `empty` + 原因，照常经派发透出（显式空态，不返回残缺卡）。"""
    for lens in rig.m2.roster.list_all():
        rig.m2.roster.set_enabled(lens.lens_id, False)
    _, outcome = _analyze(rig)
    assert outcome.envelope.status == "empty"
    assert outcome.analysis.view is None
    assert rig.m2.chat.describe_divergence_of(None).status == "unavailable"


def test_gwt7_insufficient_data_lens_does_not_block_the_others(rig: M2Rig) -> None:
    """某视角数据不足 → 该视角照常成矩阵行、其余照常出结论（06 §2.3 失败隔离）。"""
    _, outcome = _analyze(rig)
    opinions = outcome.analysis.result.opinions
    insufficient = [o for o in opinions if o.stance == "insufficient-data"]
    assert insufficient, "本案种子下应有视角拿不到数据"
    assert any(o.stance != "insufficient-data" for o in opinions), "其余视角不受牵连"
    for opinion in insufficient:
        assert opinion.key_reasons, "数据不足也须给可呈现的中性说明（01 §3）"
        assert opinion.confidence == "low"


def test_gwt7_unanimous_run_carries_the_groupthink_notice(tmp_path: Path) -> None:
    """全部参与视角均有结论且立场唯一 → 一致提示 + 证据面仍完整（06 §6）。"""
    rig = seeded_m2(tmp_path / ROOT_NAME, stances={})  # 脚本件全给 neutral
    for lens in rig.m2.roster.list_all():
        if lens.name not in _LENSES_WITH_DATA:
            rig.m2.roster.set_enabled(lens.lens_id, False)
    _, outcome = _analyze(rig)
    assert outcome.envelope.status == "ok", outcome.envelope.reason
    view = outcome.analysis.view
    assert view.unanimous is True and view.uniform_stance == "neutral"
    assert view.unanimity_notice, "一致提示不得为空"
    assert view.evidence_network.nodes, "一致时证据面仍完整"


# ─────────────────────────────── GWT-8 决策写回 L2 ───────────────────────────────

def test_gwt8_decision_is_recorded_back_into_l2(rig: M2Rig) -> None:
    """Deliberation 后的用户决策 + 理由 + 采纳/忽略视角落 L2 `history` 节点（06 §2.4）。"""
    _, outcome = _analyze(rig)
    participants = list(outcome.analysis.result.lens_ids)
    recorded = rig.m2.deliberation.record_decision(
        decision="暂不关注，等下次财报", reasoning="风险面证据更充分",
        adopted_lens_ids=(participants[0],), ignored_lens_ids=tuple(participants[1:]),
    )
    assert recorded.status == "ok", recorded.reason
    node_id = recorded.data["memory_node_id"]
    node = rig.m2.m1.graph.get_node(node_id)
    assert node.type == "history" and node.source == "user_stated"
    assert "暂不关注" in node.event and "风险面证据更充分" in node.event


# ─────────────────────────────── 回归：离线用例零出网 ───────────────────────────────

def test_all_offline_zero_egress(rig: M2Rig) -> None:
    """本关卡全部离线用例不触网：跑完一轮多视角闭环后发包探针仍为空（GWT-9 的反面）。"""
    _analyze(rig)
    assert rig.sender.calls == []
