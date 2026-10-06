"""训练对话协议（[`training.py`](../../src/st_agent/l6/training.py)）的验收用例。

对齐任务文件 [`T-L6-002.1`](../项目管理/tasks/T-L6-002.1-训练对话协议.md) 的 GWT-1..6——
概念性理解经注入端口（缺省 fail-closed）、确认门、确认后落三件（记忆 `pattern` 节点 /
提案候选 / 回访留痕）、各面缺省的显式降级、`train` 去向的接线与不伪造。
"""

from __future__ import annotations

import json

import pytest

from l6_helpers import (
    CORRECTION,
    NOW,
    PASS,
    PATTERN_TEXT,
    RESTATEMENT,
    SUGGESTION_REASON,
    train_confirmation,
    understander,
)
from st_agent.l0.storage import Store
from st_agent.l3.dispatch import (
    ROUTE_BY_INTENT,
    TRAIN_ABSENT_REASON,
    DispatchBus,
)
from st_agent.l3.intent.protocol import INTENT_PARAM_SPECS
from st_agent.l6 import (
    MEMORY_ABSENT_NOTE,
    NO_SUGGESTION_NOTE,
    UNDERSTANDER_ABSENT_REASON,
    FeedbackPool,
    TrainingError,
    TrainingProtocol,
    TrainingSession,
    build_l6,
)

SUGGESTION = {"config_id": "fatigue.ignore-threshold", "current": 5, "suggested": 4,
              "reason": SUGGESTION_REASON}


def protocol(tmp_path, *, understand=None, writer=None, pool=None, **kw):
    """带真 ``Store`` 的训练对话面（理解端口 / 记忆写入面按需注入）。"""
    store = Store.create(tmp_path / "root", PASS)
    return TrainingProtocol(
        store=store, understander=understand, memory_writer=writer, pool=pool,
        now=lambda: NOW, **kw,
    ), store


class TestGwt1Understanding:
    """GWT-1：经注入端口做概念性理解；未注入 / 给不出复述 ⇒ fail-closed。"""

    def test_understands_through_the_injected_port(self, tmp_path):
        face, _ = protocol(tmp_path, understand=understander(suggestion=SUGGESTION))
        envelope = face.understand(correction=CORRECTION)
        assert envelope.status == "ok"
        session = envelope.data
        assert isinstance(session, TrainingSession)
        assert session.correction == CORRECTION
        assert session.restatement == RESTATEMENT
        assert session.confirmed is False          # 确认是**后一步**的事
        assert session.suggestion is not None
        assert session.suggestion.config_id == "fatigue.ignore-threshold"

    def test_user_wording_is_data_not_generated_text(self, tmp_path):
        """用户原话带第一人称照常承载（[D-053] 数据展示不过 §6）；生成文案另字段。"""
        face, _ = protocol(tmp_path, understand=understander())
        session = face.understand(correction="我觉得这条不对，因为我不认同").data
        assert "我觉得" in session.correction
        assert "我觉得" not in session.restatement

    def test_generated_restatement_must_pass_neutrality(self, tmp_path):
        face, _ = protocol(tmp_path, understand=understander(restatement="我认为这家公司很好"))
        with pytest.raises(TrainingError, match="中性化"):
            face.understand(correction=CORRECTION)

    def test_missing_understander_fails_closed(self, tmp_path):
        face, _ = protocol(tmp_path)
        envelope = face.understand(correction=CORRECTION)
        assert envelope.status == "unavailable"
        assert UNDERSTANDER_ABSENT_REASON in envelope.reason
        assert envelope.last_updated_at == NOW

    def test_port_giving_nothing_yields_unavailable(self, tmp_path):
        face, _ = protocol(tmp_path, understand=understander(raw=None))
        envelope = face.understand(correction=CORRECTION)
        assert envelope.status == "unavailable"
        assert "未能给出复述" in envelope.reason

    def test_illegal_port_payload_fails_explicitly(self, tmp_path):
        face, _ = protocol(tmp_path, understand=understander(raw="不是结构"))
        with pytest.raises(TrainingError, match="非法结构"):
            face.understand(correction=CORRECTION)

    def test_incomplete_draft_fails_explicitly(self, tmp_path):
        face, _ = protocol(tmp_path, understand=understander(raw={"restatement": RESTATEMENT}))
        with pytest.raises(TrainingError, match="不合 08 §3 形态"):
            face.understand(correction=CORRECTION)

    def test_blank_correction_is_rejected(self, tmp_path):
        face, _ = protocol(tmp_path, understand=understander())
        with pytest.raises(TrainingError, match="修正原话"):
            face.understand(correction="   ")

    def test_feedback_id_pulls_the_original_wording_from_the_pool(self, store, tmp_path):
        """由某条反馈触发时，原话与对象锚点经池子回取——**不重新采集**。"""
        from l6_helpers import FB_REJECTED, REJECT_REASON, DLV_ID, feedback_event

        pool = FeedbackPool(store=store, now=lambda: NOW)
        pool.consume(feedback_event(
            feedback_id=FB_REJECTED, action="rejected", reason=REJECT_REASON,
        ))
        face = TrainingProtocol(
            store=store, understander=understander(), pool=pool, now=lambda: NOW,
        )
        session = face.understand(feedback_id=FB_REJECTED).data
        assert session.correction == REJECT_REASON
        assert session.target_ref == DLV_ID
        assert session.feedback_id == FB_REJECTED

    def test_same_feedback_twice_is_the_same_session(self, tmp_path):
        face, _ = protocol(tmp_path, understand=understander())
        first = face.understand(correction=CORRECTION).data.training_id
        second = face.understand(correction=CORRECTION).data.training_id
        assert first == second


