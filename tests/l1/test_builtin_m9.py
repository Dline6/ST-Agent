"""T-AGT-013 批测试：取数 / 同步 / 文档三条内置原语。

GWT 对照：
- 013.1 GWT-1：数据陈旧时触发同步并返回运行态；等待策略可指定
- 013.1 GWT-2：写类动作由出厂清单判必须协作（L6 用例另测）
- 013.1 GWT-3：已有运行中批次时再次触发 → 显式拒绝
- 013.2 GWT-1：按域取数 → 经既有 adapter、落缓存、更新 as_of
- 013.2 GWT-2：域未登记 adapter → `unavailable` 并点名
- 013.2 GWT-3：出网经 `EgressGateway`（引擎内既有路径；本组断言原语只经 runner）
- 013.3 GWT-1：三个面分别返回元数据 / 正文 / 检索结果
- 013.3 GWT-2：表在而文件缺 → 损坏提示，不返回空正文
- 013.3 GWT-3：文件在而表缺 → 孤儿并标可回收
"""

from __future__ import annotations

from pathlib import Path

import pytest

from st_agent.l0.info.docs import DocStore, LocalDocSource
from st_agent.l0.market.db import MarketDb
from st_agent.l0.storage import Store
from st_agent.l0.sync_runner import SyncBusyError
from st_agent.l1.builtin import SyncPort, install_builtins
from st_agent.l1.runner import SkillRunner
from st_agent.l1.skills import SkillRegistry

PASS = "correct horse battery staple"
DATA_SYNC = "sk_data_sync_v1.0"
DATA_FETCH = "sk_data_fetch_v1.0"
DOCS = "sk_docs_v1.0"


class FakeDataSync:
    """记录调用并按脚本返回批次状态的执行面替身（不触网、不落盘）。"""

    def __init__(self, *, sequence: list[dict] | None = None,
                 snapshot: dict | None = None, busy: bool = False) -> None:
        self.start_calls: list[tuple[list[dict], str]] = []
        self.fetch_calls: list[tuple[list[dict], str]] = []
        self._sequence = sequence or [
            {"job_id": "job-1", "state": "running", "order": ["bs_k_daily"],
             "auto_added": [], "estimate": {}, "results": {}, "current": "",
             "started_at": "2026-10-29T10:00:00+08:00", "finished_at": "",
             "error": "", "as_of": ""},
        ]
        self._polls = 0
        self._snapshot = snapshot
        self._busy = busy

    def start_items(self, items, *, custom_text=""):
        if self._busy:
            raise SyncBusyError("已有同步批次在跑（job-x）；请等它结束或取消")
        self.start_calls.append((items, custom_text))
        self._polls = 0
        return {"job_id": "job-1"}

    def job(self):
        job = self._sequence[min(self._polls, len(self._sequence) - 1)]
        self._polls += 1
        return job

    def fetch_items(self, items, *, custom_text=""):
        if self._busy:
            raise SyncBusyError("已有同步批次在跑（job-x）；请等它结束或取消")
        self.fetch_calls.append((items, custom_text))
        return self._snapshot or {
            "job_id": "job-1", "state": "done", "order": ["bs_k_daily"],
            "auto_added": ["bs_calendar"], "results": {
                "g1:bs_k_daily": {"status": "ok", "rows": 3,
                                  "watermark": "2026-09-25"},
            }, "error": "", "as_of": "2026-09-25T18:00:00+08:00",
        }


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def db(store: Store) -> MarketDb:
    market = MarketDb(store)
    market.init_db()
    return market


def _pipeline(store: Store, db: MarketDb, sync: object) -> SkillRunner:
    registry = SkillRegistry(store)
    runner = SkillRunner(store, registry)
    install_builtins(
        registry, runner,
        context={"market": db, "docs": LocalDocSource(db, store), "sync": sync},
    )
    return runner


def _run(runner: SkillRunner, skill_id: str, values=None, inputs=None):
    return runner.run(skill_id, values=values, inputs=inputs)


# ───────────────────────── 013.1 data_sync ─────────────────────────


