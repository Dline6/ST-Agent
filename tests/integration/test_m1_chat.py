"""T-INT-002 · M1 集成关卡「首次可对话」的端到端冒烟（离线，进默认 pytest）。

只测**装配关系与跨层数据流**（[00 §5 反向流]），不重复各任务单测：
- GWT-1 生产组合根可用（L0/L1/L2/L3 装到同一个 `Store`，L3 编排器用真实依赖构造）
- GWT-2 反向流端到端（query 去向经**注入的 L1 SkillRunner** 跑通一次官方 Skill + Trace 可展开）
- GWT-3 配置草稿去向（configure 生成草稿 + 双通道视图；统一门面已交付（T-L1-012）故落值真生效）
- GWT-4 冲突裁决联动（L2 冲突 → `MemoryConflictDetected` → L3 裁决卡 → 落地，进程内直递，见 A3）
- GWT-5 memory_op 偏好写入支（[05 §9] / [D-062] 显式指派给本关卡的欠账，`user_stated` 直写）
- GWT-6 三态渲染送达回环对话面（`POST /api/chat` 真 HTTP，未注入门面 fail-closed）
- GWT-8 组合根在 L0 **新默认**（免口令明文根 + 审计默认关）下端到端成立（`T-L0-018.1`/`.2` 跨层效果）

真实网络 / 真端点（GWT-7）不在本文件——由 `-m live` 子集兜。
"""

from __future__ import annotations

import http.client
import json
from pathlib import Path

import pytest
from rig import ROOT_NAME
from rig_m1 import M1Rig, seeded_m1, seeded_m1_plaintext

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.storage import STORE_MARKER
from st_agent.l2.memory import checked_node, new_node_id
from st_agent.l3.dispatch import ANALYZE_ABSENT_REASON
from st_agent.ui.security import TOKEN_HEADER


@pytest.fixture()
def rig(tmp_path: Path) -> M1Rig:
    return seeded_m1(tmp_path / ROOT_NAME)


# ─────────────────────────────── GWT-1 装配根 ───────────────────────────────

def test_gwt1_production_root_assembles_all_layers(rig: M1Rig) -> None:
    """`build_m1_runtime` 把 L0→L1→L2→L3 装到**同一个 `Store`**，L3 用真实依赖构造。"""
    m1 = rig.m1
    assert len(m1.runtime.skills.list_all()) > 0, "官方 Pack 应可列出（L1 装配成功）"
    # 单一 Store 属主：L2 图谱与 L1 运行时共用同一个 store
    assert m1.graph.store is m1.runtime.store
    # L3 编排器接的是 L1 真面（任务 A1），非注入 Fake
    assert m1.bus._runner is m1.runtime.runner          # noqa: SLF001（装配断言）
    assert m1.intent._descriptors is m1.runtime.skills  # noqa: SLF001
    assert m1.adjudicator._queue is m1.queue            # noqa: SLF001（裁决面接 L2 队列）


# ─────────────────────────────── GWT-2 反向流 ───────────────────────────────

def test_gwt2_reverse_flow_query_dispatches_official_skill(rig: M1Rig) -> None:
    """输入一句话 → 理解 → 澄清 → 确认 → 派发 `query` 跑通一次官方 Skill、信封原样透出、Trace 可展开。"""
    turn = rig.m1.chat.post("看下 sh.600000 有没有退市风险")
    assert turn.needs_confirmation is True
    assert turn.confirmation.target == "sk_delisting_risk_scan_v1.0"

    outcome = rig.m1.chat.confirm_and_dispatch(turn.session_id)
    assert outcome.wired is True and outcome.intent == "query"
    assert outcome.envelope.status == "ok", outcome.envelope.reason
    # 信封原样透出、链含本次 skill_run 步骤（官方取数经注入面，零出网）
    assert outcome.trace is not None and len(outcome.trace.steps) >= 1
    assert rig.sender.calls == [], "官方执行器只读注入的本地缓存，不应出网"

    described = rig.m1.chat.describe_trace_of(outcome.trace)
    assert described.status == "ok"
    assert described.data.component_type == "trace_timeline"


# ─────────────────────────────── GWT-3 配置草稿 ───────────────────────────────

