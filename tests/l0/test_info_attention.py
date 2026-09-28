"""T-L0-015.1 测试：关注面注入通道与 `coverage=watch` 契约。

GWT 对照（任务文件 6 条）：

- GWT-1/2 **覆盖显式**——05 登记行 `coverage=watch` + 01/02/05 的 watch 语义
- GWT-3 **注入通道**——`watchlist` 由调用方注入并透传给抓取器；L0 不 import L2
- GWT-4 **三态语义**——`None`（调用方缺陷）/ `[]`（已知空集）/ 非空（采集）
- GWT-5 **默认禁用**——未注入 → 干净 `unavailable`（不刷 `failed`）
- GWT-6 **无回归**——见同批调整的 `test_info_sentiment.py` / `test_info_base.py`
"""

from __future__ import annotations

import re
from pathlib import Path

from info_helpers import FakeInfoFetcher
from st_agent.l0.info import ATTENTION_TASKS, INFO_TASKS, get_info_task

IRM = "info_sentiment_qa_irm"
SSE = "info_sentiment_qa_sse"
WINDOW = ("2026-09-01", "2026-09-30")
ROOT = Path(__file__).resolve().parents[2]
"""仓库根（本文件在 ``tests/l0/``）。"""


def _qa(code: str = "000001", *, answer: str | None = None) -> dict:
    return {"code": code, "question": "公司如何回应近期传闻？", "answer": answer,
            "answerer": "董事会办公室" if answer else None,
            "ask_time": "2026-09-20 10:30"}


class TestGwt12CoverageExplicit:
    def test_irm_is_declared_watch_coverage(self):
        spec = get_info_task(IRM)
        assert spec.coverage == "watch"
        assert spec.role == "parallel"
        assert IRM in ATTENTION_TASKS

    def test_attention_scope_is_derived_from_coverage(self):
        """关注面任务集由 `coverage` 派生——不是第二份手写清单，增删随注册表。"""
        assert ATTENTION_TASKS == tuple(
            t.task_key for t in INFO_TASKS if t.coverage == "watch")

    def test_registry_doc_declares_watch_for_irm(self):
        """05 登记行须显式写 `watch`（契约先行，铁律 8）。"""
        doc = (ROOT / "docs/数据库设计-BaoStock数据层/05-同步策略与新鲜度契约.md"
               ).read_text(encoding="utf-8")
        row = next(line for line in doc.splitlines()
                   if line.startswith(f"| {IRM} |"))
        assert re.search(r"\|\s*watch\s*\|", row), row
        assert "用户关注面" in row
        # 值域说明须登记 watch 的语义（非市场全域）
        assert re.search(r"`watch`（按用户关注面", doc)

    def test_platform_and_layer_contracts_declare_watch(self):
        c01 = (ROOT / "docs/技术架构-v2/01-平台共享契约.md").read_text(encoding="utf-8")
        assert "`watch`" in c01 and "空结果不等于全市场无数据" in c01
        c02 = (ROOT / "docs/技术架构-v2/02-L0-本地优先基座.md").read_text(encoding="utf-8")
        assert "coverage=watch" in c02 and "池外标的恒为空" in c02


class TestGwt3InjectionChannel:
    def test_watchlist_is_passed_through_to_fetcher(self, sync_factory):
        fake = FakeInfoFetcher({IRM: {"sentiment_qa": (_qa(),)}})
        env = sync_factory(fake).run_task(
            IRM, window=WINDOW, enabled={IRM: True},
            watchlist=["sz.000001", "sz.300750"])
        assert env.status == "ok"
        assert fake.code_calls == [(IRM, ("sz.000001", "sz.300750"))]

    def test_non_attention_task_gets_no_codes(self, sync_factory):
        fake = FakeInfoFetcher({SSE: {"sentiment_qa": (_qa("600000"),)}})
        sync_factory(fake).run_task(SSE, window=WINDOW)
        assert fake.code_calls == [(SSE, None)]

    def test_l0_info_never_imports_l2(self):
        """L0 **不得**向上读 L2——关注面只能由调用方注入（铁律 7 / D-037）。"""
        for name in ("sync.py", "fetch.py", "sources.py"):
            text = (ROOT / f"src/st_agent/l0/info/{name}").read_text(encoding="utf-8")
            assert "st_agent.l2" not in text, name


