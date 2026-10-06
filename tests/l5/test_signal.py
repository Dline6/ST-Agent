"""T-L5-001.1 测试：信号模型与采纳（[07 §1](../../docs/技术架构-v2/07-L5-主动触达.md)）。

GWT 对照（任务文件 5 条）：
- GWT-1 一条 `SignalEmitted` → 六字段齐备的 `Signal`，`signal_id` 合 01 §1 形态
- GWT-2 同一事件重复采纳得**同一 `signal_id`**（幂等）；内容 / 级别 / 溯源不同即不同
- GWT-3 内容载体逐视角**并列保留**（非单视角结论），不得为空、不合并
- GWT-4 缺字段 / 非法取值 / 非法引用 / 事件名不符 / 缺 `trace_id` → 逐类显式报错并点名字段
- GWT-5 结论或逐视角摘要未过 01 §6 输出校验 → 拒收并点名命中原文
"""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from l5_helpers import (
    ANN_ID,
    CONCLUSION,
    LENS_A,
    LENS_B,
    LENS_C,
    MN_ID,
    NOW,
    RUN_ID,
    SNAP_ID,
    TRACE_ID,
    content,
    event,
    payload,
    stance,
)
from st_agent.contracts.identifiers import SignalId, digest_id
from st_agent.contracts.neutrality import NeutralityGuard
from st_agent.contracts.time_events import PlatformEvent
from st_agent.l5 import (
    SIGNAL_EVENT,
    Signal,
    SignalAdoptionError,
    SignalContent,
    SignalLensStance,
    adopt_signal,
    signal_digest_key,
)

SIG_FORMAT = re.compile(r"^sig_[0-9a-f]{20}$")


class TestGwt1SixFields:
    """GWT-1：采纳产出 07 §1 的六字段，且 `signal_id` 合 01 §1 形态。"""

    def test_adopt_yields_contract_fields(self):
        signal = adopt_signal(event())
        assert set(signal.model_dump()) == {
            "signal_id", "level", "content_ref", "evidence_refs", "dedup_key", "source_trace_id",
        }
        assert signal.level == "important"
        assert signal.dedup_key == "sh.600000:announcement_density"
        assert signal.source_trace_id == TRACE_ID
        assert signal.content_ref.conclusion == CONCLUSION

    def test_signal_id_is_contract_form(self):
        signal = adopt_signal(event())
        assert SIG_FORMAT.match(signal.signal_id)
        assert SignalId.of(signal.signal_id).value == signal.signal_id
        # 形态确定 ⇒ 可复算：摘要输入与 id 一一对应
        expected = SignalId.of(digest_id(
            "sig",
            *signal_digest_key(
                level=signal.level, content=signal.content_ref,
                evidence_refs=signal.evidence_refs, dedup_key=signal.dedup_key,
                source_trace_id=signal.source_trace_id,
            ),
        )).value
        assert expected == signal.signal_id

    def test_evidence_refs_sorted_and_deduped(self):
        signal = adopt_signal(event(evidence_refs=[RUN_ID, ANN_ID, RUN_ID]))
        assert signal.evidence_refs == tuple(sorted({ANN_ID, RUN_ID}))

    def test_all_four_evidence_kinds_accepted(self):
        signal = adopt_signal(event(evidence_refs=[ANN_ID, SNAP_ID, RUN_ID, MN_ID]))
        assert len(signal.evidence_refs) == 4


class TestGwt2DeterministicId:
    """GWT-2：幂等锚点——同一信号重复投递得同一 `signal_id`。"""

    def test_same_event_same_id(self):
        assert adopt_signal(event()).signal_id == adopt_signal(event()).signal_id

    def test_id_ignores_adoption_time(self):
        later = event(occurred_at=NOW.replace(hour=23))
        assert adopt_signal(later).signal_id == adopt_signal(event()).signal_id

    def test_id_ignores_evidence_and_stance_order(self):
        permuted = event(
            evidence_refs=[RUN_ID, ANN_ID],
            content_ref=content(stances=[
                stance(LENS_C, "neutral", "同类标的公告密度普遍上行"),
                stance(LENS_A, "positive", "资金面显示净流入"),
                stance(LENS_B, "negative", "基本面指标走弱"),
            ]),
        )
        assert adopt_signal(permuted).signal_id == adopt_signal(event()).signal_id

    @pytest.mark.parametrize("over, trace_id", [
        ({"level": "emergency"}, TRACE_ID),
        ({"dedup_key": "sh.600000:price_limit"}, TRACE_ID),
        ({"content_ref": content(conclusion="另一条结论")}, TRACE_ID),
        ({"source_trace_id": "tr_" + "6" * 20}, "tr_" + "6" * 20),
    ])
    def test_other_signal_is_other_id(self, over, trace_id):
        assert adopt_signal(event(trace_id=trace_id, **over)).signal_id != adopt_signal(event()).signal_id