class TestDataSync:
    def test_gwt1_triggers_batch_and_returns_running_state(self, store, db):
        sync = FakeDataSync()
        port = SyncPort()
        port.bind(sync)
        runner = _pipeline(store, db, port)

        out = _run(runner, DATA_SYNC, {"dataset": "k_daily", "scope": "custom"},
                   {"codes": ["sh.600000"]})

        envelope = out.envelope
        assert envelope.status == "ok"
        assert envelope.data["state"] == "running"
        assert envelope.data["job_id"] == "job-1"
        items, custom_text = sync.start_calls[0]
        assert items[0]["target"] == "custom"
        assert items[0]["dataset"] == "k_daily"
        assert custom_text == "sh.600000"

    def test_gwt1_wait_seconds_waits_for_the_batch_to_finish(self, store, db):
        running = {"job_id": "job-1", "state": "running", "order": [],
                   "auto_added": [], "estimate": {}, "results": {},
                   "current": "", "started_at": "", "finished_at": "",
                   "error": "", "as_of": ""}
        done = dict(running, state="done", finished_at="2026-10-29T10:01:00+08:00",
                    as_of="2026-09-25T18:00:00+08:00")
        sync = FakeDataSync(sequence=[running, done])
        port = SyncPort()
        port.bind(sync)
        runner = _pipeline(store, db, port)

        out = _run(runner, DATA_SYNC, {
            "dataset": "k_daily", "scope": "watchlist", "wait_seconds": 1,
        })

        assert out.envelope.status == "ok"
        assert out.envelope.data["state"] == "done"

    def test_gwt3_running_batch_is_explicitly_refused(self, store, db):
        port = SyncPort()
        port.bind(FakeDataSync(busy=True))
        runner = _pipeline(store, db, port)

        out = _run(runner, DATA_SYNC, {"dataset": "k_daily", "scope": "watchlist"})

        assert out.envelope.status == "unavailable"
        assert "不并发" in (out.envelope.reason or "")

    def test_unbound_port_is_unavailable_and_does_not_fake_a_batch(self, store, db):
        runner = _pipeline(store, db, SyncPort())
        out = _run(runner, DATA_SYNC, {"dataset": "k_daily", "scope": "watchlist"})
        assert out.envelope.status == "unavailable"
        assert "T-AGT-013.1" in (out.envelope.reason or "")


# ───────────────────────── 013.2 data_fetch ─────────────────────────


class TestDataFetch:
    def test_gwt1_fetches_through_the_engine_and_reports_as_of(self, store, db):
        sync = FakeDataSync()
        port = SyncPort()
        port.bind(sync)
        runner = _pipeline(store, db, port)

        out = _run(runner, DATA_FETCH, {"dataset": "k_daily", "scope": "custom"},
                   {"codes": ["sh.600000"]})

        envelope = out.envelope
        assert envelope.status == "ok"
        assert envelope.data["dataset"] == "k_daily"
        assert envelope.data["rows"] == 3
        assert envelope.data["watermark"] == "2026-09-25"
        assert envelope.as_of is not None
        items, custom_text = sync.fetch_calls[0]
        assert items[0]["dataset"] == "k_daily"
        assert custom_text == "sh.600000"

    def test_gwt2_unregistered_domain_is_unavailable_and_named(self, store, db):
        sync = FakeDataSync()
        port = SyncPort()
        port.bind(sync)
        runner = _pipeline(store, db, port)

        out = _run(runner, DATA_FETCH, {"dataset": "no-such-domain"})

        assert out.envelope.status == "unavailable"
        assert "no-such-domain" in (out.envelope.reason or "")
        assert sync.fetch_calls == []

    def test_gwt3_failed_item_is_never_reported_as_success(self, store, db):
        port = SyncPort()
        port.bind(FakeDataSync(snapshot={
            "job_id": "job-1", "state": "done", "order": ["bs_k_daily"],
            "auto_added": [], "results": {
                "g1:bs_k_daily": {"status": "unavailable", "reason": "源不可达"},
            }, "error": "", "as_of": "2026-09-25T18:00:00+08:00",
        }))
        runner = _pipeline(store, db, port)

        out = _run(runner, DATA_FETCH, {"dataset": "k_daily", "scope": "watchlist"})

        assert out.envelope.status == "unavailable"
        assert "源不可达" in (out.envelope.reason or "")

    def test_empty_item_is_known_empty_not_failure(self, store, db):
        """`empty` 是合法的已知空集（01 §5），不判失败、不冒充 `unavailable`。"""
        port = SyncPort()
        port.bind(FakeDataSync(snapshot={
            "job_id": "job-1", "state": "done", "order": ["info_announcement_cninfo"],
            "auto_added": [], "results": {
                "g1:info_announcement_cninfo": {"status": "empty", "rows": 0,
                                                 "reason": "窗口内无公告"},
            }, "error": "", "as_of": "2026-09-25T18:00:00+08:00",
        }))
        runner = _pipeline(store, db, port)

        out = _run(runner, DATA_FETCH, {
            "dataset": "announcement_cninfo", "start": "2026-09-01",
            "end": "2026-09-02",
        })

        assert out.envelope.status == "ok"
        assert out.envelope.data["results"]["g1:info_announcement_cninfo"]["status"] == "empty"

    def test_gwt2_busy_engine_is_refused(self, store, db):
        port = SyncPort()
        port.bind(FakeDataSync(busy=True))
        runner = _pipeline(store, db, port)
        out = _run(runner, DATA_FETCH, {"dataset": "k_daily", "scope": "watchlist"})
        assert out.envelope.status == "unavailable"
        assert "不并发" in (out.envelope.reason or "")

    def test_unbound_port_is_unavailable(self, store, db):
        runner = _pipeline(store, db, SyncPort())
        out = _run(runner, DATA_FETCH, {"dataset": "k_daily", "scope": "watchlist"})
        assert out.envelope.status == "unavailable"
        assert "T-AGT-013.2" in (out.envelope.reason or "")


