"""T-AGT-017.2 测试：大结果回读原语 ``sk_result_read``（[05 §10](../../docs/技术架构-v2/05-L3-对话主入口.md) / [01 §5](../../docs/技术架构-v2/01-平台共享契约.md)）。

GWT 对照：
- GWT-1：超阈值结果的指针可回读——按 ``offset`` / ``max_chars`` 分片取回完整载荷。
- GWT-2：未超阈值时循环工作上下文原样（本原语只被显式调用才生效）。
- GWT-3：回读越界 → ``validation_failed``，不返回半截假数据。
"""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.storage import Store
from st_agent.l1.builtin.install import BUILTIN_PRIMITIVES
from st_agent.l1.builtin.results import (
    DEFAULT_RESULT_READ_CHARS,
    MAX_RESULT_READ_CHARS,
    RESULT_READ,
    ResultAccess,
)
from st_agent.l1.runner.models import RUN_PREFIX, SkillRun

PASS = "correct horse battery staple"
RUN_ID = "run_" + "a" * 20


@pytest.fixture()
def store(tmp_path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def execute(store):
    return RESULT_READ.executor({"results": ResultAccess(store)})


def _write_run(store: Store, data, *, status: str = "ok", run_id: str = RUN_ID) -> SkillRun:
    envelope = (
        ResultEnvelope.ok(data) if status == "ok"
        else ResultEnvelope.empty("没有载荷")
    )
    run = SkillRun(
        skill_run_id=run_id,
        skill_id="sk_alpha_v1.0",
        trace_id="tr_abc",
        params={},
        inputs={},
        upstream=(),
        envelope=envelope,
        duration_ms=1,
        started_at=datetime.now().astimezone(),
    )
    store.put("execution_log", f"{RUN_PREFIX}{run_id}.json",
              run.model_dump_json().encode("utf-8"))
    return run


def _payload(data) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, default=repr)


def test_seed_is_registered():
    assert RESULT_READ in BUILTIN_PRIMITIVES
    assert RESULT_READ.base == "sk_result_read"
    assert RESULT_READ.requires == ("results",)


def test_read_returns_first_chunk(store, execute):
    data = {"rows": [{"code": "sh.600000", "value": index} for index in range(20)]}
    _write_run(store, data)
    payload = _payload(data)
    envelope = execute(
        SimpleNamespace(inputs={"skill_run_id": RUN_ID, "max_chars": 100}), {},
    )
    assert envelope.status == "ok"
    assert envelope.data["total_chars"] == len(payload)
    assert envelope.data["offset"] == 0
    assert envelope.data["next_offset"] == 100
    assert envelope.data["truncated"] is True
    assert envelope.data["text"] == payload[:100]


def test_default_chunk_is_two_thousand_chars(store, execute):
    _write_run(store, {"text": "x" * 3000})
    envelope = execute(SimpleNamespace(inputs={"skill_run_id": RUN_ID}), {})
    assert envelope.status == "ok"
    assert envelope.data["next_offset"] == DEFAULT_RESULT_READ_CHARS
    assert envelope.data["truncated"] is True


def test_chunks_reassemble_into_the_original_payload(store, execute):
    data = {"text": "字" * 300}
    _write_run(store, data)
    payload = _payload(data)
    chunks: list[str] = []
    offset = 0
    while True:
        envelope = execute(
            SimpleNamespace(inputs={
                "skill_run_id": RUN_ID, "offset": offset, "max_chars": 50,
            }), {},
        )
        assert envelope.status == "ok"
        chunks.append(envelope.data["text"])
        offset = envelope.data["next_offset"]
        if not envelope.data["truncated"]:
            break
    assert "".join(chunks) == payload


def test_offset_past_end_is_rejected(store, execute):
    data = {"text": "short"}
    _write_run(store, data)
    total = len(_payload(data))
    envelope = execute(
        SimpleNamespace(inputs={"skill_run_id": RUN_ID, "offset": total + 1}), {},
    )
    assert envelope.status == "validation_failed"
    assert "越过结果末尾" in envelope.reason


def test_offset_at_end_returns_empty_text(store, execute):
    data = {"text": "short"}
    _write_run(store, data)
    total = len(_payload(data))
    envelope = execute(
        SimpleNamespace(inputs={"skill_run_id": RUN_ID, "offset": total}), {},
    )
    assert envelope.status == "ok"
    assert envelope.data["text"] == ""
    assert envelope.data["truncated"] is False


def test_negative_offset_is_rejected(store, execute):
    _write_run(store, {"text": "x"})
    envelope = execute(
        SimpleNamespace(inputs={"skill_run_id": RUN_ID, "offset": -1}), {},
    )
    assert envelope.status == "validation_failed"
    assert "为负" in envelope.reason


def test_max_chars_bounds_are_enforced(store, execute):
    _write_run(store, {"text": "x" * 100})
    too_big = execute(
        SimpleNamespace(inputs={"skill_run_id": RUN_ID, "max_chars": MAX_RESULT_READ_CHARS + 1}),
        {},
    )
    assert too_big.status == "validation_failed"
    not_int = execute(
        SimpleNamespace(inputs={"skill_run_id": RUN_ID, "max_chars": "10"}), {},
    )
    assert not_int.status == "validation_failed"


def test_missing_record_is_unavailable(execute):
    envelope = execute(SimpleNamespace(inputs={"skill_run_id": RUN_ID}), {})
    assert envelope.status == "unavailable"
    assert "取不到" in envelope.reason


def test_non_ok_status_has_no_payload_to_read(store, execute):
    _write_run(store, None, status="empty")
    envelope = execute(SimpleNamespace(inputs={"skill_run_id": RUN_ID}), {})
    assert envelope.status == "empty"
    assert "没有可回读的载荷" in envelope.reason


def test_invalid_skill_run_id_is_rejected(store, execute):
    _write_run(store, {"text": "x"})
    for bad in ("../skill-run/x", "run_XYZ", "run_" + "g" * 20):
        envelope = execute(SimpleNamespace(inputs={"skill_run_id": bad}), {})
        assert envelope.status == "validation_failed", bad
        assert "形态非法" in envelope.reason


def test_missing_skill_run_id_is_rejected(execute):
    envelope = execute(SimpleNamespace(inputs={}), {})
    assert envelope.status == "validation_failed"
    assert "缺少 skill_run_id" in envelope.reason
