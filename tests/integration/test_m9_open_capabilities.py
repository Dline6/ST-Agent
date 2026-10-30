"""`T-INT-010` · M9 集成关卡：开放能力面的跨层装配用例。

只测**装配关系与跨层数据流**（单叶行为已由各自套件覆盖）：GWT 锚在
[02 §5–§7](../../docs/技术架构-v2/02-L0-本地优先基座.md) 的取数 / 出网 / 能力声明与
[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md) 的循环 / 交还 / 证据包。

**全离线**：只换三处网络跳（LLM HTTP 发送口、开放网络 HTTP 发送口、信息面抓取器），
`Store` / `MarketDb` / `EgressGateway` / `InfoSync` / 沙箱 / 闸门 / 留痕**都是真的**；
真端点路径在 `tests/live/`。**出厂分级一次都不放宽**——取数 / 写文件 / 联网的确认
正是本关卡要一并验的口径。
"""

from __future__ import annotations

from pathlib import Path

from rig_m9 import (
    ANNOUNCEMENT_DATASET,
    ANNOUNCEMENT_TASK,
    DRAGON_TIGER_DATASET,
    TASK_TEXT,
    brave_payload,
    enable_web_search,
    seeded_m9,
)

from st_agent.app import TurnResult
from st_agent.l0.file_workspace import FileWorkspace
from st_agent.l0.info.docs import LocalDocSource
from st_agent.l0.net.web_config import set_egress_enabled
from st_agent.l1.runner.models import SkillRun
from st_agent.l3.runtime import load_agent_run

ANNOUNCEMENT_SQL = (
    "SELECT code, title, pub_date, source_id FROM announcement"
    " WHERE code = ? ORDER BY pub_date DESC"
)
DRAGON_TIGER_SQL = (
    "SELECT code, trade_date, reasons, net_amount, source_id FROM dragon_tiger"
    " WHERE code = ? ORDER BY trade_date DESC"
)


# ───────────────────────────── 共用小工具 ─────────────────────────────


def _investigate(rig, text: str = TASK_TEXT):
    """走一次 `investigate` 全流程（意图 → 确认 → 派发），返回派发产出。"""
    turn = rig.m9.chat.post(text)
    assert isinstance(turn, TurnResult), turn
    assert turn.needs_confirmation, "investigate 意图应产出确认卡"
    return rig.m9.chat.confirm_and_dispatch(turn.session_id)


def _resume(rig, agent_run_id: str, *, session_id: str | None = None):
    """确认并继续（对交还的待确认步投 approve）。"""
    return rig.m9.chat.turn({
        "action": "resume",
        "agent_run_id": agent_run_id,
        "decision": "approve",
        "session_id": session_id,
    })


def _record_of(rig, payload):
    """从一次完成的续跑产出取回新留痕。"""
    return load_agent_run(rig.store, payload["reply"].data.slots["agent_run_id"])


def _skill_run(rig, skill_run_id: str) -> SkillRun:
    raw = rig.store.get("execution_log", f"skill-run/{skill_run_id}.json")
    return SkillRun.model_validate_json(raw)


def _rows(rig, sql: str, params: tuple = ()) -> list[dict]:
    env = rig.db.query(sql, params)
    assert env.status == "ok", env
    return env.data["rows"]


def _assert_pending(payload, tool_name: str) -> dict:
    cont = payload["agent_continuation"]
    assert cont is not None, "应再次交还待确认步"
    assert cont["kind"] == "pending_step"
    assert cont["pending"]["tool_name"] == tool_name
    return cont


# ─────────── GWT-1 · 本地缺数据：循环自己补上并落缓存后作答 ───────────


