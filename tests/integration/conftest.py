"""T-INT-001 · M0 集成关卡的 fixture（装配 rig 见同目录 ``rig.py``）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from rig import ROOT_NAME, Rig, bare, seeded


@pytest.fixture()
def seeded_rig(tmp_path: Path) -> Rig:
    """已播种数据面的装配（``MarketDb`` 已建库）。"""
    return seeded(tmp_path / ROOT_NAME)


@pytest.fixture()
def bare_rig(tmp_path: Path) -> Rig:
    """无数据面的装配（``MarketDb`` 未建库）。"""
    return bare(tmp_path / ROOT_NAME)
