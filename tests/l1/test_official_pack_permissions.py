"""T-L1-009.3 测试：官方 Pack 权限声明校正（01 §10 的声明语义范围）。

GWT 对照（任务文件 3 条）：
- GWT-1 官方 Pack 无需批准即可执行（含原 `net_access` 的两条）
- GWT-2 声明字段与校验仍在（不因清空而废掉权限模型）
- GWT-3 口径可查（文档侧由 `verify_docs.py --strict` 承载）
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.storage import Store
from st_agent.l1.runtime import L1Runtime, build_l1_runtime
from st_agent.l1.skills import OFFICIAL_PACK, SkillValidationError

PASS = "correct horse battery staple"
NOW = datetime(2026, 9, 27, 9, 0, tzinfo=timezone(timedelta(hours=8)))
STOCK_WATCH = "sk_stock_watch_v1.0"


class _FakeMarketQuery:
    def query(self, sql: str, params: tuple = ()) -> ResultEnvelope:
        return ResultEnvelope.empty("测试数据面为空")


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def runtime(store: Store) -> L1Runtime:
    return build_l1_runtime(store, market_query=_FakeMarketQuery())


class TestGwt1OfficialPackNeedsNoApproval:
    def test_no_seed_declares_any_permission(self):
        assert len(OFFICIAL_PACK) == 12
        assert [s["base"] for s in OFFICIAL_PACK if s["permissions"]] == []

    def test_ledger_stays_empty_after_seeding(self, runtime: L1Runtime, store: Store):
        """播种**不**写批准记录（无声明即无待批事项）。"""
        assert [n for n in store.list_files("config")
                if n.startswith("skill-permissions/")] == []

    def test_scheduled_official_skill_runs_without_approval(self, runtime: L1Runtime):
        """`sk_stock_watch` 曾声明 `net_access`，清空后不再被硬门拦住。"""
        run = runtime.scheduler.trigger(STOCK_WATCH, now=NOW)

        assert "缺少已批准权限" not in (run.envelope.reason or "")
        assert run.status == "ok"


class TestGwt2DeclarationModelStaysIntact:
    def test_seed_still_goes_through_permission_validation(self, runtime: L1Runtime):
        with pytest.raises(SkillValidationError):
            runtime.skills.register(
                "sk_bad_perm", version="1.0", name="声明非法",
                description="权限声明语法非法",
                permissions=("net_access",),   # 缺作用域，§10 语法非法
            )

    def test_a_declaring_skill_still_requires_approval(self, runtime: L1Runtime):
        """清的是**官方 Pack 的声明**，不是权限模型本身。"""
        runtime.skills.register(
            "sk_still_declares", version="1.0", name="仍然声明权限",
            description="自建且声明了权限",
            parameters=(dict(name="frequency_minutes", type="integer", default=30,
                             min_value=5, max_value=1440, description="频率"),),
            permissions=("local_read:<data/other/**>",), offline_level="full",
        )
        runtime.runner.register_executor(
            "sk_still_declares_v1.0", lambda ctx, params: ResultEnvelope.ok({}))

        run = runtime.scheduler.trigger("sk_still_declares_v1.0", now=NOW)

        assert run.status == "failed"
        assert "缺少已批准权限" in (run.envelope.reason or "")
