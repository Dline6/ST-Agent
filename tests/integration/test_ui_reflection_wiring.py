"""表现层适配面（`M4Runtime.reflection`）的**真装配**验收（[`T-UI-004.2`]）。

`tests/ui/` 的那组用替身钉 ui 侧的信封与描述形态；本组用 M4 关卡的真组合根
（``seeded_m4``）证明**接线本身成立**：适配面读得到 L6 的真数据、写面真的经变更流
与授权面生效、输入类失败真的转成 `ValueError`、且这些端点经真回环面出得去。

不重复 `tests/l6/` 已覆盖的单面行为（那些是 L6 各叶的 GWT），只验「组合根 → 适配面
→ 回环面」这一段。
"""

from __future__ import annotations

import http.client
import json
from pathlib import Path

import pytest
from rig import ROOT_NAME
from rig_m4 import seeded_m4

from st_agent.l6 import RESET_CONFIRMATIONS, TIER_CONFIG_ID, TIERS
from st_agent.ui.security import TOKEN_HEADER
from st_agent.ui.server import serve

PROPOSAL = {
    "config_id": TIER_CONFIG_ID,
    "current": "collaborative",
    "suggested": "manual",
    "reason": "先把档位收一档，观察一周再放",
    "trace_ref": "",
}

DELIVERY = "dlv_" + "1" * 20


@pytest.fixture()
def rig(tmp_path: Path):
    return seeded_m4(tmp_path / ROOT_NAME)


def _get(running, path: str, *, token: str | None = None):  # noqa: ANN001, ANN201
    conn = http.client.HTTPConnection(running.host, running.port, timeout=5)
    headers = {} if token is None else {TOKEN_HEADER: token}
    try:
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read())
    finally:
        conn.close()


def test_reflection_facade_reads_the_real_l6_faces(rig) -> None:
    facade = rig.m4.reflection

    assert facade.report_weeks() == ()                 # 未到点 ⇒ 没有任何一期报告
    assert facade.report("2026-W41") is None
    assert facade.changes() == []
    assert facade.experiments() == []
    assert facade.trainings() == []
    assert facade.pending() == []
    assert facade.proposals() == []                    # rig 未脚本化观察 ⇒ 不产提案


def test_authorization_reads_and_switches_through_the_real_face(rig) -> None:
    facade = rig.m4.reflection

    current = facade.authorization()
    assert current["available"] is True
    assert current["tier"] == "collaborative"           # 缺省档（08 §5）
    assert current["tiers"] == list(TIERS)
    assert current["grading"], "风险分级清单应随档位一并可读"

    switched = facade.set_authorization(tier="autonomous")
    assert switched["changed"] is True
    assert facade.authorization()["tier"] == "autonomous"


def test_unknown_tier_is_an_input_error(rig) -> None:
    facade = rig.m4.reflection
    with pytest.raises(ValueError, match="档位须为"):
        facade.set_authorization(tier="whatever")
    with pytest.raises(ValueError, match="不得为空"):
        facade.set_authorization(grading=[])


def test_a_proposal_goes_through_the_change_flow_and_can_be_rolled_back(rig) -> None:
    """提交 → 审批门立案 → 接受生效（产生 `change_id`）→ 回滚（**本身也是一次变更**）。"""
    facade = rig.m4.reflection

    decided = facade.decide(action="accept", proposal=PROPOSAL)   # 提交 + 接受（一次调用）
    assert decided["applied"] is True
    change_id = decided["change"]["change_id"]
    assert facade.authorization()["tier"] == "manual"            # 值真的变了

    timeline = facade.changes()
    assert [item["change_id"] for item in timeline] == [change_id]

    rolled = facade.rollback(change_id)
    assert rolled["applied"] is True
    assert facade.authorization()["tier"] == "collaborative"     # 回到变更前的取值
    assert len(facade.changes()) == 2, "回滚本身也是一次变更（新 change_id、原记录保留）"


def test_a_repeated_decision_is_an_input_error(rig) -> None:
    """重复处置被**显式拒**（不是静默无操作）——就地在适配面转成输入类失败。"""
    facade = rig.m4.reflection
    decided = facade.decide(action="accept", proposal=PROPOSAL)
    pending_id = decided["pending"]["pending_id"] if decided["pending"] else ""
    assert pending_id, "接受后应能取到该条待批准项（留痕保留）"
    with pytest.raises(ValueError, match="已处置过"):
        facade.decide(action="accept", pending_id=pending_id)


