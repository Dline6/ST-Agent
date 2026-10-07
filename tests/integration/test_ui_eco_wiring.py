"""生态面的表现层入口在**真装配**下端到端（[`T-UI-004.4`]）。

`tests/ui/test_ui_eco.py` 用替身钉信封与描述形态；本组用 M4 关卡的真组合根经**真回环面**走一遍：
清单 → 确认 → 导出（真落文件）→ 放进收件目录 → 校验四段 → 安装（真写入）→ 留痕可查，
外加官方索引的**不可用**态、越界警示的**空**态与真实的禁用面。
"""

from __future__ import annotations

import http.client
import json
import shutil
from pathlib import Path

import pytest
from rig import ROOT_NAME
from rig_m4 import seeded_m4

PERMISSION = "local_read:<data_cache/**>"

from st_agent.ui.security import TOKEN_HEADER
from st_agent.ui.server import serve


@pytest.fixture()
def rig(tmp_path: Path):
    return seeded_m4(tmp_path / ROOT_NAME)


@pytest.fixture()
def running(rig):
    with serve(dev=False, eco=rig.m4.ecosystem) as ui:
        yield ui


def _call(running, method: str, path: str, body: dict | None = None):  # noqa: ANN001, ANN201
    conn = http.client.HTTPConnection(running.host, running.port, timeout=5)
    headers = {TOKEN_HEADER: running.token}
    payload = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    try:
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read())
    finally:
        conn.close()


def test_export_plan_lists_what_will_be_shared(running) -> None:
    _, payload = _call(running, "GET", "/api/eco/export/plan")
    assert payload["status"] == "ok"
    assert payload["data"]["component_type"] == "report_card"
    assert payload["data"]["slots"]["sections"][0]["title"] == "本次导出包含以下公开信息"


def test_memory_export_writes_into_the_exports_directory(running, rig) -> None:
    status, payload = _call(
        running, "POST", "/api/eco/export",
        {"kind": "mem", "author": "本机用户", "confirmed_by": "user"},
    )
    assert status == 200 and payload["status"] == "ok"
    written = Path(payload["data"]["path"])
    assert written.is_file() and written.suffix == ".stmem"
    assert written.parent == Path(rig.root) / "exports"      # 只落在导出目录
    assert payload["data"]["checksum"] == payload["data"]["card"]["checksum"]


def test_export_without_confirmation_is_refused(running, rig) -> None:
    _, payload = _call(
        running, "POST", "/api/eco/export", {"kind": "mem", "author": "本机用户"}
    )
    assert payload["status"] == "validation_failed"
    assert not list((Path(rig.root) / "exports").glob("*.stmem")), "未确认 ⇒ 不生成文件"


def test_the_memory_export_round_trip(running, rig) -> None:
    """`.stmem` 的强制三步 + 收件 → 校验四段；空图谱的片段**装不了**（导入记录须有节点结果）。"""
    _, exported = _call(
        running, "POST", "/api/eco/export",
        {"kind": "mem", "author": "本机用户", "confirmed_by": "user"},
    )
    source = Path(exported["data"]["path"])
    shutil.copy(source, Path(rig.root) / "inbox" / source.name)

    _, inbox = _call(running, "GET", "/api/eco/inbox")
    assert [row[0] for row in inbox["data"]["slots"]["rows"]] == [source.name]

    _, review = _call(
        running, "POST", "/api/eco/import/review",
        {"file_name": source.name, "received_from": "本机用户"},
    )
    assert review["status"] == "ok"
    assert [section["title"] for section in review["data"]["slots"]["sections"]] == [
        "能力", "权限申请", "依赖", "来源追溯",
    ]

    _, permissions = _call(
        running, "POST", "/api/eco/import/permissions",
        {"file_name": source.name, "received_from": "本机用户"},
    )
    assert permissions["status"] == "ok"
    assert permissions["data"]["slots"]["entries"] == []      # `.stmem` 无权限声明

    _, refused = _call(
        running, "POST", "/api/eco/import/install",
        {"file_name": source.name, "confirmed_by": "user", "received_from": "本机用户"},
    )
    assert refused["status"] == "validation_failed", "空图谱导出的片段没有可导入的节点"
    assert "节点结果" in refused["reason"]


def test_the_skill_export_round_trip_and_the_already_installed_refusal(running, rig) -> None:
    """`.stskill` 的导出 → 收件 → 校验 → 安装：同 id 已装着 ⇒ **显式拒**（不静默覆盖）。"""
    descriptor = rig.m4.m1.runtime.skills.list_all()[0]
    _, exported = _call(
        running, "POST", "/api/eco/export",
        {"kind": "skill", "ref": descriptor.skill_id, "author": "本机用户"},
    )
    assert exported["status"] == "ok", exported.get("reason")
    assert exported["data"]["path"].endswith(".stskill")
    source = Path(exported["data"]["path"])
    shutil.copy(source, Path(rig.root) / "inbox" / source.name)

    _, review = _call(
        running, "POST", "/api/eco/import/review",
        {"file_name": source.name, "received_from": "alice"},
    )
    assert review["status"] == "ok", review
    sections = review["data"]["slots"]["sections"]
    assert descriptor.name in sections[0]["lines"][0]
    assert sections[1]["lines"] == ["无声明（不经本机的文件 / 网络 / 命令出口）"]

    _, refused = _call(
        running, "POST", "/api/eco/import/install",
        {"file_name": source.name, "confirmed_by": "user", "received_from": "alice"},
    )
    assert refused["status"] == "validation_failed"
    assert "已存在" in refused["reason"], "同 id 已装着即显式拒（升级走 publish_version）"


