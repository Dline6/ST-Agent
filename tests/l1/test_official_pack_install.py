"""T-L1-004.1 测试：官方 Pack 装载接线与数据访问基件（03 §2）。

GWT 对照（任务文件 5 条）：
- GWT-1 装载即用：12 个 Skill 描述体落盘 + 12 个 skill_id 均注册执行器
- GWT-2 幂等装载：二次装载零新增、描述体逐字段不变
- GWT-3 数据不可用不编造：取数源 ``unavailable`` 原样成为执行结果
- GWT-4 空结果是合法结果：取数源 ``empty`` 原样透出，带原因
- GWT-5 取数只有一条通道：执行面源码不含 sqlite3 / L0 具体类依赖
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.storage import Store
from st_agent.l1.runner import SkillRunner
from st_agent.l1.skills import OFFICIAL_PACK, SkillRegistry
from st_agent.l1.skills.official import (
    MarketQuerySource,
    OfficialPackLoadError,
    empty_envelope,
    install_official_pack,
    ok_envelope,
    query_rows,
    snapshot_ref,
    uncovered_bases,
)

PASS = "correct horse battery staple"

ALL_BASES: tuple[str, ...] = tuple(seed["base"] for seed in OFFICIAL_PACK)
COGNITION_BASES = ALL_BASES[:6]


def _stamp() -> datetime:
    return datetime(2026, 9, 26, 15, 0, tzinfo=datetime.now().astimezone().tzinfo)


class FakeSource:
    """取数源（鸭子类型）：``query`` 恒返回同一信封，另记调用流水供断言。"""

    def __init__(self, envelope: ResultEnvelope, snapshot: str | None = "snap|k:1@T") -> None:
        self.envelope = envelope
        self._snapshot = snapshot
        self.calls: list[tuple[str, tuple]] = []

    def query(self, sql: str, params: tuple = ()) -> ResultEnvelope:
        self.calls.append((sql, tuple(params)))
        return self.envelope

    def snapshot_id(self) -> str:
        assert self._snapshot is not None
        return self._snapshot


def ok_source() -> FakeSource:
    return FakeSource(ResultEnvelope.ok({"columns": ["x"], "rows": [{"x": 1}]}, as_of=_stamp()))


def probe_factory(source):
    """探针执行器：只证「该 skill_id 已注册执行器」，不碰语义。"""

    def _fn(ctx, params):
        return ok_envelope({"probe": ctx.skill_id}, as_of=_stamp())

    return _fn


def passthrough_factory(source):
    """取数直通执行器：把 ``query_rows`` 的结果原样当执行结果（GWT-3/4 探针）。"""

    def _fn(ctx, params):
        got = query_rows(source, "SELECT 1")
        if isinstance(got, ResultEnvelope):
            return got
        return ok_envelope({"rows": [dict(r) for r in got.rows]}, as_of=got.as_of)

    return _fn


def full_table(factory=probe_factory) -> dict:
    return {base: factory for base in ALL_BASES}


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def registry(store: Store) -> SkillRegistry:
    return SkillRegistry(store)


@pytest.fixture()
def runner(store: Store, registry: SkillRegistry) -> SkillRunner:
    return SkillRunner(store, registry)


def pack_permissions(registry: SkillRegistry) -> tuple[str, ...]:
    """官方 Pack 声明的权限并集——依赖链上的 Skill 各自声明自己的权限，
    调用方一次批准整套即整链可执行（01 §10 逐项核对）。"""
    return tuple(sorted({p for d in registry.list_all() for p in d.permissions}))


def skill_ids(registry: SkillRegistry) -> tuple[str, ...]:
    return tuple(d.skill_id for d in registry.list_all())


# ───────────────────────── GWT-1 装载即用 ─────────────────────────


class TestGwt1InstallRunnable:
    def test_install_seeds_and_registers_all_twelve(self, registry, runner):
        added = install_official_pack(
            registry, runner, market_query=ok_source(), executors=full_table()
        )
        assert len(added) == 12
        assert len(skill_ids(registry)) == 12
        assert uncovered_bases(full_table()) == ()

    def test_every_skill_runs_no_missing_executor(self, registry, runner):
        install_official_pack(
            registry, runner, market_query=ok_source(), executors=full_table()
        )
        for skill_id in skill_ids(registry):
            out = runner.run(
                skill_id, {}, approved_permissions=pack_permissions(registry)
            )
            assert out.envelope.status == "ok", (
                f"{skill_id} 未注册执行器：{out.envelope.reason}"
            )

    def test_partial_table_reports_uncovered(self, registry, runner):
        partial = {base: probe_factory for base in COGNITION_BASES}
        install_official_pack(
            registry, runner, market_query=ok_source(), executors=partial
        )
        assert uncovered_bases(partial) == ALL_BASES[6:]
        assert len(skill_ids(registry)) == 12  # 描述体仍播种齐全，只是执行器少 6 个

    def test_bad_market_query_rejected(self, registry, runner):
        with pytest.raises(OfficialPackLoadError):
            install_official_pack(
                registry, runner, market_query=object(), executors=full_table()
            )

    def test_non_callable_factory_rejected(self, registry, runner):
        with pytest.raises(OfficialPackLoadError):
            install_official_pack(
                registry, runner, market_query=ok_source(),
                executors={ALL_BASES[0]: "not-callable"},
            )


# ───────────────────────── GWT-2 幂等装载 ─────────────────────────


class TestGwt2Idempotent:
    def test_second_install_adds_nothing(self, registry, runner):
        table = full_table()
        first = install_official_pack(
            registry, runner, market_query=ok_source(), executors=table
        )
        second = install_official_pack(
            registry, runner, market_query=ok_source(), executors=table
        )
        assert len(first) == 12
        assert second == ()

    def test_descriptors_untouched_by_install(self, registry, runner):
        install_official_pack(
            registry, runner, market_query=ok_source(), executors=full_table()
        )
        before = {d.skill_id: d.model_dump_json() for d in registry.list_all()}
        install_official_pack(
            registry, runner, market_query=ok_source(), executors=full_table()
        )
        after = {d.skill_id: d.model_dump_json() for d in registry.list_all()}
        assert after == before

    def test_second_install_keeps_executors_working(self, registry, runner):
        table = full_table()
        install_official_pack(registry, runner, market_query=ok_source(), executors=table)
        install_official_pack(registry, runner, market_query=ok_source(), executors=table)
        for skill_id in skill_ids(registry):
            out = runner.run(
                skill_id, {}, approved_permissions=pack_permissions(registry)
            )
            assert out.envelope.status == "ok"


# ───────────────────────── GWT-3/4 信封透传 ─────────────────────────


class TestGwt3Gwt4EnvelopePassthrough:
    def test_unavailable_passthrough_verbatim(self, registry, runner):
        stamp = _stamp()
        source = FakeSource(
            ResultEnvelope.unavailable(
                "本地市场数据库尚未建立（首次同步未完成）", last_updated_at=stamp
            )
        )
        install_official_pack(
            registry, runner, market_query=source,
            executors={base: passthrough_factory for base in ALL_BASES},
        )
        skill_id = next(iter(skill_ids(registry)))
        out = runner.run(
            skill_id, {}, approved_permissions=pack_permissions(registry)
        )
        assert out.envelope.status == "unavailable"
        assert out.envelope.reason == "本地市场数据库尚未建立（首次同步未完成）"
        assert out.envelope.last_updated_at == stamp

    def test_empty_passthrough_verbatim(self, registry, runner):
        source = FakeSource(ResultEnvelope.empty("查询合法但结果为空（该条件无匹配行）"))
        install_official_pack(
            registry, runner, market_query=source,
            executors={base: passthrough_factory for base in ALL_BASES},
        )
        skill_id = next(iter(skill_ids(registry)))
        out = runner.run(
            skill_id, {}, approved_permissions=pack_permissions(registry)
        )
        assert out.envelope.status == "empty"
        assert out.envelope.reason == "查询合法但结果为空（该条件无匹配行）"

    def test_query_rows_flattens_ok_rows(self):
        got = query_rows(ok_source(), "SELECT 1")
        assert not isinstance(got, ResultEnvelope)
        assert got.rows == ({"x": 1},)
        assert got.as_of == _stamp()

    def test_query_rows_rejects_non_envelope(self):
        class Bad:
            def query(self, sql, params=()):
                return {"rows": []}

        with pytest.raises(OfficialPackLoadError):
            query_rows(Bad(), "SELECT 1")

    def test_query_rows_rejects_ok_without_rows(self):
        source = FakeSource(ResultEnvelope.ok({"columns": ["x"]}))
        with pytest.raises(OfficialPackLoadError):
            query_rows(source, "SELECT 1")


# ───────────────────────── 取数源口径与证据引用 ─────────────────────────


class TestSourceShapeAndEvidence:
    def test_market_db_qualifies_as_source(self):
        from st_agent.l0.market import MarketDb

        class _Store:
            def list_files(self, partition):  # pragma: no cover - 仅证形态
                return ()

        assert isinstance(MarketDb(_Store()), MarketQuerySource)

    def test_object_without_query_rejected(self):
        assert not isinstance(object(), MarketQuerySource)

    def test_snapshot_ref_is_bounded_and_deterministic(self):
        first = snapshot_ref(FakeSource(ResultEnvelope.empty("x"), snapshot="snap|a:1@T"))
        second = snapshot_ref(FakeSource(ResultEnvelope.empty("x"), snapshot="snap|a:1@T"))
        other = snapshot_ref(FakeSource(ResultEnvelope.empty("x"), snapshot="snap|a:2@T"))
        assert first is not None and second is not None and other is not None
        assert first == second
        assert first != other
        assert first.kind == "dataset_snapshot_id"
        assert first.ref == "snap_" + hashlib.sha256("snap|a:1@T".encode()).hexdigest()[:20]
        assert len(first.ref) <= 128

    def test_snapshot_ref_none_without_provider(self):
        class Bare:
            def query(self, sql, params=()):
                return ResultEnvelope.empty("x")

        assert snapshot_ref(Bare()) is None


# ───────────────────────── GWT-5 取数只有一条通道 ─────────────────────────


class TestGwt5SingleDataChannel:
    def test_official_package_has_no_direct_datasource_access(self):
        """执行面不得 import 数据面实现（sqlite3 / L0 具体模块）——只经基件的 query 入口。"""
        import ast

        import st_agent.l1.skills.official as pkg

        directory = Path(pkg.__file__).parent
        offenders = []
        for path in sorted(directory.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                module = None
                if isinstance(node, ast.ImportFrom) and node.level == 0:
                    module = node.module or ""
                elif isinstance(node, ast.Import):
                    module = ",".join(alias.name for alias in node.names)
                if module is None:
                    continue
                if module == "sqlite3" or module.startswith("st_agent.l0"):
                    offenders.append(f"{path.name}:{node.lineno} import {module}")
        assert not offenders, (
            "官方 Pack 执行面只能经基件取数，不得直连数据面：\n  " + "\n  ".join(offenders)
        )