class TestGwt1LoopFillsLocalGaps:
    def test_fetches_missing_announcement_and_dragon_tiger_then_answers(
        self, tmp_path: Path
    ):
        """Given 本地缺公告与龙虎榜，When 提问，Then 经 `data_fetch` 取数、落缓存并回答。"""
        rig = seeded_m9(
            tmp_path / "root",
            script=[
                ("sk_data_status", {}),
                ("sk_data_fetch", {"dataset": ANNOUNCEMENT_DATASET}),
                ("sk_data_fetch", {"dataset": DRAGON_TIGER_DATASET}),
                ("sk_local_sql", {"sql": ANNOUNCEMENT_SQL, "params": ["sh.600000"]}),
                ("sk_local_sql", {"sql": DRAGON_TIGER_SQL, "params": ["sh.600000"]}),
            ],
            finish="公告与龙虎榜已取回本地：sh.600000 有一条回购公告与一条上榜记录。",
        )

        # 取数前：本地确实没有这两域数据（GWT-1 的「缺」）
        assert _rows(rig, "SELECT count(*) AS n FROM announcement")[0]["n"] == 0
        assert _rows(rig, "SELECT count(*) AS n FROM dragon_tiger")[0]["n"] == 0

        first = _investigate(rig)
        rec1 = first.investigation.report.record

        # 第一步自治读现状；第二步是写类取数，出厂口径即交还确认
        assert rec1.termination == "needs_confirmation"
        assert [step.tool_name for step in rec1.steps] == ["sk_data_status"]
        assert rec1.pending is not None
        assert rec1.pending.tool_name == "sk_data_fetch"
        assert rig.m9.l6.agent_authorization.tier() == "autonomous"

        # 确认公告取数 → 真跑并落缓存；下一步（龙虎榜）再次交还
        p1 = _resume(
            rig, first.investigation.agent_run_id,
            session_id=first.investigation.session_id,
        )
        cont1 = _assert_pending(p1, "sk_data_fetch")
        rec2 = load_agent_run(rig.store, cont1["agent_run_id"])
        assert rec2.resumed_from == first.investigation.agent_run_id
        assert [step.tool_name for step in rec2.steps] == [
            "sk_data_status", "sk_data_fetch",
        ]
        assert [step.status for step in rec2.steps] == ["ok", "ok"]

        # 第一次取数已真落缓存（不是替身计数）
        assert _rows(rig, "SELECT count(*) AS n FROM announcement")[0]["n"] == 1
        assert _rows(rig, "SELECT count(*) AS n FROM dragon_tiger")[0]["n"] == 0

        # 确认龙虎榜取数 → 循环继续跑完 SQL 作答
        p2 = _resume(
            rig, cont1["agent_run_id"], session_id=first.investigation.session_id,
        )
        assert p2["agent_continuation"] is None
        assert p2["reply"].status == "ok"
        rec3 = _record_of(rig, p2)
        assert rec3.termination == "finished"
        assert [step.tool_name for step in rec3.steps] == [
            "sk_data_status", "sk_data_fetch", "sk_data_fetch",
            "sk_local_sql", "sk_local_sql",
        ]
        assert all(step.status == "ok" for step in rec3.steps)

        # 两域都进了本地缓存，且带来源与时间
        ann = _rows(rig, ANNOUNCEMENT_SQL, ("sh.600000",))[0]
        assert ann["title"] == "关于回购公司股份的公告"
        assert ann["pub_date"] == "2026-09-20"
        assert ann["source_id"] == "cninfo"
        dt = _rows(rig, DRAGON_TIGER_SQL, ("sh.600000",))[0]
        assert dt["trade_date"] == "2026-09-25"
        assert dt["reasons"] == "日涨幅偏离值达7%"
        assert dt["source_id"] == "eastmoney"

        # 公告正文也真落了 data_cache（读得回原文）
        source = LocalDocSource(rig.db, rig.store)
        listed = source.list_docs(domain="announcement")
        assert listed.status == "ok" and len(listed.data["rows"]) == 1
        doc_id = listed.data["rows"][0]["doc_id"]
        read = source.read_doc(doc_id)
        assert read.status == "ok" and read.data["text"] == "回购正文……"

        # 收尾文本进 answer 槽；证据表逐步对位
        slots = p2["reply"].data.slots
        assert slots["answer"] == (
            "公告与龙虎榜已取回本地：sh.600000 有一条回购公告与一条上榜记录。"
        )
        assert [row["tool_name"] for row in slots["rows"]] == [
            "sk_data_status", "sk_data_fetch", "sk_data_fetch",
            "sk_local_sql", "sk_local_sql",
        ]
        fetch_run = _skill_run(rig, rec3.steps[1].skill_run_id)
        assert fetch_run.envelope.data["dataset"] == ANNOUNCEMENT_DATASET
        assert fetch_run.envelope.data["as_of"]