def test_a_shared_skill_needs_each_permission_approved(running, rig) -> None:
    """带权限声明的 `.stskill`：**未全部批准即拒安装**；逐项批准后才装得上（01 §10）。"""
    from st_agent.eco import ShareContainer

    descriptor = rig.m4.m1.runtime.skills.list_all()[0].model_copy(
        update={"skill_id": "sk_sharedma_v1.0", "permissions": (PERMISSION,)}
    )
    container = ShareContainer.pack(
        descriptor, author="alice", sharer="alice", origin_chain=("bob",),
    )
    container.save_to(Path(rig.root) / "inbox" / "shared.stskill")

    _, review = _call(
        running, "POST", "/api/eco/import/review",
        {"file_name": "shared.stskill", "received_from": "alice"},
    )
    assert review["status"] == "ok", review
    permission_lines = review["data"]["slots"]["sections"][1]["lines"]
    assert any(PERMISSION in line and "pending" in line for line in permission_lines)

    _, refused = _call(
        running, "POST", "/api/eco/import/install",
        {"file_name": "shared.stskill", "confirmed_by": "user", "received_from": "alice"},
    )
    assert refused["status"] == "validation_failed"
    assert "未全部批准" in refused["reason"]

    _, permissions = _call(
        running, "POST", "/api/eco/import/permissions",
        {"file_name": "shared.stskill", "received_from": "alice"},
    )
    entry = permissions["data"]["slots"]["entries"][0]
    assert entry["identifier"] == PERMISSION and entry["actions"] == ["approve", "reject"]

    _, approved = _call(
        running, "POST", "/api/eco/import/decide",
        {"file_name": "shared.stskill", "permission": PERMISSION, "action": "approve"},
    )
    assert approved["status"] == "ok" and approved["data"]["decision"] == "approve"

    _, installed = _call(
        running, "POST", "/api/eco/import/install",
        {"file_name": "shared.stskill", "confirmed_by": "user", "received_from": "alice"},
    )
    assert installed["status"] == "ok", installed.get("reason")
    assert installed["data"]["installed_id"] == "sk_sharedma_v1.0"


def test_a_file_without_a_recorded_sharer_needs_the_source(running, rig) -> None:
    """文件未记来源时须由用户补录（09 §5）；缺来源是**输入类**失败，不是内部错。"""
    _, exported = _call(
        running, "POST", "/api/eco/export",
        {"kind": "mem", "author": "本机用户", "confirmed_by": "user"},
    )
    name = Path(exported["data"]["path"]).name
    shutil.copy(Path(exported["data"]["path"]), Path(rig.root) / "inbox" / name)
    _, missing = _call(running, "POST", "/api/eco/import/review", {"file_name": name})
    assert missing["status"] == "validation_failed"
    assert "来源" in missing["reason"]


def test_install_without_confirmation_is_refused(running, rig) -> None:
    _, exported = _call(
        running, "POST", "/api/eco/export",
        {"kind": "mem", "author": "本机用户", "confirmed_by": "user"},
    )
    name = Path(exported["data"]["path"]).name
    shutil.copy(Path(exported["data"]["path"]), Path(rig.root) / "inbox" / name)
    _, payload = _call(running, "POST", "/api/eco/import/install", {"file_name": name})
    assert payload["status"] == "validation_failed"
    assert "用户确认" in payload["reason"]


def test_a_missing_file_is_an_input_error(running) -> None:
    _, payload = _call(running, "POST", "/api/eco/import/review", {"file_name": "nope.stskill"})
    assert payload["status"] == "validation_failed"
    assert "读不到" in payload["reason"]


def test_a_path_is_not_accepted_as_a_file_name(running) -> None:
    """**只接受收件目录内的文件名**——不提供按任意路径读写的本地原语（09 §6）。"""
    _, payload = _call(
        running, "POST", "/api/eco/import/review", {"file_name": "../../etc/passwd"}
    )
    assert payload["status"] == "validation_failed"
    assert "不接受路径" in payload["reason"]


def test_index_is_unavailable_without_an_endpoint(running) -> None:
    _, payload = _call(running, "GET", "/api/eco/index")
    assert payload["status"] == "unavailable"
    assert "官方 Skill 索引不可用" in payload["reason"]


def test_no_violations_yet_is_an_empty_state(running) -> None:
    _, payload = _call(running, "GET", "/api/eco/violations")
    assert payload["status"] == "empty"
    assert payload["data"] is None


def test_disabling_an_ability_goes_through_the_sandbox(running, rig) -> None:
    descriptor = rig.m4.m1.runtime.skills.list_all()[0]
    _, payload = _call(
        running, "POST", "/api/eco/violations/disable", {"skill_id": descriptor.skill_id}
    )
    assert payload["status"] == "ok"
    assert rig.m4.m1.runtime.sandbox.is_disabled(descriptor.skill_id) is True

    _, history = _call(running, "GET", "/api/eco/violations/history")
    assert history["data"]["component_type"] == "table"


def test_eco_endpoints_require_the_token(running) -> None:
    conn = http.client.HTTPConnection(running.host, running.port, timeout=5)
    try:
        conn.request("GET", "/api/eco/inbox")
        assert conn.getresponse().status == 401
    finally:
        conn.close()
