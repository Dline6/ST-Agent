"""T-SC-001.2 测试：01-平台共享契约 §4 Trace（推理链）。"""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from st_agent.contracts import (
    STEP_TYPES,
    ConclusionRef,
    ContractViolation,
    Trace,
    TraceError,
    TraceId,
    TraceStep,
    digest_of,
)

TZ = timezone.utc
T0 = datetime(2026, 9, 25, 9, 0, tzinfo=TZ)


def step(kind="skill_run", ref="run_0123456789abcdef0123", **kw) -> TraceStep:
    base = dict(step_type=kind, ref=ref,
                input_digest=digest_of({"in": 1}), output_digest=digest_of({"out": 2}),
                duration_ms=12, timestamp=T0)
    base.update(kw)
    return TraceStep(**base)


class TestSection4Trace:
    """GWT-3：一条结论可沿 Trace 还原完整推理链。"""

    def test_replay_restores_full_chain(self):
        """data_fetch → skill_run → lens_opinion → aggregation 全链可回放。"""
        tr = Trace(trace_id=TraceId.generate())
        tr = (tr.append_step(step("data_fetch", "snap_0123456789abcdef0123"))
                .append_step(step("skill_run", "run_0123456789abcdef0123"))
                .append_step(step("lens_opinion", "lens_0123456789abcdef01"))
                .append_step(step("aggregation", "tr_0123456789abcdef012345")))
        replayed = tr.replay()
        assert [r[0] for r in replayed] == ["data_fetch", "skill_run",
                                            "lens_opinion", "aggregation"]
        assert replayed[1][1] == "run_0123456789abcdef0123"   # ref 可下钻
        assert replayed[1][2] and replayed[1][3]              # 输入输出摘要在场
        tr = tr.conclude(ConclusionRef(kind="message", ref="msg-001"))
        assert tr.conclusion_ref.kind == "message"

    def test_five_step_types_registered(self):
        assert STEP_TYPES == ("data_fetch", "skill_run", "memory_read",
                              "lens_opinion", "aggregation")
        with pytest.raises(ValidationError):
            step(kind="roleplay")        # 五枚举之外 → 拒

    def test_append_only_rewrite_rejected(self):
        """GWT-3a：修改既有步骤被拒绝；追加成功且顺序保留。"""
        tr = Trace(trace_id=TraceId.generate())
        s1, s2 = step(), step(ref="run_ffffffffffffffffffff")
        tr = tr.append_step(s1).append_step(s2)
        with pytest.raises(ValidationError):      # frozen：原地改写即拒
            s1.step_type = "aggregation"
        with pytest.raises(ValidationError):      # 容器本身也 frozen
            tr.steps[0].ref = "tampered"
        s3 = step(ref="run_aaaaaaaaaaaaaaaaaaaa")
        tr2 = tr.append_step(s3)                  # 追加成功
        assert [s.ref for s in tr2.steps] == [s1.ref, s2.ref, s3.ref]
        assert tr.steps == (s1, s2)               # 原 Trace 不受影响（函数式更新）

    def test_degraded_path_recorded(self):
        """断网/降级路径同样记入 Trace，且带中性说明。"""
        tr = Trace(trace_id=TraceId.generate())
        tr = tr.append_degraded(
            step("data_fetch", "snap_0123456789abcdef0123"),
            note="数据源不可用，使用最后快照（已标注过期）",
        )
        s = tr.steps[-1]
        assert s.degraded is True
        assert "降级" in s.note or "快照" in s.note

    def test_conclude_seals_trace(self):
        """conclusion_ref 登记后封链：再追加/再 conclude 均拒绝。"""
        tr = Trace(trace_id=TraceId.generate()).append_step(step())
        tr = tr.conclude(ConclusionRef(kind="signal", ref="sig_0123456789abcdef0123"))
        assert tr.closed is True
        with pytest.raises(TraceError, match="封链"):
            tr.append_step(step())
        with pytest.raises(TraceError, match="封链"):
            tr.conclude(ConclusionRef(kind="message", ref="msg-002"))

    def test_naive_timestamp_rejected(self):
        with pytest.raises(ValidationError, match="时区"):
            step(timestamp=datetime(2026, 9, 25, 9, 0))

    def test_negative_duration_rejected(self):
        with pytest.raises(ValidationError):
            step(duration_ms=-1)

    def test_digest_stable_and_sensitive(self):
        """摘要可复算且对输入敏感（可追溯性的最小保证）。"""
        a = digest_of({"x": 1, "y": [2, 3]})
        b = digest_of({"y": [2, 3], "x": 1})       # 键序无关（规范化）
        c = digest_of({"x": 2, "y": [2, 3]})       # 内容变化 → 摘要变化
        assert a == b and a != c
        assert len(a) == 64

    def test_trace_immutable_container(self):
        tr = Trace(trace_id=TraceId.generate())
        with pytest.raises(ValidationError):
            tr.closed = True
