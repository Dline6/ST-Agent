"""`T-INT-005` · M4 集成关卡：反思演进与生态闭环的跨层装配用例。

只测**装配关系与跨层数据流**（单任务行为已由各自套件覆盖，见各任务 备注）：本关卡的
GWT 锚在 [00 §5 端到端数据流](../../docs/技术架构-v2/00-架构总览.md) 的**第 7–8 步**
（反馈回流 → 反思报告 → 演进建议 → 变更流）与 [09-生态](../../docs/技术架构-v2/09-生态与分享.md)
的导出 / 导入校验流程。

全部离线（出网面——LLM / 渠道 / 索引——由 rig 的替身接管）；真实 LLM 端点在
`tests/live/test_l6_adapters_live.py`（GWT-11）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from rig import ROOT_NAME
from rig_m4 import NOW, M4Rig, observation, seeded_m4

from st_agent.contracts.capability_types import Provenance, SkillDescriptor
from st_agent.contracts.identifiers import FeedbackId, TraceId
from st_agent.eco import ShareContainer, ShareFormatError, ShareImportError
from st_agent.eco.import_pipeline import IMPORT_CONFIRMATION
from st_agent.l1.skills.ids import parse_skill_id
from st_agent.l2.memory import FragmentPayload, checked_node, new_node_id
from st_agent.l6 import (
    DAY_CONFIG_ID,
    REPEAT_CONFIG_ID,
    TIME_CONFIG_ID,
    TIER_CONFIG_ID,
    ChangeProposal,
)
from st_agent.l6.weekly import week_key

CUSTOM_SKILL_BASE = "sk_m4_share_demo"
CUSTOM_SKILL_ID = f"{CUSTOM_SKILL_BASE}_v1.0"


@pytest.fixture()
def rig(tmp_path: Path) -> M4Rig:
    return seeded_m4(tmp_path / ROOT_NAME)


# ───────────────────────────── 共用小工具 ─────────────────────────────


def _record_feedback(rig: M4Rig, action: str, *, feedback_id: str, reason: str = "") -> None:
    """像对话层那样记一条反馈（走真 L3 采集面 → 总线 → 各订阅者）。"""
    ref = TraceId.generate().value
    envelope = rig.m4.chat.record_feedback(
        {"kind": "trace", "ref": ref}, action,
        reason=reason or None, feedback_id=feedback_id,
    )
    assert envelope.status == "ok", envelope.reason


def _register_custom_skill(rig: M4Rig) -> SkillDescriptor:
    """在源根注册一个**自建** Skill（导出的取材面；官方面不可导出，因其目标根已存在）。"""
    return rig.m4.m3.m1.runtime.skills.register(
        CUSTOM_SKILL_BASE, version="1.0", name="导出演示能力",
        description="M4 关卡导出→导入闭环用的自建能力",
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {}},
        source="user-built",
    )


def _memory_listing(rig: M4Rig) -> dict[str, bytes]:
    store = rig.m4.store
    return {name: store.get("memory", name) for name in store.list_files("memory")}


def _due_now(rig: M4Rig) -> None:
    """把周报的到点配置改成「此刻必然到点」（周一 08:30 ⇒ 周一 00:00）。"""
    rig.m4.l6.reports.set_day_of_week(0)
    rig.m4.l6.reports.set_time("00:00")


# ─────────────────────────── GWT-1 生产装配根 ───────────────────────────


class TestGwt1AssemblyRoot:
    def test_all_faces_are_constructed_with_real_dependencies(self, rig: M4Rig) -> None:
        m4 = rig.m4
        # L6 八面
        assert m4.l6.pool is not None and m4.l6.reports is not None
        assert m4.l6.training is not None and m4.l6.proposals is not None
        assert m4.l6.experiments is not None and m4.l6.authorization is not None
        assert m4.l6.change_flow is not None and m4.l6.factory_reset is not None
        # ECO 三面
        assert m4.eco.exporter is not None and m4.eco.importer is not None
        assert m4.eco.index is not None
        # M3 面原样就位、同源
        assert m4.chat is m4.m3.chat and m4.store is m4.m3.store
        assert m4.l5 is m4.m3.l5 and m4.m2 is m4.m3.m2

    def test_families_are_registered_on_the_shared_registry(self, rig: M4Rig) -> None:
        listed = {e.config_id for e in rig.m4.m3.m1.runtime.config_registry.list(scope="global")}
        assert {TIME_CONFIG_ID, DAY_CONFIG_ID, REPEAT_CONFIG_ID, TIER_CONFIG_ID} <= listed

    def test_bus_subscribers_include_l6_and_the_change_notifier(self, rig: M4Rig) -> None:
        subs = rig.m4.events.subscribers()
        assert "l6:feedback-pool" in subs
        assert "l5:delivery-orchestrator" in subs and "l5:fatigue-monitor" in subs
        assert subs.count("m4:change-notifier") == 2          # 两个变更事件各一订阅

    def test_eco_index_reports_the_missing_endpoint(self, rig: M4Rig) -> None:
        """官方索引未配端点 ⇒ 显式失败，**不返回半截索引**（[09 §4]）。"""
        with pytest.raises(Exception) as exc:
            rig.m4.eco.index.browse()
        assert "索引" in str(exc.value)


# ─────────────────────────── GWT-2 反馈回流与去重 ───────────────────────────


class TestGwt2FeedbackLoop:
    def test_feedback_lands_in_the_pool_via_the_bus(self, rig: M4Rig) -> None:
        fid = FeedbackId.generate().value
        _record_feedback(rig, "adopted", feedback_id=fid)
        assert rig.m4.l6.pool.get(fid) is not None

    def test_same_feedback_id_is_idempotent_not_an_append(self, rig: M4Rig) -> None:
        fid = FeedbackId.generate().value
        _record_feedback(rig, "adopted", feedback_id=fid)
        _record_feedback(rig, "ignored", feedback_id=fid)         # 同一事实的重放
        assert rig.m4.l6.pool.counts().total == 1                 # 幂等覆盖、不追加流水
        assert rig.m4.l6.pool.get(fid).event.action == "ignored"

    def test_both_l6_and_l5_consume_the_same_feedback(self, rig: M4Rig) -> None:
        """L6 池与 L5 疲劳面**并列消费**同一事件（互不干扰；[08 §1] / [07 §7]）。"""
        subs = rig.m4.events.subscribers("FeedbackRecorded")
        assert "l6:feedback-pool" in subs and "l5:fatigue-monitor" in subs


# ─────────────────────── GWT-3 每周反思报告（真实反馈数据） ───────────────────────


class TestGwt3WeeklyReport:
    def test_report_is_built_from_real_data_and_delivered_once(self, rig: M4Rig) -> None:
        _due_now(rig)
        _record_feedback(rig, "adopted", feedback_id=FeedbackId.generate().value)

        result = rig.m4.tick(NOW)
        report = result.weekly_report
        assert report is not None and report.week == week_key(NOW.date())
        assert len(report.sections) == 4
        assert report.delivered_channel == "desktop"
        assert rig.channels["desktop"].payloads                  # 真经 L5 渠道投出
        assert rig.m4.l6.reports.stored(report.week).delivered_channel == "desktop"

        # 同一周第二次 tick 不重投（判据取盘上留痕，重启安全）
        assert rig.m4.tick(NOW).weekly_report is None

    def test_no_touch_week_reports_the_empty_state(self, rig: M4Rig) -> None:
        """没有任何触达 ⇒ 报告走「本周无触达」空态，**不硬凑**（[08 §2]）。"""
        report = rig.m4.l6.reports.build(week_key(NOW.date()), now=NOW)
        assert report.empty
        assert "无触达" in report.body

    def test_low_feedback_week_reports_insufficient_data(self, rig: M4Rig) -> None:
        """有触达但反馈低于数据不足阈值 ⇒ 走「数据还不够」空态（两处空态分开判定）。"""
        rig.m4.tick(NOW)                                         # 产生真实投递留痕
        report = rig.m4.l6.reports.build(week_key(NOW.date()), now=NOW)
        assert not report.empty and report.insufficient
        assert "数据还不够" in report.body

    def test_weekly_report_turns_patterns_into_proposals(self, tmp_path: Path) -> None:
        """到点时先做一次模式识别，提案落盘、并受频率上限约束（[08 §4]）。"""
        rig = seeded_m4(tmp_path / ROOT_NAME, observations=(observation(key="k1"),))
        _due_now(rig)
        rig.m4.tick(NOW)
        assert rig.m4.l6.proposals.get(rig.m4.l6.proposals.all()[0].proposal_id) is not None
        assert len(rig.m4.l6.proposals.all()) == 1


# ─────────────────── GWT-4 演进授权档位 + 自动变更显式告知 ───────────────────


class TestGwt4AuthorizationAndNotice:
    def test_collaborative_files_without_applying(self, rig: M4Rig) -> None:
        run = rig.m4.l6.change_flow.submit(
            ChangeProposal(config_id=TIME_CONFIG_ID, suggested="07:30", reason="周报改到早间生成")
        )
        assert run.applied is False and run.pending is not None
        assert rig.m4.l6.reports.report_time().strftime("%H:%M") == "20:00"   # 未生效

    def test_manual_files_nothing(self, rig: M4Rig) -> None:
        rig.m4.l6.authorization.set_tier("manual")
        run = rig.m4.l6.change_flow.submit(
            ChangeProposal(config_id=TIME_CONFIG_ID, suggested="07:30", reason="周报改到早间生成")
        )
        assert run.decision.filed is False and run.pending is None

    def test_autonomous_auto_applies_and_notifies_via_l5(self, rig: M4Rig) -> None:
        # 协作档（缺省）先立案；切自主档后接受 → 走生效路径并发告知
        pending = rig.m4.l6.change_flow.submit(
            ChangeProposal(config_id=TIME_CONFIG_ID, suggested="07:30", reason="周报改到早间生成")
        ).pending
        assert pending is not None
        rig.m4.l6.authorization.set_tier("autonomous")
        before = len(rig.channels["desktop"].payloads)

        run = rig.m4.l6.change_flow.accept(pending.pending_id)
        assert run.applied is True and run.change is not None
        assert run.change.notified is True                       # 铁律 6：自动变更显式告知
        assert len(rig.channels["desktop"].payloads) > before    # 经 L5 通道投出
        assert rig.m4.l6.reports.report_time().strftime("%H:%M") == "07:30"

    def test_autonomous_low_risk_change_auto_applies_on_submit(self, rig: M4Rig) -> None:
        rig.m4.l6.authorization.set_tier("autonomous")
        run = rig.m4.l6.change_flow.submit(
            ChangeProposal(config_id=TIME_CONFIG_ID, suggested="07:30", reason="周报改到早间生成")
        )
        assert run.applied is True and run.change is not None and run.change.notified is True

    def test_memory_policy_is_never_autonomous(self, rig: M4Rig) -> None:
        """Memory 内容 / 策略在任何档位下都不自动（[08 §7] 红线）。"""
        rig.m4.l6.authorization.set_tier("autonomous")
        decision = rig.m4.l6.change_flow.gate(
            ChangeProposal(config_id="memory.confidence-baseline", suggested=0.5, reason="调整基线")
        )
        assert decision.auto is False


# ─────────────────────────── GWT-5 变更回滚 ───────────────────────────


class TestGwt5Rollback:
    def test_rollback_replays_the_previous_value(self, rig: M4Rig) -> None:
        rig.m4.l6.authorization.set_tier("autonomous")
        run = rig.m4.l6.change_flow.submit(
            ChangeProposal(config_id=TIME_CONFIG_ID, suggested="07:30", reason="周报改到早间生成")
        )
        assert run.applied is True
        change_id = run.change.change_id

        rolled = rig.m4.l6.change_flow.rollback(change_id)
        assert rolled.applied is True and rolled.change is not None
        assert rig.m4.l6.reports.report_time().strftime("%H:%M") == "20:00"
        # 历史里新旧两条都在
        ids = {c.change_id for c in rig.m4.l6.change_flow.history()}
        assert {change_id, rolled.change.change_id} <= ids


# ─────────────────────────── GWT-6 出厂重置 ───────────────────────────


class TestGwt6FactoryReset:
    def test_three_confirmations_are_a_hard_gate(self, rig: M4Rig) -> None:
        withheld = rig.m4.l6.factory_reset.request(2)
        assert withheld.performed is False and withheld.reset_id == ""

    def test_reset_keeps_memory_untouched(self, rig: M4Rig) -> None:
        # 先制造一条演进变更与一段记忆
        rig.m4.l6.authorization.set_tier("autonomous")
        rig.m4.l6.change_flow.submit(
            ChangeProposal(config_id=TIME_CONFIG_ID, suggested="07:30", reason="周报改到早间生成")
        )
        rig.m4.m3.m1.writer.add_node(checked_node(
            type="thesis", memory_node_id=new_node_id(), confidence=0.7,
            source="user_stated", privacy_level="private",
            created_at=NOW, updated_at=NOW, subject="sh.600000", subject_kind="stock",
            view="长期看好", stated_at=NOW,
        ))
        before = _memory_listing(rig)

        outcome = rig.m4.l6.factory_reset.request(3)
        assert outcome.performed is True
        assert rig.m4.l6.authorization.tier() == "collaborative"   # 档位复位
        assert _memory_listing(rig) == before                      # Memory 逐字节不变


# ─────────────────────── GWT-7 导出 → 干净环境导入 ───────────────────────


class TestGwt7ExportImportRoundTrip:
    def test_export_then_import_into_a_clean_root(self, rig: M4Rig, tmp_path: Path) -> None:
        _register_custom_skill(rig)
        container = rig.m4.eco.exporter.export_skill(
            CUSTOM_SKILL_ID, author="tester", sharer="alice",
        )
        blob = container.to_bytes()

        clean = seeded_m4(tmp_path / "clean")
        plan = clean.m4.eco.importer.inspect(blob, received_from="alice")
        outcome = clean.m4.eco.importer.install(plan, confirmed_by=IMPORT_CONFIRMATION)

        assert outcome.installed_id == CUSTOM_SKILL_ID
        installed = clean.m4.m3.m1.runtime.skills.get(CUSTOM_SKILL_ID)
        assert installed.source == "imported"
        # 来源追溯链完整（分享者 / 时间 / 校验和 / 出处链）
        assert plan.provenance.sharer == "alice"
        assert plan.provenance.checksum == container.checksum()
        assert plan.what_it_wants()[-1].startswith("来源追溯：")


# ─────────────────────── GWT-8 恶意 / 越权导入被拦截 ───────────────────────


class TestGwt8ImportIsBlocked:
    def test_corrupt_file_is_reported_and_not_installed(self, rig: M4Rig) -> None:
        with pytest.raises(ShareFormatError):
            rig.m4.eco.importer.inspect(b"{ not a container }")

    def test_tampered_checksum_is_rejected(self, rig: M4Rig) -> None:
        _register_custom_skill(rig)
        document = rig.m4.eco.exporter.export_skill(
            CUSTOM_SKILL_ID, author="tester", sharer="alice",
        ).to_document()
        document["payload"]["description"] = "被篡改的说明"       # 改载荷、校验和不动
        with pytest.raises(ShareFormatError):
            rig.m4.eco.importer.inspect(json.dumps(document).encode("utf-8"))

    def test_missing_dependency_is_reported_not_auto_installed(self, rig: M4Rig) -> None:
        descriptor = SkillDescriptor(
            skill_id="sk_m4_needs_missing_v1.0", name="依赖缺失演示",
            description="依赖一个本地没有的能力", input_schema={}, output_schema={},
            parameters=(), dependencies=("sk_m4_absent_v1.0",), source="user-built",
            provenance=Provenance(), permissions=(), offline_level="full",
            version_policy="follow-latest",
        )
        container = ShareContainer.pack(
            descriptor, author="tester", sharer="alice",
            dependencies=("sk_m4_absent_v1.0",), created_at=NOW,
        )
        plan = rig.m4.eco.importer.inspect(container.to_bytes())
        assert [gap.skill_id for gap in plan.missing] == ["sk_m4_absent_v1.0"]
        assert any("缺失依赖" in line for line in plan.what_it_wants())

    def test_private_memory_fragment_is_refused(self, rig: M4Rig) -> None:
        node = checked_node(
            type="thesis", memory_node_id=new_node_id(), confidence=0.7,
            source="user_stated", privacy_level="private",
            created_at=NOW, updated_at=NOW, subject="sh.600000", subject_kind="stock",
            view="私有判断", stated_at=NOW,
        )
        container = ShareContainer.pack(
            FragmentPayload(nodes=(node,)), author="tester", sharer="alice",
            dependencies=(), created_at=NOW,
        )
        plan = rig.m4.eco.importer.inspect(container.to_bytes())
        with pytest.raises(ShareImportError):
            rig.m4.eco.importer.install(plan, confirmed_by=IMPORT_CONFIRMATION)

    def test_unapproved_permissions_block_install(self, rig: M4Rig) -> None:
        descriptor = SkillDescriptor(
            skill_id="sk_m4_wants_file_v1.0", name="越权演示",
            description="声明一项未批准的本地文件权限", input_schema={}, output_schema={},
            parameters=(), dependencies=(), source="user-built", provenance=Provenance(),
            permissions=("local_read:<notes/>",), offline_level="full",
            version_policy="follow-latest",
        )
        container = ShareContainer.pack(
            descriptor, author="tester", sharer="alice", dependencies=(), created_at=NOW,
        )
        plan = rig.m4.eco.importer.inspect(container.to_bytes())
        base, _ = parse_skill_id("sk_m4_wants_file_v1.0")
        assert rig.m4.m3.m1.runtime.skill_permissions.pending_permissions(base)  # 仅登记待批
        with pytest.raises(ShareImportError):
            rig.m4.eco.importer.install(plan, confirmed_by=IMPORT_CONFIRMATION)


# ─────────────────── GWT-9 训练对话与回访在对话面的接线 ───────────────────


class TestGwt9TrainingDialog:
    def test_train_destination_is_wired_to_the_l6_protocol(self, rig: M4Rig) -> None:
        m4 = rig.m4
        turn = m4.chat.post("我想训练你：只看公告面的信息")
        assert turn.needs_confirmation is True
        outcome = m4.chat.confirm_and_dispatch(
            turn.session_id, values={"correction": "只看公告面的信息"},
        )
        assert outcome.wired is True and outcome.training is not None
        session = outcome.training.session
        assert session is not None and rig.training.calls        # 真经注入的理解端口

        confirmed = m4.l6.training.confirm(session)
        recorded = m4.l6.training.record(confirmed)
        assert recorded.pattern_node_id                            # 经记忆写入面新增 pattern 节点
        assert recorded.callback.training_id == session.training_id

    def test_pending_callback_surfaces_on_the_next_turn_then_is_acknowledged(
        self, rig: M4Rig
    ) -> None:
        m4 = rig.m4
        turn = m4.chat.post("我想训练你：只看公告面的信息")
        dispatched = m4.chat.confirm_and_dispatch(
            turn.session_id, values={"correction": "只看公告面的信息"},
        )
        m4.l6.training.record(m4.l6.training.confirm(dispatched.training.session))

        reply = m4.chat.turn({"action": "post", "text": "你好"})
        assert reply["callbacks"], "下次对话应带上待回访项"
        assert reply["callbacks"][0]["text"]
        assert m4.l6.training.callbacks() == ()                    # 提及即置「已提及」，不重复问


# ─────────────────── GWT-10 到点驱动（周报）与常驻循环契约 ───────────────────


class TestGwt10Tick:
    def test_tick_still_runs_the_m3_duty_cycle(self, rig: M4Rig) -> None:
        result = rig.m4.tick(NOW)
        assert result.report is not None                           # M3 的日报面照常
        assert result.weekly_report is None                        # 未到点 ⇒ 不带周报

    def test_l6_face_is_reachable_through_the_runtime(self, rig: M4Rig) -> None:
        assert rig.m4.training_callbacks() == ()

    def test_weekly_publish_requires_the_due_time(self, rig: M4Rig) -> None:
        """未到点 ⇒ 不生成、不投出周报（到点判定是纯函数，[08 §2]）。"""
        assert rig.m4.tick(NOW).weekly_report is None
        assert rig.m4.l6.reports.stored(week_key(NOW.date())) is None
