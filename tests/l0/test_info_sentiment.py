"""T-L0-010.5 测试：舆情域采集与管理。

GWT 对照（任务文件 5 条）：
- GWT-1 **双市可查**——深市互动易 + 沪市上证e互动同表、按 `market` 分片
- GWT-2 **两实体不混**——问答与热度榜各自成表（热榜不进问答表）
- GWT-3 **易腐数据正确降级**——热度榜为 `snapshot` 模式、无水位，且**不**走档案型判据
- GWT-4 正文**两段同源同现**
- GWT-5 **覆盖缺口显式**——过滤计数可区分「该市场无记录」与「数据缺失」
"""

from __future__ import annotations

from info_helpers import UNMAPPED_CODE, FakeInfoFetcher
from st_agent.l0.info import LocalDocSource, audit_docs

IRM = "info_sentiment_qa_irm"
SSE = "info_sentiment_qa_sse"
HOT = "info_sentiment_hot"
WINDOW = ("2026-09-01", "2026-09-30")


def _run_irm(sync, **kwargs):
    """跑深市互动问答——T-L0-015 起它是**关注面任务**：默认禁用，须显式开启 +
    注入关注池（`watchlist`），见 [D-037] / 05。"""
    return sync.run_task(IRM, enabled={IRM: True}, watchlist=["sz.000001"], **kwargs)


def _qa(code: str = "000001", *, question: str = "公司如何回应近期传闻？",
        answer: str | None = None, ask: str = "2026-09-20 10:30") -> dict:
    return {"code": code, "question": question, "answer": answer,
            "answerer": "董事会办公室" if answer else None, "ask_time": ask}


def _hot(code: str = "600000", *, rank: int = 1, board: str = "ths_hot") -> dict:
    return {"code": code, "board": board, "rank": rank, "heat": 9876.5}


def _plan(task_key: str, rows) -> FakeInfoFetcher:
    return FakeInfoFetcher({task_key: {("sentiment_hot" if task_key == HOT
                                        else "sentiment_qa"): tuple(rows)}})


class TestGwt1TwoMarketsParallel:
    def test_sz_and_sh_in_one_table_with_market_tag(self, db, sync_factory):
        sync = sync_factory(FakeInfoFetcher({
            IRM: {"sentiment_qa": (_qa("000001"),)},
            SSE: {"sentiment_qa": (_qa("600000", question="沪市提问"),)},
        }))
        _run_irm(sync, window=WINDOW)
        sync.run_task(SSE, window=WINDOW)
        env = db.query("SELECT code, market, source_id FROM sentiment_qa ORDER BY code")
        assert env.status == "ok"
        assert [(r["code"], r["market"]) for r in env.data["rows"]] == [
            ("sh.600000", "sh"), ("sz.000001", "sz")]

    def test_parallel_sources_do_not_overwrite_each_other(self, db, sync_factory):
        """并行互补（非主备）：两市各自写入，互不覆盖。"""
        sync = sync_factory(FakeInfoFetcher({
            IRM: {"sentiment_qa": (_qa("000001", question="深市问题"),)},
            SSE: {"sentiment_qa": (_qa("600000", question="沪市问题"),)},
        }))
        _run_irm(sync, window=WINDOW)
        sync.run_task(SSE, window=WINDOW)
        assert db.query("SELECT count(*) AS n FROM sentiment_qa").data["rows"][0]["n"] == 2

    def test_unanswered_question_is_a_legal_row(self, db, sync_factory):
        """未回复是**合法态**（`answer=NULL`），不是缺失。"""
        _run_irm(sync_factory(_plan(IRM, [_qa(answer=None)])), window=WINDOW)
        env = db.query("SELECT answer FROM sentiment_qa")
        assert env.status == "ok" and env.data["rows"][0]["answer"] is None


