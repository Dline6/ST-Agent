"""T-SC-001.1 测试：01-平台共享契约 §1 标识体系 + §5 ResultEnvelope。"""

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from st_agent.contracts import (
    ID_ALIASES,
    ID_KINDS,
    ID_REGISTRY,
    AnnouncementId,
    ChangeId,
    ContractViolation,
    DatasetSnapshotId,
    DeliveryId,
    EvidenceRef,
    FeedbackId,
    FlowId,
    LensId,
    MemoryNodeId,
    PlatformId,
    ResultEnvelope,
    SignalId,
    SkillId,
    SkillRunId,
    StockId,
    TraceId,
    TrialId,
)
from st_agent.contracts.identifiers import digest_id

TZ = timezone.utc

LOCALLY_GENERATED = (SkillId, SkillRunId, MemoryNodeId, TraceId, SignalId,
                     DeliveryId, FeedbackId, ChangeId, LensId, TrialId)
"""**本机产生的 10 类**：`generate()` 返回新的全局唯一 ID（`<prefix>_<uuid4 前 20 位>`）。"""

NOT_LOCALLY_GENERATED = (StockId, FlowId, AnnouncementId, DatasetSnapshotId)
"""**不由本机产生的 4 类**：`generate()` 须被拒绝——映射产生（`stock_id`）/ 拼接产生
（`flow_id`）/ 确定性摘要（`announcement_id` / `dataset_snapshot_id`，T-SC-002）。"""


# ───────────────────────── §1 标识体系 ─────────────────────────


class TestSection1Identifiers:
    """GWT-1：任一上层模块引用标识，均来自契约定义、全局唯一、生成后不可变。"""

    @pytest.mark.parametrize("typ", LOCALLY_GENERATED)
    def test_generate_produces_valid_unique_ids(self, typ):
        a, b = typ.generate(), typ.generate()
        assert a.value != b.value                      # 全局唯一（uuid4）
        assert a.id_kind in ID_KINDS                   # 契约 §1 登记过的类型
        assert ID_ALIASES[a.id_kind] is typ

    @pytest.mark.parametrize("typ", LOCALLY_GENERATED)
    def test_immutable_after_creation(self, typ):
        """生成后不可变（§1 首句）。"""
        tid = typ.generate()
        with pytest.raises(ValidationError):
            tid.value = "tampered"

    def test_stock_id_exchange_format(self):
        """stock_id 首个落地形态 = BaoStock sh./sz. 格式（数据库设计 01-核心实体层）。"""
        assert StockId.of("sh.600000").value == "sh.600000"
        assert StockId.of("sz.000001").id_kind == "stock_id"
        with pytest.raises(ValidationError):
            StockId.of("600000")            # 缺交易所前缀
        with pytest.raises(ValidationError):
            StockId.of("sh.60000")          # 位数不足

    @pytest.mark.parametrize("typ, hint", [
        (StockId, "security"),           # 由 L0 按交易所代码映射产生
        (FlowId, "flow_id_for"),         # 由 L1 按「注册名 + 版本」拼接产生
        (AnnouncementId, "digest_id"),   # 由 L0 按业务键的确定性摘要产生
        (DatasetSnapshotId, "snapshot_id"),  # 由 L0 按同步水位的确定性摘要产生
    ])
    def test_not_locally_generated_types_reject_generate(self, typ, hint):
        """01 §1：这 4 类的形态由产生方决定，随机产出即让形态与幂等/可复核性失效——
        拒绝说明须**可操作**（点名产生方或其构造路径）。"""
        with pytest.raises(ContractViolation, match=hint):
            typ.generate()

    def test_generation_partition_covers_all_kinds(self):
        """产出面二分且不漏：本机产生 10 类 + 拒绝 4 类 = §1 的 14 类。"""
        both = LOCALLY_GENERATED + NOT_LOCALLY_GENERATED
        assert {t.id_kind for t in both} == set(ID_KINDS)
        assert len(both) == len(ID_KINDS) == 14

    def test_kind_mismatch_rejected_at_source(self):
        """用 A 类型的 of() 校验 B 类的字符串 → 格式校验拦截（防串用第一道闸）。"""
        with pytest.raises(ValidationError, match="trace_id 格式非法"):
            TraceId.of("sig_0123456789abcdef0123")

    def test_roundtrip_serialization(self):
        """可本地持久化：JSON 往返不丢语义。"""
        tid = TraceId.generate()
        data = tid.model_dump(mode="json")
        assert TraceId.model_validate(data).value == tid.value

    def test_flow_id_registered_form(self):
        """flow_id 形态 = wf_<注册名>_v<主>.<次>（01 §1；由 T-L1-003.1 触发登记）。"""
        assert FlowId.of("wf_daily_brief_v1.0").id_kind == "flow_id"
        assert FlowId.of("wf_daily-brief_v2.3").value == "wf_daily-brief_v2.3"
        for bad in ("wf_daily_brief", "daily_brief_v1.0", "sk_x_v1.0", "wf__v1.0"):
            with pytest.raises(ValidationError):
                FlowId.of(bad)

    def test_registry_covers_contract_table(self):
        """ID_REGISTRY 是 §1 表的完整机器可读副本（14 类）。"""
        assert len(ID_KINDS) == 14
        assert set(ID_KINDS) == set(ID_REGISTRY) == set(ID_ALIASES)