def test_gwt3_configure_generates_draft_and_dual_channel(rig: M1Rig) -> None:
    """`configure` 经注入的生成面产出草稿 + 双通道视图；统一门面已交付（T-L1-012）故落值真生效。"""
    turn = rig.m1.chat.post("把盯盘阈值配置一下")
    outcome = rig.m1.chat.confirm_and_dispatch(turn.session_id)
    assert outcome.intent == "configure" and outcome.wired
    assert outcome.envelope.status == "ok", outcome.envelope.reason
    assert outcome.draft is not None  # ConfigDraft

    from st_agent.l3.config import dual_channel_view

    view = dual_channel_view(
        "sk_risk_alert_v1.0", descriptors=rig.m1.runtime.skills,
        registry=rig.m1.runtime.config_registry,
    )
    assert view.status == "ok"
    # 统一配置注册表门面已交付（T-L1-012 / D-067）→ 已声明参数取到**真登记项**，
    # 两通道同源由「声明面回落」升为「registry」（E1 已闭）
    assert view.data.params and all(p.origin == "registry" for p in view.data.params)

    # 草稿落值经门面生效并产生真 `change_id`（此前因无门面恒 fail-closed）
    accepted = rig.m1.handling.accept(outcome.draft)
    assert accepted.status == "ok", accepted.reason
    assert all(r.change_id.startswith("chg_") for r in accepted.data.records)


# ─────────────────────────────── GWT-4 冲突裁决联动 ───────────────────────────────

def test_gwt4_conflict_event_to_card_to_resolution(rig: M1Rig) -> None:
    """`inferred` 写入命中既有节点 → 事件 → 裁决卡 → 用户 accept 经 L2 落地（进程内直递，A3）。"""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    existing = rig.m1.writer.add_node(checked_node(
        type="thesis", memory_node_id=new_node_id(), confidence=0.6,
        source="user_stated", privacy_level="private",
        subject="sh.600000", subject_kind="stock", view="看多",
        created_at=now, updated_at=now,
    ))
    proposed = checked_node(
        type="thesis", memory_node_id=new_node_id(), confidence=0.5,
        source="inferred", provenance={"trace_id": "tr_" + "0" * 20},
        privacy_level="private", subject="sh.600000", subject_kind="stock",
        view="看空", created_at=now, updated_at=now,
    )
    proposal = rig.m1.queue.propose(proposed, trace_id="tr_" + "0" * 20)
    assert proposal is not None and proposal.status == "pending"
    assert proposal.target_node_id == existing.memory_node_id

    # 无事件总线——关卡在进程内把 event_for 的产物直递 from_event（A3）
    event = rig.m1.queue.event_for(proposal)
    assert event.event == "MemoryConflictDetected"
    card_envelope = rig.m1.adjudicator.from_event(event)
    assert card_envelope.status == "ok"
    assert card_envelope.data.conflict_id == proposal.conflict_id

    resolved = rig.m1.adjudicator.submit(
        card_envelope.data, decision="accept",
    )
    assert resolved.status == "ok", resolved.reason
    assert resolved.data.node is not None
    # 已裁决项不得重复裁决
    again = rig.m1.adjudicator.submit(card_envelope.data, decision="accept")
    assert again.status == "validation_failed"


# ─────────────────────── GWT-5 memory_op 偏好写入支（本关卡承接的欠账） ───────────────────────

def test_gwt5_memory_op_preference_write_lands_l2(rig: M1Rig) -> None:
    """对话显式表达偏好 → `user_stated` 直写 L2（[04 §3.2]；本关卡承接 [05 §9]/[D-062]）。"""
    result = rig.m1.chat.write_preference({
        "type": "identity", "risk_preference": "稳健", "confidence": 0.9,
    })
    assert result.status == "ok", result.reason
    node_id = result.data["memory_node_id"]
    node = rig.m1.graph.get_node(node_id)
    assert node.source == "user_stated"
    assert node.risk_preference == "稳健"
    # 直写不过白名单门、不进冲突队列——写后即图谱成员
    assert node_id in {n.memory_node_id for n in rig.m1.graph.nodes()}

    # 非法节点（缺专属字段）→ 显式失败，不静默
    bad = rig.m1.chat.write_preference({"type": "identity", "confidence": 0.9})
    assert bad.status == "validation_failed"


# ─────────────────────── GWT-6 三态经回环对话面送达真 HTTP ───────────────────────

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


def test_gwt6_chat_endpoint_serves_reverse_flow(rig: M1Rig) -> None:
    """`POST /api/chat` 对已装配运行时走通一段反向流，出站点过中性化门（真 HTTP）。"""
    from st_agent.ui.server import serve

    with serve(dev=False, chat=rig.m1.chat) as running:
        # 无令牌 → 拒绝（/api/* 必带令牌，同 GET）
        status, _ = _post(running, {"action": "post", "text": "查退市风险"})
        assert status == 401

        status, d = _post(
            running, {"action": "post", "text": "查 sh.600000 退市风险"},
            token=running.token,
        )
        assert status == 200 and d["status"] == "ok"
        assert d["needs_confirmation"] is True and d["session_id"]
        # 六态渲染语义随信封下发
        assert d["render"]["presentation"]

        status, d2 = _post(
            running, {"action": "dispatch", "session_id": d["session_id"]},
            token=running.token,
        )
        assert status == 200 and d2["status"] == "ok"
        assert d2["description"] is not None
        assert d2["description"]["status"] == "ok"
        assert d2["description"]["data"]["component_type"] == "trace_timeline"


