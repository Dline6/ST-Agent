"""L5 层测试夹具（真 ``Store``，离线可跑）。

不注入替身——预算条目的落盘 / 变更留痕 / 门面读值必须对着真 ``Store`` 才验得出来。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from l5_helpers import NOW, PASS
from st_agent.l0.storage import Store
from st_agent.l5 import AttentionBudget


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def budget() -> AttentionBudget:
    """纯内存态预算（不落盘）——判定与缺省矩阵的用例用它。"""
    return AttentionBudget()


@pytest.fixture()
def stored_budget(store: Store) -> AttentionBudget:
    """带 ``Store`` 的预算——落盘、留痕与门面用例用它。"""
    return AttentionBudget(store=store, now=lambda: NOW)