# ───────────────────────── 013.3 docs ─────────────────────────


def _seed_announcement(db: MarketDb, store: Store, *, doc_id: str = "ann_1",
                       title: str = "关于回购股份的公告", write_body: bool = True,
                       file_path: str | None = None) -> str:
    with db.transact() as con:
        con.execute(
            "INSERT OR IGNORE INTO data_source(source_id, name, manual_ref)"
            " VALUES ('cninfo', '巨潮资讯网', NULL)"
        )
        con.execute(
            "INSERT OR IGNORE INTO security(code, code_name, ipo_date, out_date,"
            " type, status) VALUES ('sh.600000', '浦发银行', '1999-11-10', NULL, 1, 1)"
        )
        con.commit()
    docs = DocStore(store)
    path = file_path or docs.write(
        "announcement", "sh.600000", "2026-09-20", doc_id, "正文内容"
    ) if write_body else (
        file_path or f"info/announcement/sh.600000/2026-09-20/{doc_id}.txt"
    )
    with db.transact() as con:
        con.execute(
            "INSERT INTO announcement(announcement_id, code, title, pub_date,"
            " file_path, coverage, source_id) VALUES (?, 'sh.600000', ?,"
            " '2026-09-20', ?, 'all', 'cninfo')",
            (doc_id, title, path))
        con.commit()
    return path


class TestDocs:
    def test_gwt1_list_read_and_search(self, store, db):
        _seed_announcement(db, store)
        runner = _pipeline(store, db, SyncPort())

        listed = _run(runner, DOCS, {"operation": "list"}).envelope
        assert listed.status == "ok"
        assert listed.data["operation"] == "list"
        assert listed.data["rows"][0]["doc_id"] == "ann_1"

        read = _run(runner, DOCS, {"operation": "read", "doc_id": "ann_1"}).envelope
        assert read.status == "ok"
        assert read.data["text"] == "正文内容"

        found = _run(runner, DOCS, {"operation": "search", "query": "回购"}).envelope
        assert found.status == "ok"
        assert found.data["rows"][0]["doc_id"] == "ann_1"

    def test_gwt2_missing_body_is_corrupt_not_empty(self, store, db):
        _seed_announcement(db, store, write_body=False)
        runner = _pipeline(store, db, SyncPort())

        read = _run(runner, DOCS, {"operation": "read", "doc_id": "ann_1"}).envelope
        assert read.status == "unavailable"
        assert "损坏" in (read.reason or "")

        audit = _run(runner, DOCS, {"operation": "audit"}).envelope
        assert audit.status == "ok"
        assert audit.data["consistent"] is False
        assert audit.data["missing_file"][0]["recyclable"] is False

    def test_gwt3_orphan_is_reported_recyclable_and_kept(self, store, db):
        docs = DocStore(store)
        orphan = docs.write("announcement", "sh.600000", "2026-09-21",
                            "ann_orphan", "孤儿")
        runner = _pipeline(store, db, SyncPort())

        audit = _run(runner, DOCS, {"operation": "audit"}).envelope
        assert audit.status == "ok"
        assert audit.data["orphan_file"] == [
            {"path": orphan, "kind": "orphan", "recyclable": True}
        ]
        assert docs.exists(orphan)  # 只判不收

    def test_unknown_operation_is_validation_failed(self, store, db):
        runner = _pipeline(store, db, SyncPort())
        out = _run(runner, DOCS, {"operation": "drop-table"})
        assert out.envelope.status == "validation_failed"