def test_gwt6_analyze_route_is_fail_closed_without_the_l4_port(rig: M1Rig) -> None:
    """`analyze` 去向在 M1 组合根（未注入 L4 编排端口）下如实 `unavailable`，不伪造。

    2026-10-04 随 [`T-INT-003`](../项目管理/tasks/T-INT-003-M2集成关卡多视角决策闭环.md)（M2 关卡）
    改实：该去向**已接入**（原登记 `owner="T-L4-002"` 是陈旧指针，随接线一并订正），
    故不再是「未接入 + 点名归属」，而是「已接入、但 M1 组合根未注入端口」——
    `owner` 为 `None`、原因点名缺失的编排面。真链路由 M2 关卡用例覆盖。
    """
    turn = rig.m1.chat.post("分析下 sh.600000 的趋势")
    assert turn.needs_confirmation is True
    outcome = rig.m1.chat.confirm_and_dispatch(turn.session_id)
    assert outcome.envelope.status == "unavailable"
    assert outcome.owner is None
    assert ANALYZE_ABSENT_REASON in outcome.envelope.reason


def test_gwt6_chat_endpoint_without_facade_is_fail_closed(tmp_path: Path) -> None:
    """未注入门面 → `/api/chat` 回 `unavailable`，不静默、不崩（装配归组合根）。"""
    from st_agent.ui.server import serve

    with serve(dev=False) as running:  # chat=None
        status, d = _post(
            running, {"action": "post", "text": "查退市风险"}, token=running.token,
        )
        assert status == 200
        assert d["status"] == "unavailable"


# ─────────────── GWT-8 组合根在 L0 新默认（免口令明文根 + 审计默认关）下成立 ───────────────

def test_gwt8_production_root_over_default_plaintext_store(tmp_path: Path) -> None:
    """L0 新默认（`T-L0-018.1`：明文根免口令；`T-L0-018.2`：审计默认关）下组合根端到端成立。

    这是四个新叶子（`T-L0-017.1`/`.2`、`T-L0-018.1`/`.2`）加入 M1 后**跨层效果**的装配面：
    L0 单任务用例证明「`Store` 明文可读写」，但证明不了「生产组合根 + L2/L3 叠在明文根上」
    ——本用例补这一层（关卡的「装配关系与跨层数据流」职责，见 [`T-INT-002`] 目标节）。
    """
    rig = seeded_m1_plaintext(tmp_path / ROOT_NAME)
    # 审计默认关（[02 §6] / [D-073]）：默认装配不产生逐次出网留痕
    assert rig.m1.runtime.gateway.audit_enabled is False

    # 反向流在明文根上照常走通（query 去向经注入的 L1 SkillRunner）
    turn = rig.m1.chat.post("看下 sh.600000 有没有退市风险")
    assert turn.needs_confirmation is True
    outcome = rig.m1.chat.confirm_and_dispatch(turn.session_id)
    assert outcome.wired and outcome.intent == "query"
    assert outcome.envelope.status == "ok", outcome.envelope.reason

    # 免口令根：明文格式标记在盘；默认关审计 → 无 llm_call 审计、零发包
    assert (rig.root / STORE_MARKER).is_file()
    assert rig.m1.runtime.gateway.query(kind="llm_call") == ()
    assert rig.sender.calls == [], "官方执行器只读注入的本地缓存，不应出网"

    # 偏好写入（`user_stated` 直写 L2）在明文根上同样落库
    pref = rig.m1.chat.write_preference(
        {"type": "identity", "risk_preference": "稳健", "confidence": 0.9}
    )
    assert pref.status == "ok", pref.reason
    node = rig.m1.graph.get_node(pref.data["memory_node_id"])
    assert node.risk_preference == "稳健"


# ─────────────────────── 回归：离线用例零出网 ───────────────────────

def test_all_offline_zero_egress(rig: M1Rig) -> None:
    """本关卡离线用例全程不触网：GWT-2..6 跑完后发包探针仍为空。"""
    turn = rig.m1.chat.post("看下 sh.600000 有没有退市风险")
    rig.m1.chat.confirm_and_dispatch(turn.session_id)
    assert rig.sender.calls == []