class TestGwt3MultiLensContent:
    """GWT-3：内容载体是多视角并列，**不是**单一视角结论。"""

    def test_stances_kept_side_by_side(self):
        signal = adopt_signal(event())
        assert [s.stance for s in signal.content_ref.lens_stances] == [
            "positive", "negative", "neutral",
        ]
        assert [s.lens_id for s in signal.content_ref.lens_stances] == [LENS_A, LENS_B, LENS_C]

    def test_stances_normalized_by_lens_id(self):
        signal = adopt_signal(event(content_ref=content(stances=[
            stance(LENS_C, "neutral", "同类标的普遍上行"),
            stance(LENS_A, "positive", "资金面净流入"),
        ])))
        assert [s.lens_id for s in signal.content_ref.lens_stances] == [LENS_A, LENS_C]

    def test_empty_stances_rejected(self):
        with pytest.raises(SignalAdoptionError, match="lens_stances 不得为空"):
            adopt_signal(event(content_ref=content(stances=[])))

    def test_duplicate_lens_rejected(self):
        with pytest.raises(SignalAdoptionError, match="不得出现两次"):
            adopt_signal(event(content_ref=content(
                stances=[stance(LENS_A, "positive", "资金面净流入"),
                         stance(LENS_A, "negative", "基本面走弱")]
            )))

    def test_model_itself_refuses_single_lens_free_content(self):
        """值对象层面也守同一条不变量（不经负载构造也不许空逐视角）。"""
        with pytest.raises(ValidationError) as err:
            SignalContent(conclusion=CONCLUSION, lens_stances=())
        assert "lens_stances 不得为空" in str(err.value)

    def test_single_lens_stance_is_still_content_not_a_unified_conclusion(self):
        """只有一个视角有数据时可交付——但 `stance` 如实记 `insufficient-data`，不冒充结论。"""
        signal = adopt_signal(event(content_ref=content(stances=[
            stance(LENS_A, "insufficient-data", "该视角数据不足，未形成方向"),
        ])))
        assert signal.content_ref.lens_stances[0].stance == "insufficient-data"