class TestGwt2ConfirmationGate:
    """GWT-2：未经用户确认的理解**不得落账**。"""

    def test_unconfirmed_session_is_refused(self, tmp_path):
        face, store = protocol(tmp_path, understand=understander(), writer=_writer())
        session = face.understand(correction=CORRECTION).data
        with pytest.raises(TrainingError, match="未经用户确认"):
            face.record(session)

    def test_refusal_writes_nothing(self, tmp_path):
        writer = _writer()
        face, store = protocol(tmp_path, understand=understander(), writer=writer)
        session = face.understand(correction=CORRECTION).data
        with pytest.raises(TrainingError):
            face.record(session)
        assert writer.nodes == []
        assert store.list_files("reflection") == ()
        assert face.callbacks() == ()

    def test_confirm_then_record_goes_through(self, tmp_path):
        face, _ = protocol(tmp_path, understand=understander(), writer=_writer())
        session = face.understand(correction=CORRECTION).data
        outcome = face.record(face.confirm(session))
        assert outcome.session.confirmed is True


class TestGwt3RecordedTriple:
    """GWT-3：确认后落三件——记忆节点 / 提案候选 / 回访留痕；零 `change_id`。"""

    def test_pattern_node_is_appended_as_user_stated(self, tmp_path):
        writer = _writer()
        face, _ = protocol(tmp_path, understand=understander(), writer=writer)
        session = face.understand(correction=CORRECTION).data
        outcome = face.record(face.confirm(session))
        assert len(writer.nodes) == 1
        node = writer.nodes[0]
        assert node.type == "pattern"
        assert node.pattern == PATTERN_TEXT
        assert node.source == "user_stated"
        assert node.provenance is None            # 用户自陈不带 provenance（04 §4）
        assert node.triggered_by == ()            # 用户自陈类可留空
        assert outcome.pattern_node_id == node.memory_node_id
        assert outcome.memory_note == ""

    def test_proposal_candidate_is_carried_without_change_id(self, tmp_path):
        face, _ = protocol(
            tmp_path, understand=understander(suggestion=SUGGESTION), writer=_writer(),
        )
        session = face.understand(correction=CORRECTION).data
        outcome = face.record(face.confirm(session))
        assert outcome.session.suggestion.config_id == "fatigue.ignore-threshold"
        blob = json.dumps(outcome.model_dump(mode="json"), ensure_ascii=False)
        assert "change_id" not in blob
        assert "chg_" not in blob

    def test_callback_note_is_left_for_the_next_conversation(self, tmp_path):
        face, _ = protocol(tmp_path, understand=understander(), writer=_writer())
        session = face.understand(correction=CORRECTION).data
        outcome = face.record(face.confirm(session))
        assert outcome.callback.training_id == session.training_id
        assert outcome.callback.correction == CORRECTION     # 用户数据原样
        assert "我觉得" not in outcome.callback.text          # 生成文案不夹用户原话

    def test_memory_write_only_appends(self):
        """本层只增不改——`edit_node` / `replace_node` 的调用零出现（[04 §3.2] 红线）。"""
        for path in _l6_sources():
            text = path.read_text(encoding="utf-8")
            assert "edit_node(" not in text, path.name
            assert "replace_node(" not in text, path.name