class TestGwt4ThreeStates:
    def test_missing_watchlist_is_a_caller_defect(self, sync_factory):
        env = sync_factory(FakeInfoFetcher({})).run_task(
            IRM, window=WINDOW, enabled={IRM: True})
        assert env.status == "validation_failed"
        assert "关注面" in env.reason

    def test_empty_watchlist_is_known_empty_not_missing(self, db, sync_factory):
        """`[]` ＝已接通道、关注面为空 → `empty` 且记 `ok`（已知空集 ≠ 缺失）。"""
        fake = FakeInfoFetcher({IRM: {"sentiment_qa": (_qa(),)}})
        env = sync_factory(fake).run_task(
            IRM, window=WINDOW, enabled={IRM: True}, watchlist=[])
        assert env.status == "empty"
        assert "关注面为空" in env.reason
        assert "不等于全市场无舆情" in env.reason
        assert fake.calls == []  # 空池 → 一个请求都不发
        row = db.query("SELECT last_status, last_error FROM sync_state"
                       " WHERE task_key=?", (IRM,)).data["rows"][0]
        assert row["last_status"] == "ok"
        assert "关注面为空" in row["last_error"]  # 留痕：为何是 0 行

    def test_empty_pool_does_not_stale_the_domain(self, db, sync_factory):
        """若空池记 `failed`，按 05 判据整个 `sentiment_qa` 域会恒 `stale`（假设 A2）。"""
        sync = sync_factory(FakeInfoFetcher({SSE: {"sentiment_qa": (_qa("600000"),)}}))
        sync.run_task(SSE, window=WINDOW)
        sync.run_task(IRM, window=WINDOW, enabled={IRM: True}, watchlist=[])
        verdict = sync.freshness_verdict("sentiment_qa")
        assert verdict.stale is False, verdict.detail

    def test_non_empty_pool_collects(self, db, sync_factory):
        env = sync_factory(FakeInfoFetcher({IRM: {"sentiment_qa": (_qa(),)}})).run_task(
            IRM, window=WINDOW, enabled={IRM: True}, watchlist=["sz.000001"])
        assert env.status == "ok"
        assert db.query("SELECT count(*) AS n FROM sentiment_qa"
                        ).data["rows"][0]["n"] == 1


class TestGwt5DefaultOff:
    def test_absent_watchlist_is_a_clean_unavailable(self, sync_factory):
        """未注入关注面 → **默认禁用**的干净 `unavailable`（不得每轮刷 `failed`）。"""
        env = sync_factory(FakeInfoFetcher({})).run_task(IRM, window=WINDOW)
        assert env.status == "unavailable"
        assert "关注面" in env.reason and "默认禁用" in env.reason

    def test_run_all_leaves_irm_in_clean_state(self, db, sync_factory):
        results = sync_factory(FakeInfoFetcher({})).run_all()
        assert results[IRM].status == "unavailable"
        row = db.query("SELECT last_status FROM sync_state WHERE task_key=?",
                       (IRM,)).data["rows"][0]
        assert row["last_status"] != "failed"

    def test_run_all_runs_irm_when_enabled_and_injected(self, db, sync_factory):
        fake = FakeInfoFetcher({IRM: {"sentiment_qa": (_qa(),)}})
        results = sync_factory(fake).run_all(
            enabled={IRM: True}, watchlist=["sz.000001"])
        assert results[IRM].status == "ok"
        # `run_all` 跑全部任务；只有关注面任务带 codes
        assert (IRM, ("sz.000001",)) in fake.code_calls
        assert (SSE, None) in fake.code_calls
