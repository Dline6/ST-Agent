"""L6 层测试夹具（真 ``Store``，离线可跑）。

不注入替身——池子的落盘 / 幂等 / 损坏与周报条目的落值留痕必须对着真 ``Store``
才验得出来（同 ``tests/l5/conftest.py`` 的口径）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from l6_helpers import NOW, PASS
from st_agent.l0.storage import Store
from st_agent.l6 import FeedbackPool


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def pool(store: Store) -> FeedbackPool:
    """带 ``Store`` 的反思数据池——落盘与幂等用例用它。"""
    return FeedbackPool(store=store, now=lambda: NOW)