class TestGwt4Degradation:
    """GWT-4：各面缺省**显式降级**，无一静默。"""

    def test_absent_memory_writer_is_stated(self, tmp_path):
        face, _ = protocol(tmp_path, understand=understander(suggestion=SUGGESTION))
        session = face.understand(correction=CORRECTION).data
        outcome = face.record(face.confirm(session))
        assert outcome.pattern_node_id == ""
        assert outcome.memory_note == MEMORY_ABSENT_NOTE

    def test_absent_suggestion_is_stated(self, tmp_path):
        face, _ = protocol(tmp_path, understand=understander(), writer=_writer())
        session = face.understand(correction=CORRECTION).data
        outcome = face.record(face.confirm(session))
        assert outcome.session.suggestion is None
        assert outcome.suggestion_note == NO_SUGGESTION_NOTE

    def test_present_suggestion_leaves_no_note(self, tmp_path):
        face, _ = protocol(
            tmp_path, understand=understander(suggestion=SUGGESTION), writer=_writer(),
        )
        session = face.understand(correction=CORRECTION).data
        assert face.record(face.confirm(session)).suggestion_note == ""

    def test_illegal_suggestion_config_id_is_refused(self, tmp_path):
        face, _ = protocol(
            tmp_path,
            understand=understander(suggestion={"config_id": "noseparator", "reason": SUGGESTION_REASON}),
        )
        with pytest.raises(TrainingError, match="点分形态"):
            face.understand(correction=CORRECTION)

    def test_memory_pure_memory_mode_does_not_claim_persistence(self, tmp_path):
        """无 `Store` ⇒ 纯内存态：读面照常，但不落盘。"""
        face = TrainingProtocol(understander=understander(), memory_writer=_writer(),
                                now=lambda: NOW)
        session = face.understand(correction=CORRECTION).data
        outcome = face.record(face.confirm(session))
        assert face.callbacks()[0].training_id == outcome.session.training_id


class TestGwt5CallbackReadFace:
    """GWT-5：回访留痕落 `reflection` 分区、读面可取、重启安全、空集不报错。"""

    def test_callback_is_persisted_under_reflection(self, tmp_path):
        face, store = protocol(tmp_path, understand=understander(), writer=_writer())
        outcome = face.record(face.confirm(face.understand(correction=CORRECTION).data))
        path = f"training/{outcome.session.training_id}.json"
        assert store.list_files("reflection") == (path,)

    def test_read_face_survives_a_restart(self, tmp_path):
        face, store = protocol(tmp_path, understand=understander(), writer=_writer())
        outcome = face.record(face.confirm(face.understand(correction=CORRECTION).data))
        reborn = TrainingProtocol(store=store, now=lambda: NOW)
        assert [c.training_id for c in reborn.callbacks()] == [outcome.session.training_id]
        assert reborn.outcome(outcome.session.training_id).callback.text == outcome.callback.text

    def test_empty_read_face_is_an_empty_tuple(self, tmp_path):
        face, _ = protocol(tmp_path, understand=understander())
        assert face.callbacks() == ()
        assert face.all() == ()
        assert face.outcome("train_absent") is None

    def test_acknowledge_clears_the_pending_face(self, tmp_path):
        face, store = protocol(tmp_path, understand=understander(), writer=_writer())
        session = face.understand(correction=CORRECTION).data
        face.record(face.confirm(session))
        face.acknowledge(session.training_id)
        assert face.callbacks() == ()
        assert len(face.callbacks(pending_only=False)) == 1
        reborn = TrainingProtocol(store=store, now=lambda: NOW)   # 置位也跨构造器成立
        assert reborn.callbacks() == ()

    def test_acknowledge_unknown_session_is_refused(self, tmp_path):
        face, _ = protocol(tmp_path, understand=understander())
        with pytest.raises(TrainingError, match="无此训练会话"):
            face.acknowledge("train_absent")


