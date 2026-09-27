"""L2 记忆图谱测试 fixture（真 ``Store`` + 真 ``MemoryGraph``，离线可跑）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from memory_helpers import PASS
from st_agent.l0.storage import Store
from st_agent.l2.memory import MemoryGraph, MemoryReader, MemoryWriter


@pytest.fixture()
def store_root(tmp_path: Path) -> Path:
    return tmp_path / "root"


@pytest.fixture()
def store(store_root: Path) -> Store:
    return Store.create(store_root, PASS)


@pytest.fixture()
def graph(store: Store) -> MemoryGraph:
    return MemoryGraph(store)


@pytest.fixture()
def writer(graph: MemoryGraph) -> MemoryWriter:
    return MemoryWriter(graph)


@pytest.fixture()
def reader(graph: MemoryGraph) -> MemoryReader:
    return MemoryReader(graph)
