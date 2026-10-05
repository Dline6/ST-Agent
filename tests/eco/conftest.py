"""ECO 层测试夹具（真 ``Store`` + 真注册表 / 图谱，离线可跑）。

四类分享物的取材面各一份**真件**：L1 的 ``SkillRegistry``（含官方 Pack 播种）与
``WorkflowStore``、L4 的 ``LensRoster``、L2 的 ``MemoryGraph`` + ``MemoryShare``。
不注入替身——容器的往返与导出取材必须对着真件才验得出来。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eco_helpers import NOW, PASS
from st_agent.eco import ShareExporter
from st_agent.l0.storage import Store
from st_agent.l1.skills import SkillRegistry
from st_agent.l1.skills.pack import ensure_official_pack
from st_agent.l1.workflow.store import WorkflowStore
from st_agent.l2.memory import MemoryGraph, MemoryShare, MemoryWriter
from st_agent.l4.roster import LensRoster


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def skills(store: Store) -> SkillRegistry:
    registry = SkillRegistry(store)
    ensure_official_pack(registry)
    return registry


@pytest.fixture()
def workflows(store: Store, skills: SkillRegistry) -> WorkflowStore:
    return WorkflowStore(store, skills)


@pytest.fixture()
def lenses(store: Store, skills: SkillRegistry) -> LensRoster:
    roster = LensRoster(store, skills=skills)
    roster.seed_builtin()
    return roster


@pytest.fixture()
def graph(store: Store) -> MemoryGraph:
    return MemoryGraph(store)


@pytest.fixture()
def writer(graph: MemoryGraph) -> MemoryWriter:
    return MemoryWriter(graph)


@pytest.fixture()
def memory(graph: MemoryGraph) -> MemoryShare:
    return MemoryShare(graph)


@pytest.fixture()
def exporter(skills, workflows, lenses, memory) -> ShareExporter:
    """四类取材面齐备的导出面（时钟固定，容器与卡片可复现）。"""
    return ShareExporter(
        skills=skills, workflows=workflows, lenses=lenses, memory=memory,
        now=lambda: NOW,
    )
