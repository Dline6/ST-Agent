"""`T-UI-005.1` · Studio 画布后端面（读面 / 编辑转交 / 编辑后接受）的**跨层装配**用例。

只测**装配关系与跨层数据流**（`CanvasEditor` 的编辑行为已由 `tests/l1/test_studio_canvas.py`
覆盖）：主动提案 → 交 Studio 落画布 → 画布读 → 结构编辑 → 接受落**编辑后** DAG，全程经
**生产组合根** `build_m4_runtime`（真 `Store` / 真 `SkillRegistry` / 真 `DraftIntake`）。

锚点：story-06 的「Studio 主画布」与 [08 §4](../../docs/技术架构-v2/08-L6-反思演进.md)
的「衔接 story-06」。全部离线（出网面由 rig 替身接管）。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from rig import ROOT_NAME
from rig_m4 import NOW, M4Rig, observation, seeded_m4

from st_agent.app import ReflectionFacade


@pytest.fixture()
def rig(tmp_path: Path) -> M4Rig:
    """真装配 + 一条必然过阈值的模式观察（`count=6 > 缺省 5`）。"""
    return seeded_m4(tmp_path / ROOT_NAME, observations=(observation(),))


def _handoff_one(rig: M4Rig) -> str:
    """跑一次模式识别、交 Studio 落画布，返回 `proposal_id`。"""
    rig.m4.l6.proposals.detect(now=NOW)
    proposals = rig.m4.l6.proposals.all()
    assert len(proposals) == 1
    proposal_id = proposals[0].proposal_id
    view = rig.m4.reflection.studio_handoff(proposal_id)
    assert view["available"] is True
    return proposal_id


# ───────────────────────── GWT-1 画布读面 ─────────────────────────


def test_gwt1_canvas_read_matches_the_session(rig: M4Rig) -> None:
    pid = _handoff_one(rig)
    face = rig.m4.reflection
    assert isinstance(face, ReflectionFacade)

    payload = face.studio_canvas(pid)

    assert payload["available"] is True
    canvas = payload["canvas"]
    assert canvas["session"] == {
        "proposal_id": pid,
        "base": "wf_same_kind_helper",
        "flow_id": "wf_same_kind_helper_v0.0",
        "node_count": 1,
        "status": "editing",
    }
    assert [node["node_id"] for node in canvas["nodes"]] == ["n1"]
    assert canvas["nodes"][0]["skill_id"] == "sk_stock_watch_v1.0"
    assert canvas["edges"] == [] and canvas["groups"] == []
    assert canvas["violations"] == []            # 引用的官方 Skill 已注册
    assert canvas["validation_ok"] is True
    assert "n2" not in canvas                    # 新增节点尚未出现


def test_gwt1_skill_options_come_from_the_registry(rig: M4Rig) -> None:
    """「加节点」的可选清单取自 L1 注册表的投影（01 §2），不新造第二份登记。"""
    pid = _handoff_one(rig)
    canvas = rig.m4.reflection.studio_canvas(pid)["canvas"]
    names = {entry["name"] for entry in canvas["skill_options"]}
    assert "sk_stock_watch" in names
    assert all(entry["skill_id"].startswith("sk_") for entry in canvas["skill_options"])


# ───────────────────────── GWT-2 编辑转交 ─────────────────────────


def test_gwt2_edit_adds_node_and_reflects_in_canvas(rig: M4Rig) -> None:
    pid = _handoff_one(rig)

    payload = rig.m4.reflection.studio_edit(
        proposal_id=pid, op="add_node",
        args={"node_id": "n2", "skill_id": "sk_stock_watch_v1.0"},
    )

    canvas = payload["canvas"]
    assert canvas["edit"]["applied"] is True
    assert canvas["edit"]["action"] == "add_node"
    assert canvas["edit"]["blocked_by"] == []
    assert {node["node_id"] for node in canvas["nodes"]} == {"n1", "n2"}
    assert canvas["session"]["node_count"] == 2


def test_gwt2_blocked_edit_is_applied_false_never_an_envelope_failure(rig: M4Rig) -> None:
    """用户级冲突（重复标识）走 `applied=False`——**不抛异常、画布不变**（03 §4 全函数）。"""
    pid = _handoff_one(rig)

    payload = rig.m4.reflection.studio_edit(
        proposal_id=pid, op="add_node",
        args={"node_id": "n1", "skill_id": "sk_stock_watch_v1.0"},
    )

    canvas = payload["canvas"]
    assert canvas["edit"]["applied"] is False
    assert canvas["edit"]["message"]
    assert canvas["session"]["node_count"] == 1


def test_gwt2_connect_then_disconnect_round_trips(rig: M4Rig) -> None:
    pid = _handoff_one(rig)
    face = rig.m4.reflection
    face.studio_edit(proposal_id=pid, op="add_node",
                     args={"node_id": "n2", "skill_id": "sk_stock_watch_v1.0"})

    connected = face.studio_edit(
        proposal_id=pid, op="connect",
        args={"edge_id": "e1", "from_node": "n1", "to_node": "n2"},
    )["canvas"]
    assert connected["edit"]["applied"] is True
    assert [edge["edge_id"] for edge in connected["edges"]] == ["e1"]

    disconnected = face.studio_edit(
        proposal_id=pid, op="disconnect", args={"edge_id": "e1"},
    )["canvas"]
    assert disconnected["edit"]["applied"] is True
    assert disconnected["edges"] == []


def test_gwt2_missing_argument_is_input_failure(rig: M4Rig) -> None:
    pid = _handoff_one(rig)
    with pytest.raises(ValueError, match="缺少必填参数"):
        rig.m4.reflection.studio_edit(proposal_id=pid, op="connect", args={"edge_id": "e1"})


def test_gwt2_unknown_op_is_input_failure(rig: M4Rig) -> None:
    """`params` 编辑本批不开放（[`T-UI-005.1` A3]）——`set_params` 不在枚举内。"""
    pid = _handoff_one(rig)
    with pytest.raises(ValueError, match="画布编辑操作"):
        rig.m4.reflection.studio_edit(proposal_id=pid, op="set_params", args={})


def test_gwt2_canvas_before_handoff_is_input_failure(rig: M4Rig) -> None:
    with pytest.raises(ValueError, match="尚未交 Studio"):
        rig.m4.reflection.studio_canvas("prp_nonexistent")


# ───────────────────────── GWT-3 编辑后接受 ─────────────────────────


def test_gwt3_accept_lands_the_edited_dag(rig: M4Rig) -> None:
    pid = _handoff_one(rig)
    face = rig.m4.reflection
    face.studio_edit(proposal_id=pid, op="add_node",
                     args={"node_id": "n2", "skill_id": "sk_stock_watch_v1.0"})

    accepted = face.studio_decide(proposal_id=pid, action="accept")

    assert accepted["applied"] is True
    assert accepted["flow_id"].endswith("_v1.0")
    raw = rig.m4.store.get("config", f"workflow/{accepted['flow_id']}.json")
    dag = json.loads(raw.decode("utf-8"))
    assert {node["node_id"] for node in dag["nodes"]} == {"n1", "n2"}   # 编辑**进入**落盘


# ───────────────────────── GWT-4 一览与 fail-closed ─────────────────────────


def test_gwt4_sessions_lists_open_sessions(rig: M4Rig) -> None:
    pid = _handoff_one(rig)
    payload = rig.m4.reflection.studio_sessions()
    assert payload["available"] is True
    assert [item["proposal_id"] for item in payload["sessions"]] == [pid]
    assert payload["sessions"][0]["status"] == "editing"


def test_gwt4_subface_absent_is_unavailable_not_faked() -> None:
    """`L6Stack.studio is None` ⇒ `available: false`（**不**伪造画布 / 清单）。"""
    face = ReflectionFacade(l6=SimpleNamespace(studio=None), feedback=lambda *a, **k: None)
    assert face.studio_sessions()["available"] is False
    assert face.studio_canvas("prp_x")["available"] is False
    assert face.studio_skills()["available"] is False
    assert face.studio_edit(proposal_id="prp_x", op="add_node", args={})["available"] is False


def test_gwt4_skills_absent_when_registry_not_wired() -> None:
    """`skills=None`（未注注册表）⇒ 清单子面 fail-closed 并点名，**不伪造空清单**。"""
    face = ReflectionFacade(
        l6=SimpleNamespace(studio=SimpleNamespace(available=True)),
        feedback=lambda *a, **k: None,
    )
    payload = face.studio_skills()
    assert payload["available"] is False
    assert "Skill 注册表" in payload["reason"]