class TestGwt4ExplicitRejection:
    """GWT-4：负载不合契约即显式拒（逐类点名字段），不臆补、不产出半截信号。"""

    def test_event_name_must_be_signal_emitted(self):
        other = PlatformEvent(event="FeedbackRecorded", payload={}, occurred_at=NOW)
        with pytest.raises(SignalAdoptionError, match=SIGNAL_EVENT):
            adopt_signal(other)

    @pytest.mark.parametrize("field", [
        "level", "content_ref", "evidence_refs", "dedup_key", "source_trace_id",
    ])
    def test_missing_payload_field_named(self, field):
        short = payload()
        del short[field]
        ev = PlatformEvent(event=SIGNAL_EVENT, payload=short, trace_id=TRACE_ID, occurred_at=NOW)
        with pytest.raises(SignalAdoptionError, match=field):
            adopt_signal(ev)

    @pytest.mark.parametrize("level", ["urgent", "", "IMPORTANT", None])
    def test_unknown_level_rejected(self, level):
        with pytest.raises(SignalAdoptionError, match="level"):
            adopt_signal(event(level=level))

    @pytest.mark.parametrize("bad", [
        {"content_ref": "x"},
        {"content_ref": {}},
        {"content_ref": {"conclusion": "", "lens_stances": [stance(LENS_A, "positive", "好")]}},
        {"content_ref": {"conclusion": CONCLUSION}},
        {"content_ref": {"conclusion": CONCLUSION, "lens_stances": "x"}},
        {"content_ref": {"conclusion": CONCLUSION,
                         "lens_stances": [stance(LENS_A, "bullish", "上行")]}},
        {"content_ref": {"conclusion": CONCLUSION, "lens_stances": [{"stance": "neutral"}]}},
        {"evidence_refs": []},
        {"evidence_refs": "ann_x"},
        {"evidence_refs": [ANN_ID, "sh.600000"]},
        {"evidence_refs": ["lens_" + "a" * 20]},
        {"dedup_key": "  "},
        {"source_trace_id": "not-a-trace"},
    ])
    def test_malformed_payload_rejected(self, bad):
        with pytest.raises(SignalAdoptionError):
            adopt_signal(event(**bad))

    def test_error_names_the_offending_evidence_ref(self):
        offending = "sig_" + "9" * 20
        with pytest.raises(SignalAdoptionError, match=offending) as err:
            adopt_signal(event(evidence_refs=[ANN_ID, offending]))
        assert "announcement_id" in str(err.value)

    def test_envelope_without_trace_id_refused(self):
        """信封本身强制 `trace_id`（01 §11）；绕过信封校验时本层仍拒（防御性）。"""
        with pytest.raises(ValidationError, match="trace_id"):
            PlatformEvent(event=SIGNAL_EVENT, payload=payload(), occurred_at=NOW)
        forged = PlatformEvent.model_construct(
            event=SIGNAL_EVENT, payload=payload(), trace_id="", change_id=None, occurred_at=NOW
        )
        with pytest.raises(SignalAdoptionError, match="trace_id"):
            adopt_signal(forged)

    def test_non_mapping_payload_refused(self):
        forged = PlatformEvent.model_construct(
            event=SIGNAL_EVENT, payload="x", trace_id=TRACE_ID, change_id=None, occurred_at=NOW
        )
        with pytest.raises(SignalAdoptionError, match="负载须为映射"):
            adopt_signal(forged)

    def test_trace_id_and_source_trace_must_agree(self):
        with pytest.raises(SignalAdoptionError, match="同源"):
            adopt_signal(event(trace_id="tr_" + "7" * 20))

    @pytest.mark.parametrize("field, value", [
        ("signal_id", "sig_short"),
        ("source_trace_id", "sig_" + "1" * 20),
    ])
    def test_signal_model_validates_ids(self, field, value):
        base = dict(
            signal_id=adopt_signal(event()).signal_id, level="important",
            content_ref=adopt_signal(event()).content_ref,
            evidence_refs=(ANN_ID,), dedup_key="k", source_trace_id=TRACE_ID,
        )
        base[field] = value
        with pytest.raises(ValidationError):
            Signal(**base)

    def test_signal_model_refuses_empty_evidence(self):
        signal = adopt_signal(event())
        with pytest.raises(ValidationError) as err:
            Signal(**{**signal.model_dump(), "content_ref": signal.content_ref,
                      "evidence_refs": ()})
        assert "evidence_refs 不得为空" in str(err.value)


class TestGwt5Neutrality:
    """GWT-5：结论与逐视角摘要在采纳面过 01 §6 执行点 2（中性化不开的口子）。"""

    @pytest.mark.parametrize("text", ["我认为该标的偏强", "很遗憾，本周数据缺失", "让我来提示一下"])
    def test_anthropomorphic_conclusion_refused(self, text):
        with pytest.raises(SignalAdoptionError, match="未过 01 §6 中性化校验"):
            adopt_signal(event(content_ref=content(conclusion=text)))

    def test_anthropomorphic_stance_summary_refused_and_named(self):
        with pytest.raises(SignalAdoptionError, match="lens_stances") as err:
            adopt_signal(event(content_ref=content(stances=[
                stance(LENS_A, "positive", "资金面净流入"),
                stance(LENS_B, "negative", "我担心基本面走弱"),
            ])))
        assert "我担心基本面走弱" in str(err.value)

    def test_reason_text_of_adopted_signal_is_neutral(self):
        """采纳通过的内容本身也过守卫（正证：同一规则库、同一份实现）。"""
        signal = adopt_signal(event())
        guard = NeutralityGuard()
        assert guard.check_output(signal.content_ref.conclusion).passed
        assert all(guard.check_output(s.summary).passed for s in signal.content_ref.lens_stances)

    def test_stance_value_object_validates_shape(self):
        with pytest.raises(ValidationError):
            SignalLensStance(lens_id="not-a-lens", stance="neutral", summary="x")