class TestGwt6TrainRoute:
    """GWT-6：`train` 去向接线——鸭子面、不 import L6、缺省 fail-closed。"""

    def test_route_is_registered_as_wired(self):
        spec = ROUTE_BY_INTENT["train"]
        assert spec.wired is True
        assert spec.owner is None

    def test_correction_is_a_declared_intent_param(self):
        assert "correction" in {spec.name for spec in INTENT_PARAM_SPECS["train"]}

    def test_dispatch_hands_the_session_out(self, tmp_path):
        face, _ = protocol(tmp_path, understand=understander(suggestion=SUGGESTION))
        bus = DispatchBus(trainings=face)
        outcome = bus.dispatch(train_confirmation(), now=NOW)
        assert outcome.wired is True
        assert outcome.envelope.status == "ok"
        assert outcome.training.session.restatement == RESTATEMENT
        assert outcome.training.session.confirmed is False

    def test_dispatch_without_the_port_does_not_fake_it(self, tmp_path):
        bus = DispatchBus()
        outcome = bus.dispatch(train_confirmation(), now=NOW)
        assert outcome.envelope.status == "unavailable"
        assert TRAIN_ABSENT_REASON in outcome.envelope.reason
        assert outcome.training is None

    def test_dispatch_is_refused_when_the_card_is_unconfirmed(self, tmp_path):
        face, _ = protocol(tmp_path, understand=understander())
        outcome = DispatchBus(trainings=face).dispatch(
            train_confirmation(confirmed=False), now=NOW,
        )
        assert outcome.envelope.status == "validation_failed"

    def test_illegal_port_payload_is_dependency_failed(self, tmp_path):
        class Broken:
            def start(self, confirmation, *, values=None, now=None):
                return "不是载荷"

        outcome = DispatchBus(trainings=Broken()).dispatch(train_confirmation(), now=NOW)
        assert outcome.envelope.status == "dependency_failed"
        assert "非法结构" in outcome.envelope.reason

    def test_l6_does_not_import_the_dispatch_bus(self):
        for path in _l6_sources():
            assert "st_agent.l3.dispatch" not in path.read_text(encoding="utf-8"), path.name


class TestStackAssembly:
    """`build_l6` 只增训练面：缺省路径不变、注入即生效。"""

    def test_build_l6_exposes_the_training_face(self, tmp_path):
        store = Store.create(tmp_path / "root", PASS)
        built = build_l6(store, understander=understander(), memory_writer=_writer(),
                         now=lambda: NOW)
        assert isinstance(built.training, TrainingProtocol)
        session = built.training.understand(correction=CORRECTION).data
        built.training.record(built.training.confirm(session))
        assert len(built.training.callbacks()) == 1


def _writer():
    from l6_helpers import memory_writer

    return memory_writer()


def _l6_sources():
    """L6 层源码清单（同 [`test_pool.py`](test_pool.py) 的逐文件扫描口径）。"""
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src" / "st_agent" / "l6"
    return sorted(src.glob("*.py"))