class TestGwt2TwoEntitiesSeparate:
    def test_hot_does_not_land_in_qa_table(self, db, sync_factory):
        sync_factory(_plan(HOT, [_hot()])).run_task(HOT, window=("2026-09-25", "2026-09-25"))
        assert db.query("SELECT count(*) AS n FROM sentiment_qa"
                        ).data["rows"][0]["n"] == 0
        env = db.query("SELECT board, rank FROM sentiment_hot")
        assert env.status == "ok" and env.data["rows"][0]["board"] == "ths_hot"

    def test_two_boards_are_distinguished(self, db, sync_factory):
        sync_factory(_plan(HOT, [_hot(board="ths_hot", rank=1),
                                 _hot(board="em_popularity", rank=1)])).run_task(
            HOT, window=("2026-09-25", "2026-09-25"))
        env = db.query("SELECT DISTINCT board FROM sentiment_hot ORDER BY board")
        assert [r["board"] for r in env.data["rows"]] == ["em_popularity", "ths_hot"]


class TestGwt3PerishableData:
    def test_hot_is_snapshot_without_watermark(self, db, sync_factory):
        """易腐数据：`snapshot` 模式、**无水位**（值是「此刻」排名，不按水位续跑）。"""
        sync_factory(_plan(HOT, [_hot()])).run_task(HOT, window=("2026-09-25", "2026-09-25"))
        env = db.query("SELECT mode, watermark FROM sync_state WHERE task_key=?", (HOT,))
        assert env.data["rows"][0]["mode"] == "snapshot"
        assert env.data["rows"][0]["watermark"] is None

    def test_hot_window_is_single_day_anchor(self):
        from st_agent.l0.info import window_for

        assert window_for(HOT, None, day="2026-09-25") == ("2026-09-25", "2026-09-25")

    def test_hot_not_treated_as_archival(self, db, sync_factory):
        """热度榜**不**进档案型判据（与公告区分）。"""
        sync = sync_factory(_plan(HOT, [_hot()]))
        sync.run_task(HOT, window=("2026-09-25", "2026-09-25"))
        assert sync.archival_gaps("sentiment_hot") == ()

    def test_snapshots_accumulate_across_runs(self, db, sync_factory):
        sync_factory(_plan(HOT, [_hot(rank=1)])).run_task(
            HOT, window=("2026-09-24", "2026-09-24"))
        sync_factory(_plan(HOT, [_hot(rank=1)])).run_task(
            HOT, window=("2026-09-25", "2026-09-25"))
        # 快照时刻由引擎生成，两次运行 → 两行（历史不动）
        env = db.query("SELECT count(*) AS n FROM sentiment_hot")
        assert env.data["rows"][0]["n"] == 2


class TestGwt4TwoSegments:
    def test_qa_body_written_and_audit_clean(self, db, store, sync_factory):
        sync = sync_factory(_plan(IRM, [_qa(answer="已关注到相关报道。")]))
        _run_irm(sync, window=WINDOW)
        row = db.query("SELECT qa_id, file_path FROM sentiment_qa").data["rows"][0]
        doc = LocalDocSource(db, store).read_doc(row["qa_id"])
        assert doc.status == "ok"
        assert "已关注到相关报道。" in doc.data["text"]
        assert sync.audit_docs().is_consistent

    def test_doc_path_stays_under_text_root(self, db, sync_factory):
        _run_irm(sync_factory(_plan(IRM, [_qa()])), window=WINDOW)
        path = db.query("SELECT file_path FROM sentiment_qa").data["rows"][0]["file_path"]
        assert path.startswith("info/sentiment_qa/") and path.endswith(".txt")


class TestGwt5CoverageGapExplicit:
    def test_filtered_codes_reported(self, db, sync_factory):
        env = _run_irm(
            sync_factory(_plan(IRM, [_qa("000001"), _qa("920002")])), window=WINDOW)
        assert env.status == "ok"
        assert env.data["filtered_unmapped"]["sample"] == [UNMAPPED_CODE]

    def test_market_outage_distinguishable_from_no_records(self, db, sync_factory):
        """某市场源不可用 → 该任务 `unavailable`；**不**与「该市场无记录」混同。"""
        sync = sync_factory(FakeInfoFetcher({
            IRM: {"sentiment_qa": (_qa("000001"),)},
        }, unavailable_on=(SSE,)))
        assert _run_irm(sync, window=WINDOW).status == "ok"
        assert sync.run_task(SSE, window=WINDOW).status == "unavailable"
        verdict = sync.freshness_verdict("sentiment_qa")
        assert verdict.stale is True and SSE in verdict.detail
