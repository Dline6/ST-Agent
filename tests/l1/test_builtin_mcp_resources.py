"""T-AGT-019 测试：MCP resources 内置原语（03 §5.5）。

GWT 对照：
- GWT-1：``sk_mcp_list_resources`` 返回清单。
- GWT-2：``sk_mcp_read_resource`` 返回内容，并在 ``execution_log`` 留 ``skill_run`` 记录。
- GWT-3：Server 不支持 resources → 显式 ``unavailable``。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from st_agent.l0.storage import Store
from st_agent.l1.builtin import BUILTIN_PRIMITIVES, install_builtins
from st_agent.l1.builtin.mcp_resources import MCP_LIST_RESOURCES, MCP_READ_RESOURCE
from st_agent.l1.mcp import McpServerRegistry
from st_agent.l1.runner import RUN_PREFIX, SkillRunner
from st_agent.l1.runner.models import SkillRun
from st_agent.l1.skills import SkillRegistry

PASS = "correct horse battery staple"
STUB = Path(__file__).resolve().parent / "_mcp_stub_server.py"
LIST_ID = "sk_mcp_list_resources_v1.0"
READ_ID = "sk_mcp_read_resource_v1.0"


def _add(registry: McpServerRegistry, server_id: str, *extra: str) -> None:
    registry.add_server(
        server_id, display_name=f"资源存根 {server_id}",
        command=sys.executable, args=[str(STUB), *extra],
    )


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def servers(store: Store) -> McpServerRegistry:
    registry = McpServerRegistry(store)
    _add(registry, "stub")
    _add(registry, "stub_nr", "--no-resources")
    return registry


@pytest.fixture()
def runner(store: Store, servers: McpServerRegistry) -> SkillRunner:
    skills = SkillRegistry(store)
    pipeline = SkillRunner(store, skills)
    install_builtins(
        skills, pipeline,
        context={"mcp_servers": servers},
        primitives=(MCP_LIST_RESOURCES, MCP_READ_RESOURCE),
    )
    return pipeline


def _run(pipeline: SkillRunner, skill_id: str, inputs: dict):
    return pipeline.run(skill_id, inputs=inputs)


def test_seed_declarations():
    assert MCP_LIST_RESOURCES in BUILTIN_PRIMITIVES
    assert MCP_READ_RESOURCE in BUILTIN_PRIMITIVES
    assert MCP_LIST_RESOURCES.base == "sk_mcp_list_resources"
    assert MCP_READ_RESOURCE.base == "sk_mcp_read_resource"
    assert MCP_LIST_RESOURCES.requires == ("mcp_servers",)
    assert MCP_READ_RESOURCE.requires == ("mcp_servers",)
    assert MCP_LIST_RESOURCES.permissions == ()
    assert MCP_READ_RESOURCE.permissions == ()


def test_list_returns_resources_and_keeps_a_skill_run_record(store, runner):
    out = _run(runner, LIST_ID, {"server_id": "stub"})
    assert out.envelope.status == "ok"
    data = out.envelope.data
    assert data["server_id"] == "stub"
    assert data["count"] == 2
    assert {item["uri"] for item in data["resources"]} == {
        "stub://watchlist/default", "stub://notes/readme",
    }
    record = SkillRun.model_validate_json(
        store.get("execution_log", f"{RUN_PREFIX}{out.skill_run_id}.json")
    )
    assert record.inputs == {"server_id": "stub"}
    assert record.envelope.status == "ok"


def test_read_returns_content_and_keeps_a_skill_run_record(store, runner):
    out = _run(runner, READ_ID, {"server_id": "stub", "uri": "stub://notes/readme"})
    assert out.envelope.status == "ok"
    content = out.envelope.data["contents"][0]
    assert content["uri"] == "stub://notes/readme"
    assert content["mime_type"] == "text/plain"
    assert content["text"].startswith("离线存根资源")
    record = store.get("execution_log", f"{RUN_PREFIX}{out.skill_run_id}.json")
    assert b"stub://notes/readme" in record


def test_unsupported_resources_is_unavailable(runner):
    out = _run(runner, LIST_ID, {"server_id": "stub_nr"})
    assert out.envelope.status == "unavailable"
    assert "未声明 resources" in (out.envelope.reason or "")


def test_unknown_server_lists_registered_ids(runner):
    out = _run(runner, LIST_ID, {"server_id": "missing"})
    assert out.envelope.status == "unavailable"
    reason = out.envelope.reason or ""
    assert "未注册" in reason
    assert "stub" in reason and "stub_nr" in reason


def test_missing_server_id_is_validation_failed(runner):
    out = _run(runner, LIST_ID, {})
    assert out.envelope.status == "validation_failed"
    assert "缺少 server_id" in (out.envelope.reason or "")


def test_invalid_server_id_shape_is_validation_failed(runner):
    out = _run(runner, LIST_ID, {"server_id": "Bad Id!"})
    assert out.envelope.status == "validation_failed"


def test_missing_uri_is_validation_failed(runner):
    out = _run(runner, READ_ID, {"server_id": "stub"})
    assert out.envelope.status == "validation_failed"
    assert "uri" in (out.envelope.reason or "")


def test_disabled_server_is_unavailable(runner, servers):
    servers.disable_server("stub")
    out = _run(runner, LIST_ID, {"server_id": "stub"})
    assert out.envelope.status == "unavailable"
    assert "已禁用" in (out.envelope.reason or "")