# ─────────── GWT-2 · 出网关闭 / 未配置：显式、不假装答过 ───────────


class TestGwt2EgressClosedOrUnconfigured:
    def test_egress_off_is_explicit_and_sends_nothing(self, tmp_path: Path):
        """Given 开放网络关闭，Then 确认后原语显式 `unavailable`，零请求、零审计。"""
        rig = seeded_m9(
            tmp_path / "root",
            script=[("sk_web_search", {"query": "浦发银行 最新公告"})],
            finish="开放网络未开启，无法检索最新公告；本次未给出结论。",
        )

        first = _investigate(rig)
        assert first.investigation.report.record.pending.tool_name == "sk_web_search"

        payload = _resume(
            rig, first.investigation.agent_run_id,
            session_id=first.investigation.session_id,
        )
        assert payload["agent_continuation"] is None
        record = _record_of(rig, payload)

        # 那一步跑过了（不是静默跳过），但如实标 unavailable
        assert [step.tool_name for step in record.steps] == ["sk_web_search"]
        assert [step.status for step in record.steps] == ["unavailable"]
        run = _skill_run(rig, record.steps[0].skill_run_id)
        assert "开放网络未开启" in (run.envelope.reason or "")

        # 零请求、零审计：原语在触碰网关之前就返回了
        assert rig.http.calls == []
        assert rig.gateway.query(kind="web_search") == ()

        # 原因进入下一轮上下文（模型据此给下一步），且不产假证据
        assert "开放网络未开启" in rig.endpoint.loop_calls[-1]["prompt"]
        slots = payload["reply"].data.slots
        assert [row["status"] for row in slots["rows"]] == ["unavailable"]
        assert payload["reply"].evidence_refs == ()
        assert slots["answer"] == "开放网络未开启，无法检索最新公告；本次未给出结论。"

    def test_missing_search_provider_is_explicit(self, tmp_path: Path):
        """Given 出网已开但未配置搜索提供方，Then 同样显式不可用且零请求。"""
        rig = seeded_m9(
            tmp_path / "root",
            script=[("sk_web_search", {"query": "浦发银行 最新公告"})],
        )
        set_egress_enabled(rig.store, True)      # 开关开，但 provider 仍为空

        first = _investigate(rig)
        payload = _resume(
            rig, first.investigation.agent_run_id,
            session_id=first.investigation.session_id,
        )
        record = _record_of(rig, payload)

        assert [step.status for step in record.steps] == ["unavailable"]
        run = _skill_run(rig, record.steps[0].skill_run_id)
        assert "未配置搜索提供方" in (run.envelope.reason or "")
        assert rig.http.calls == []
        assert rig.gateway.query(kind="web_search") == ()


# ─────────── GWT-3 · 写文件落工作区分区且用户能找到 ───────────


class TestGwt3FileWriteLandsInWorkspace:
    def test_write_then_read_back_from_the_workspace_partition(self, tmp_path: Path):
        """Given 写入文件，Then 落在 `workspace` 分区且可经读原语取回。"""
        content = "# sh.600000\n公告要点：回购公司股份。\n"
        rig = seeded_m9(
            tmp_path / "root",
            script=[
                ("sk_file_write", {
                    "path": "workspace/reports/sh.600000.md", "content": content,
                }),
                ("sk_file_read", {"path": "workspace/reports/sh.600000.md"}),
            ],
            finish="已把结论写入工作区 reports/sh.600000.md",
        )

        first = _investigate(rig)
        assert first.investigation.report.record.pending.tool_name == "sk_file_write"

        payload = _resume(
            rig, first.investigation.agent_run_id,
            session_id=first.investigation.session_id,
        )
        assert payload["agent_continuation"] is None
        record = _record_of(rig, payload)
        assert [step.tool_name for step in record.steps] == [
            "sk_file_write", "sk_file_read",
        ]
        assert [step.status for step in record.steps] == ["ok", "ok"]

        # 落点在 workspace 分区：清单、物理文件、读接口三处一致
        assert "reports/sh.600000.md" in rig.store.list_files("workspace")
        assert (rig.root / "workspace" / "reports" / "sh.600000.md").exists()
        text, _raw = FileWorkspace(rig.store).read_text(
            "workspace/reports/sh.600000.md"
        )
        assert text == content

        read_run = _skill_run(rig, record.steps[1].skill_run_id)
        assert read_run.envelope.data["area"] == "workspace"
        assert read_run.envelope.data["text"] == content
        assert payload["reply"].data.slots["answer"] == (
            "已把结论写入工作区 reports/sh.600000.md"
        )