# ───────────────────────── §1 确定性摘要 ID（T-L0-011） ─────────────────────────


class TestDigestId:
    """GWT-5（T-L0-011）：``announcement_id`` / ``dataset_snapshot_id`` 共用的**唯一**
    确定性摘要实现（D-030 / D-031 / D-033）——全仓不得有第二份副本。"""

    def test_deterministic_and_bounded(self):
        first = digest_id("ann", "sh.600000", "标题", "2026-09-20")
        assert first == digest_id("ann", "sh.600000", "标题", "2026-09-20")   # 确定性
        assert first != digest_id("ann", "sh.600000", "另一标题", "2026-09-20")
        assert re.fullmatch(r"ann_[0-9a-f]{20}", first)                      # 恒 24 字符
        assert len(first) <= 128                                             # 远低于 ref 上限

    def test_empty_and_missing_parts_tolerated(self):
        """快照 ID 以「无水位」起步（``snap_...``），空成分不得炸。"""
        assert re.fullmatch(r"snap_[0-9a-f]{20}", digest_id("snap"))
        assert re.fullmatch(r"snap_[0-9a-f]{20}", digest_id("snap", ""))
        assert digest_id("snap", "") == digest_id("snap")

    def test_single_recipe_in_repo(self):
        """只有一个 ``<前缀>_<sha256 前 20 位>`` 实现（本轮由 L0 的 ``info/sync.py`` 迁入）。"""
        root = Path(__file__).resolve().parents[2] / "src" / "st_agent"
        offenders = [p.relative_to(root).as_posix() for p in root.rglob("*.py")
                     if "hexdigest()[:20]" in p.read_text(encoding="utf-8")
                     and p.name != "identifiers.py"]
        assert offenders == []


# ───────────────────────── §5 ResultEnvelope ─────────────────────────


class TestSection5ResultEnvelope:
    """GWT-2：Skill 执行产出必须包成 ResultEnvelope，失败语义走 §5 分支。"""

    def test_ok_carries_payload_and_evidence(self):
        ev = EvidenceRef(kind="announcement_id", ref="ann_0123456789abcdef0123")
        env = ResultEnvelope.ok({"hits": 3}, as_of=datetime(2026, 9, 25, tzinfo=TZ),
                                evidence_refs=(ev,))
        assert env.status == "ok"
        assert env.data == {"hits": 3}
        assert env.evidence_refs == (ev,)

    def test_ok_without_data_rejected(self):
        with pytest.raises(ValidationError, match="data"):
            ResultEnvelope.ok(None)

    def test_empty_requires_reason(self):
        """合法空结果必须携带原因说明，不得返回裸空。"""
        env = ResultEnvelope.empty("今日全市场无退市高危信号")
        assert env.status == "empty" and env.data is None and env.reason
        with pytest.raises(ValidationError, match="reason"):
            ResultEnvelope(status="empty", data=None, reason=None)

    def test_six_status_branches_covered(self):
        """六种 status 全部可构造且语义字段齐备。"""
        now = datetime.now(TZ)
        envs = {
            "ok": ResultEnvelope.ok(1),
            "empty": ResultEnvelope.empty("无信号"),
            "unavailable": ResultEnvelope.unavailable("数据源不可用", last_updated_at=now),
            "failed": ResultEnvelope.failed("执行异常", log_ref="run_0123456789abcdef0123"),
            "dependency_failed": ResultEnvelope.dependency_failed("上游 Skill 失败"),
            "validation_failed": ResultEnvelope.validation_failed("参数超出取值范围"),
        }
        assert set(envs) == {"ok", "empty", "unavailable", "failed",
                             "dependency_failed", "validation_failed"}

    def test_failure_branches_forbid_payload(self):
        """失败分支不得携带 data（防用错误数据继续，§5 dependency_failed 语义）。"""
        with pytest.raises(ValidationError, match="data"):
            ResultEnvelope(status="failed", data={"x": 1}, reason="r", log_ref="L")

    def test_unavailable_requires_last_updated(self):
        """unavailable 必须标注「最后更新时间 T」。"""
        with pytest.raises(ValidationError, match="last_updated_at"):
            ResultEnvelope(status="unavailable", reason="数据延迟")

    def test_failed_requires_log_entry(self):
        """failed 须含可查日志入口。"""
        with pytest.raises(ValidationError, match="log_ref"):
            ResultEnvelope(status="failed", reason="崩了")

    def test_naive_datetime_rejected(self):
        """时间字段必须带时区语义（01 §8）。"""
        with pytest.raises(ValidationError, match="时区"):
            ResultEnvelope.ok(1, as_of=datetime(2026, 9, 25))

    def test_factory_helpers_are_canonical_path(self):
        """Skill 实现统一走工厂方法，绕过裸构造的语义负担。"""
        assert ResultEnvelope.validation_failed("理由").status == "validation_failed"
        assert ResultEnvelope.dependency_failed("上游失败").reason == "上游失败"

    def test_envelope_immutable(self):
        env = ResultEnvelope.ok(1)
        with pytest.raises(ValidationError):
            env.status = "empty"