def test_reject_keeps_the_value_byte_for_byte(rig) -> None:
    """否决：留痕而配置取值**逐字节不变**（[08 §5]）；延后：排队而非丢弃。"""
    facade = rig.m4.reflection
    before = facade.authorization()["tier"]

    queued = facade.decide(action="reject", proposal=PROPOSAL)
    assert queued["applied"] is False
    assert queued["pending"]["status"] == "rejected"
    assert facade.authorization()["tier"] == before              # 值没动
    assert facade.changes() == []                                # 也没进变更历史

    deferred = facade.decide(action="defer", proposal=PROPOSAL)
    assert deferred["pending"]["status"] == "deferred"
    assert deferred["pending"]["deferrals"] == 1                 # 排队可再处置
    assert facade.authorization()["tier"] == before


def test_missing_targets_are_input_errors(rig) -> None:
    facade = rig.m4.reflection
    with pytest.raises(ValueError, match="待批准项"):
        facade.decide(action="accept", pending_id="pnd_absent")
    with pytest.raises(ValueError, match="变更不存在"):
        facade.rollback("chg_absent")
    with pytest.raises(ValueError, match="处置动作"):
        facade.decide(action="frobnicate", pending_id="pnd_1")


def test_factory_reset_needs_three_confirmations(rig) -> None:
    facade = rig.m4.reflection
    with pytest.raises(ValueError, match=f"{RESET_CONFIRMATIONS} 次确认"):
        facade.factory_reset(RESET_CONFIRMATIONS - 1)
    with pytest.raises(ValueError, match="整数"):
        facade.factory_reset("3")
    assert facade.factory_reset(RESET_CONFIRMATIONS)["performed"] is True


def test_feedback_is_produced_by_the_interaction_layer_and_lands_in_the_pool(rig) -> None:
    """反馈**由 L3 采集面产生**（`feedback_id` 由它铸）、经事件进 L6 反思数据池（[08 §1]）。"""
    collected = rig.m4.reflection.record_feedback(
        target={"kind": "delivery", "ref": DELIVERY}, action="rejected", reason="估值已偏高",
    )
    assert collected.status == "ok"
    feedback_id = collected.data.event.feedback_id
    assert feedback_id.startswith("fb_")
    assert collected.data.delivery.delivered is True

    entries = rig.m4.l6.pool.all()
    assert [entry.event.feedback_id for entry in entries] == [feedback_id]
    assert entries[0].event.reason == "估值已偏高"


def test_bad_feedback_payload_is_rejected_by_the_collector(rig) -> None:
    facade = rig.m4.reflection
    rejected = facade.record_feedback(
        target={"kind": "delivery", "ref": DELIVERY}, action="rejected",   # 缺原因
    )
    assert rejected.status == "validation_failed"
    assert "reason" in rejected.reason
    with pytest.raises(ValueError, match="反馈对象"):
        facade.record_feedback(target=None, action="adopted")


def test_the_face_is_reachable_through_the_loopback_endpoints(rig) -> None:
    """组合根 → 适配面 → 回环面：真 runtime 的适配面经端口注入后，端点真的出得去。"""
    with serve(dev=False, reflection=rig.m4.reflection) as running:
        status, status_payload = _get(
            running, "/api/reflection/status", token=running.token
        )
        _, report = _get(running, "/api/reflection/reports", token=running.token)
        _, proposals = _get(running, "/api/reflection/proposals", token=running.token)
    assert status == 200 and status_payload["status"] == "ok"
    assert report["status"] == "empty"                  # 未到点 ⇒ 明确「尚未有报告」
    assert proposals["status"] == "ok"
    assert proposals["data"]["component_type"] == "proposal_card"


def test_endpoints_require_the_token(rig) -> None:
    with serve(dev=False, reflection=rig.m4.reflection) as running:
        assert _get(running, "/api/reflection/reports")[0] == 401
        assert _get(running, "/api/reflection/reports", token="wrong")[0] == 401