# ─────────── GWT-4 · 外网数据带来源与时间，出网有审计 ───────────


class TestGwt4ProvenanceAndEgressAudit:
    def test_web_search_results_carry_source_and_time_and_are_audited(
        self, tmp_path: Path
    ):
        """Given 一次联网检索，Then 结果带来源 / 时间，出网在网关审计里可查。"""
        rig = seeded_m9(
            tmp_path / "root",
            script=[("sk_web_search", {"query": "ST 股票 最新公告", "limit": 2})],
            finish="已检索到两条公开来源的结果。",
        )
        enable_web_search(rig)
        rig.http.payload = brave_payload(
            {"title": "浦发银行公告", "url": "https://news.example/1",
             "description": "关于回购", "page_age": "2026-10-20"},
            {"title": "ST 股动态", "url": "https://news.example/2",
             "description": "龙虎榜", "page_age": "2026-10-25"},
        )

        first = _investigate(rig)
        payload = _resume(
            rig, first.investigation.agent_run_id,
            session_id=first.investigation.session_id,
        )
        record = _record_of(rig, payload)
        assert [step.status for step in record.steps] == ["ok"]

        run = _skill_run(rig, record.steps[0].skill_run_id)
        assert run.envelope.status == "ok"
        assert run.envelope.as_of is not None
        assert run.envelope.data["provider"] == "brave"
        assert run.envelope.data["count"] == 2
        assert run.envelope.data["retrieved_at"]
        for item in run.envelope.data["results"]:
            assert item["source"] == "brave"
            assert item["title"] and item["url"]

        # 出网审计：一条 web_search 记录，宿主与发起方可查，内容零明文
        events = rig.gateway.query(kind="web_search")
        assert len(events) == 1
        event = events[0]
        assert event.initiator == "sk_web_search_v1.0"
        assert event.target_host == "api.search.brave.com"
        assert event.status == "ok"
        assert "联网搜索" in event.purpose
        assert event.bytes_out > 0 and event.bytes_in > 0
        blob = " ".join(item.model_dump_json() for item in events)
        assert "ST 股票 最新公告" not in blob
        assert len(rig.http.calls) == 1

    def test_data_fetch_is_audited_and_writes_source_and_watermark(
        self, tmp_path: Path
    ):
        """Given 一次按域取数，Then 缓存行带来源 / 时间，且抓取经网关留痕。"""
        rig = seeded_m9(
            tmp_path / "root",
            script=[("sk_data_fetch", {"dataset": ANNOUNCEMENT_DATASET})],
            finish="公告已取回并落本地缓存。",
        )

        first = _investigate(rig)
        payload = _resume(
            rig, first.investigation.agent_run_id,
            session_id=first.investigation.session_id,
        )
        record = _record_of(rig, payload)
        assert [step.status for step in record.steps] == ["ok"]

        run = _skill_run(rig, record.steps[0].skill_run_id)
        assert run.envelope.data["as_of"]

        state = _rows(
            rig,
            "SELECT last_status, last_success_at, source_id FROM sync_state"
            " WHERE task_key = ?",
            (ANNOUNCEMENT_TASK,),
        )[0]
        assert state["last_status"] == "ok"
        assert state["last_success_at"]
        assert state["source_id"] == "cninfo"

        row = _rows(rig, ANNOUNCEMENT_SQL, ("sh.600000",))[0]
        assert row["source_id"] == "cninfo" and row["pub_date"] == "2026-09-20"

        # 出网审计：抓取经网关、宿主是真实主机名（不是 source_id 冒充）
        events = rig.gateway.query(kind="data_fetch")
        assert events
        assert all(event.status == "ok" for event in events)
        assert all("." in event.target_host and "_" not in event.target_host
                   for event in events)
        assert any("信息面同步" in event.purpose for event in events)
        assert rig.http.calls == []              # 信息面抓取走抓取器替身，不经 web_http
