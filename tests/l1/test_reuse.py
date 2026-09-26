"""T-L1-001.4 测试：03 §1.3 输出复用 + 新鲜度查询。

GWT 对照（任务文件 3 条）：
- GWT-8 数据不可用不编造：``ctx.freshness`` 判定 → ``unavailable_envelope``
  带**真实**最后更新时间（取自判定，不臆造），且不可用输出不登记为可复用
- GWT-9 空结果结构化：``empty`` 必带 reason、可登记、可复用、reason 往返不丢
- GWT-F 过期引用自决：``reuse`` 返回 ``StalenessVerdict``（只给判定不给建议）

另覆盖假设 A1–A4、A8、A9 的机器可验证面。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.time_events import StalenessVerdict
from st_agent.l1.reuse import (
    DEFAULT_MAX_AGE,
    OUTPUT_PREFIX,
    OutputNotRegisteredError,
    OutputRegistry,
    ReuseValidationError,
    age_based_verdict,
    unavailable_envelope,
)
from st_agent.l1.reuse.freshness import MarketFreshnessOracle
from st_agent.l1.runner import SkillRunner
from st_agent.l1.skills import SkillRegistry
from st_agent.l0.storage import Store

PASS = "correct horse battery staple"

BASE = "sk_reuse_demo"
DEMO = f"{BASE}_v1.0"


def now() -> datetime:
    return datetime.now().astimezone()


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def registry(store: Store) -> SkillRegistry:
    reg = SkillRegistry(store)
    reg.register(
        BASE, name="输出复用演示技能",
        description="演示一次成功输出、空结果与不可用分支的登记与复用",
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {}},
    )
    return reg


@pytest.fixture()
def outputs(store: Store) -> OutputRegistry:
    return OutputRegistry(store)


@pytest.fixture()
def runner(store: Store, registry: SkillRegistry, outputs: OutputRegistry) -> SkillRunner:
    return SkillRunner(store, registry, outputs=outputs)


def index_files(store: Store) -> tuple[str, ...]:
    return tuple(f for f in store.list_files("execution_log")
                 if f.startswith(OUTPUT_PREFIX))


def execute(runner: SkillRunner, fn):
    runner.register_executor(DEMO, fn)
    return runner.run(DEMO)


def run_with(runner: SkillRunner, fn) -> ResultEnvelope:
    return execute(runner, fn).envelope


class FakeOracle:
    """固定判定的新鲜度来源（替身 L0 §05 口径）。"""

    def __init__(self, verdict: StalenessVerdict) -> None:
        self.verdict_value = verdict
        self.calls: list[str] = []

    def verdict(self, domain: str) -> StalenessVerdict:
        self.calls.append(domain)
        return self.verdict_value


def verdict_for(domain: str, *, stale: bool, at: datetime) -> StalenessVerdict:
    return StalenessVerdict(
        stale=stale, last_updated_at=at,
        detail=f"{domain} 域判定（替身）",
    )


# ───────────────────────── GWT-8 数据不可用不编造 ─────────────────────────


class TestGwt8UnavailableNotFabricated:
    def test_executor_builds_unavailable_from_freshness_verdict(self, store, registry):
        stamp = now() - timedelta(days=2)
        oracle = FakeOracle(verdict_for("kline", stale=True, at=stamp))
        runner = SkillRunner(store, registry, freshness=oracle)

        def fn(ctx, params):
            assert ctx.freshness is not None
            return unavailable_envelope(
                ctx.freshness.verdict("kline"), "行情数据停留于最后成功同步")

        envelope = run_with(runner, fn)

        assert envelope.status == "unavailable"
        assert envelope.data is None
        # 「最后更新时间 T」逐字来自判定，不是执行器自己造的
        assert envelope.last_updated_at == stamp
        assert envelope.reason and "行情数据停留" in envelope.reason
        assert oracle.calls == ["kline"]

    def test_unavailable_output_is_not_registered(self, store, registry, outputs):
        oracle = FakeOracle(verdict_for("kline", stale=True, at=now()))
        runner = SkillRunner(store, registry, outputs=outputs, freshness=oracle)

        outcome = execute(
            runner,
            lambda ctx, params: unavailable_envelope(
                ctx.freshness.verdict("kline"), "数据源不可用"),
        )

        assert outcome.envelope.status == "unavailable"
        assert index_files(store) == ()
        assert outputs.is_registered(outcome.skill_run_id) is False

    def test_market_oracle_delegates_to_l0_source(self):
        stamp = now()

        class FakeSync:
            def __init__(self) -> None:
                self.seen: list[str] = []

            def freshness_verdict(self, domain: str) -> StalenessVerdict:
                self.seen.append(domain)
                return verdict_for(domain, stale=False, at=stamp)

        sync = FakeSync()
        oracle = MarketFreshnessOracle(sync)
        got = oracle.verdict("sector")
        assert got.stale is False and got.last_updated_at == stamp
        assert sync.seen == ["sector"]

    def test_market_oracle_rejects_source_without_l0_verdict(self):
        class NotAnOracle:
            pass

        with pytest.raises(ReuseValidationError):
            MarketFreshnessOracle(NotAnOracle())


# ───────────────────────── GWT-9 空结果结构化 ─────────────────────────


class TestGwt9StructuredEmpty:
    def test_empty_is_registered_and_reusable(self, store, registry, outputs, runner):
        as_of = now()
        outcome = execute(
            runner, lambda ctx, params: ResultEnvelope.empty(
                "今日全市场无退市高危信号", as_of=as_of))

        assert outcome.envelope.status == "empty" and outcome.envelope.data is None
        view = outputs.reuse(outcome.skill_run_id)
        assert view.output.status == "empty"
        assert view.envelope.status == "empty"
        # reason 往返不丢（「不得返回裸空」，01 §5）
        assert view.envelope.reason == "今日全市场无退市高危信号"
        assert view.envelope.data is None

    def test_empty_registration_records_as_of(self, store, registry, runner):
        as_of = now()
        outcome = execute(
            runner, lambda ctx, params: ResultEnvelope.empty("无信号", as_of=as_of))

        record = OutputRegistry(store).get(outcome.skill_run_id)
        assert record.status == "empty" and record.as_of == as_of


# ───────────────────────── GWT-F 过期引用自决 ─────────────────────────


class TestGwtFStaleReference:
    def _run_with_as_of(self, runner, as_of: datetime | None) -> str:
        return execute(
            runner, lambda ctx, params: ResultEnvelope.ok({"n": 1}, as_of=as_of),
        ).skill_run_id

    def test_expired_reference_is_stale_by_age(self, runner, outputs):
        run_id = self._run_with_as_of(runner, now() - timedelta(days=3))
        view = outputs.reuse(run_id)

        assert view.verdict.stale is True
        assert view.verdict.last_updated_at == view.output.as_of
        assert "超出预期新鲜度" in view.verdict.detail
        # 只给判定不给建议：判定里不含「应重算」类的统一建议
        assert "重算" not in view.verdict.detail

    def test_fresh_reference_is_not_stale_by_age(self, runner, outputs):
        run_id = self._run_with_as_of(runner, now())
        view = outputs.reuse(run_id)

        assert view.verdict.stale is False
        assert "在预期新鲜度" in view.verdict.detail

    def test_reference_without_as_of_is_conservatively_stale(self, runner, outputs):
        run_id = self._run_with_as_of(runner, None)
        view = outputs.reuse(run_id)

        assert view.output.as_of is None
        assert view.verdict.stale is True
        assert "未带 as_of" in view.verdict.detail
        assert view.verdict.last_updated_at == view.output.registered_at

    def test_l0_verdict_flags_reference_older_than_current_face(self, store, registry):
        stamp = now()
        oracle = FakeOracle(verdict_for("kline", stale=False, at=stamp))
        outputs = OutputRegistry(store, freshness=oracle)
        runner = SkillRunner(store, registry, outputs=outputs)
        run_id = self._run_with_as_of(runner, stamp - timedelta(days=5))

        view = outputs.reuse(run_id, domain="kline")

        assert view.verdict.stale is True
        assert view.verdict.last_updated_at == stamp   # 取自 L0 判定
        assert "引用快照早于当前数据面" in view.verdict.detail
        assert oracle.calls == ["kline"]

    def test_l0_verdict_passes_through_when_reference_up_to_date(self, store, registry):
        stamp = now()
        oracle = FakeOracle(verdict_for("sector", stale=True, at=stamp))
        outputs = OutputRegistry(store, freshness=oracle)
        runner = SkillRunner(store, registry, outputs=outputs)
        run_id = self._run_with_as_of(runner, stamp)

        view = outputs.reuse(run_id, domain="sector")

        # 引用不落伍 → 原样透出 L0 判定（不为「引用新」而抹掉数据面缺陷）
        assert view.verdict == oracle.verdict_value

    def test_domain_without_oracle_falls_back_to_age(self, runner, outputs):
        run_id = self._run_with_as_of(runner, now())
        view = outputs.reuse(run_id, domain="kline")

        assert "未接 L0 同步状态" in view.verdict.detail

    def test_freshness_is_reachable_without_loading_envelope(self, store, registry):
        stamp = now() - timedelta(days=3)
        outputs = OutputRegistry(store)
        runner = SkillRunner(store, registry, outputs=outputs)
        run_id = self._run_with_as_of(runner, stamp)

        assert outputs.freshness(run_id).stale is True


# ───────────────────────── 登记面（A1 / A2 / 索引形态） ─────────────────────────


class TestRegistration:
    def test_index_is_payload_free(self, store, registry, runner):
        payload = {"heavy": [1, 2, 3]}
        runner.register_executor(
            DEMO, lambda ctx, params: ResultEnvelope.ok(payload, as_of=now()))
        run_id = runner.run(DEMO).skill_run_id

        raw = json.loads(store.get("execution_log", f"{OUTPUT_PREFIX}{run_id}.json"))
        assert set(raw) == {"skill_run_id", "skill_id", "status", "as_of", "registered_at"}
        assert raw["skill_id"] == DEMO and raw["status"] == "ok"

    def test_payload_is_single_sourced_from_skill_run(self, store, registry, runner, outputs):
        payload = {"heavy": [1, 2, 3]}
        runner.register_executor(
            DEMO, lambda ctx, params: ResultEnvelope.ok(payload, as_of=now()))
        run_id = runner.run(DEMO).skill_run_id

        view = outputs.reuse(run_id)
        assert view.envelope.data == payload
        # 载荷只存一份：skill-run 记录里才有
        run_raw = store.get("execution_log", f"skill-run/{run_id}.json")
        assert b'"heavy"' in run_raw

    def test_non_reusable_status_rejected(self, outputs):
        for envelope in (
            ResultEnvelope.failed("执行失败", log_ref="skill-run/x.json"),
            ResultEnvelope.validation_failed("参数非法"),
            ResultEnvelope.dependency_failed("上游失败"),
            ResultEnvelope.unavailable("数据不可用", last_updated_at=now()),
        ):
            with pytest.raises(ReuseValidationError):
                outputs.register("run_" + "0" * 20, DEMO, envelope)

    def test_unknown_run_id_raises(self, outputs):
        with pytest.raises(OutputNotRegisteredError):
            outputs.get("run_" + "1" * 20)

    def test_index_absent_but_skill_run_present_still_raises(self, store, registry):
        plain = SkillRunner(store, registry)        # 未接 outputs → 无索引
        plain.register_executor(
            DEMO, lambda ctx, params: ResultEnvelope.ok({"n": 1}))
        run_id = plain.run(DEMO).skill_run_id
        assert store.get("execution_log", f"skill-run/{run_id}.json")

        outputs = OutputRegistry(store)
        assert outputs.is_registered(run_id) is False
        with pytest.raises(OutputNotRegisteredError):
            outputs.reuse(run_id)

    def test_illegal_ids_rejected(self, outputs):
        ok_env = ResultEnvelope.ok({"n": 1})
        with pytest.raises(ReuseValidationError):
            outputs.register("not-a-run-id", DEMO, ok_env)
        with pytest.raises(ReuseValidationError):
            outputs.register("run_" + "2" * 20, "not-a-skill-id", ok_env)


# ───────────────────────── runner 接线（A3 / A4） ─────────────────────────


class TestRunnerWiring:
    def test_without_outputs_behavior_unchanged(self, store, registry):
        runner = SkillRunner(store, registry)
        seen: dict = {}

        def fn(ctx, params):
            seen["freshness"] = ctx.freshness
            return ResultEnvelope.ok({"n": 1})

        envelope = run_with(runner, fn)

        assert envelope.status == "ok"
        assert index_files(store) == ()
        assert seen["freshness"] is None

    def test_ctx_carries_freshness_handle(self, store, registry, outputs):
        oracle = FakeOracle(verdict_for("kline", stale=False, at=now()))
        runner = SkillRunner(store, registry, outputs=outputs, freshness=oracle)
        seen: dict = {}

        def fn(ctx, params):
            seen["freshness"] = ctx.freshness
            return ResultEnvelope.ok({"n": 1})

        run_with(runner, fn)
        assert seen["freshness"] is oracle

    def test_registration_failure_turns_envelope_failed(self, store, registry):
        class BrokenOutputs:
            def register(self, *args, **kwargs):
                raise ReuseValidationError("输出分区不可写")

        runner = SkillRunner(store, registry, outputs=BrokenOutputs())
        envelope = run_with(runner, lambda ctx, params: ResultEnvelope.ok({"n": 1}))

        assert envelope.status == "failed"
        assert envelope.log_ref and envelope.log_ref.startswith("skill-run/")
        assert "输出登记失败" in envelope.reason

    def test_failed_run_is_not_registered(self, store, registry, outputs, runner):
        def boom(ctx, params):
            raise RuntimeError("内部错误")

        envelope = run_with(runner, boom)

        assert envelope.status == "failed"
        assert index_files(store) == ()
        assert "输出登记失败" not in envelope.reason


# ───────────────────────── 超龄兜底口径（A9 单元） ─────────────────────────


class TestAgeBasedVerdict:
    def test_within_max_age_is_fresh(self):
        stamp = now() - timedelta(hours=1)
        v = age_based_verdict(stamp, fallback_at=stamp, now=now())
        assert v.stale is False and v.last_updated_at == stamp

    def test_beyond_max_age_is_stale(self):
        stamp = now() - DEFAULT_MAX_AGE - timedelta(minutes=1)
        v = age_based_verdict(stamp, fallback_at=stamp, now=now())
        assert v.stale is True and "超出预期新鲜度" in v.detail

    def test_missing_as_of_uses_fallback_stamp(self):
        fallback = now() - timedelta(hours=2)
        v = age_based_verdict(None, fallback_at=fallback)
        assert v.stale is True and v.last_updated_at == fallback

    def test_naive_datetime_rejected(self):
        naive = datetime(2026, 9, 1, 12, 0, 0)
        with pytest.raises(ReuseValidationError):
            age_based_verdict(naive, fallback_at=naive, now=now())
        with pytest.raises(ReuseValidationError):
            age_based_verdict(None, fallback_at=naive)

    def test_unavailable_envelope_uses_verdict_stamp(self):
        stamp = now() - timedelta(days=1)
        v = verdict_for("kline", stale=True, at=stamp)
        envelope = unavailable_envelope(v, "行情数据不可用")

        assert envelope.status == "unavailable"
        assert envelope.last_updated_at == stamp
        assert envelope.reason == "行情数据不可用"
