"""`T-INT-003` 测试：官方数据维度声明（[`l1/skills/dimensions.py`](../../src/st_agent/l1/skills/dimensions.py)）。

该声明是盲点判定「维度 → 产出 Skill」的**唯一真相源**（[06 §3](../../docs/技术架构-v2/06-L4-多视角推理.md)）。
它与官方 Pack 同处一包、**随官方 Skill 增删同步**——故此处钉住三处**易漂移**的耦合：
声明里的 base 必须在 Pack 里存在、承载表必须在 schema 里存在、L0 的数据域必须在声明里有着落。
"""

from __future__ import annotations

import re
from pathlib import Path

from st_agent.l0.market import DOMAIN_TASKS
from st_agent.l1.skills.dimensions import OFFICIAL_DIMENSIONS
from st_agent.l1.skills.pack import OFFICIAL_PACK

SCHEMA = Path(__file__).resolve().parents[2] / "src" / "st_agent" / "l0" / "market" / "schema.sql"

_PACK_BASES = {seed["base"] for seed in OFFICIAL_PACK}


def _schema_objects() -> set[str]:
    text = SCHEMA.read_text(encoding="utf-8")
    return set(re.findall(r"CREATE\s+(?:TABLE|VIEW)\s+(\w+)", text, flags=re.IGNORECASE))


def test_declared_producers_exist_in_the_official_pack() -> None:
    """声明的产出 Skill 必须在官方 Pack 里——否则维度页会指向不存在的能力。"""
    unknown = {
        base
        for dim in OFFICIAL_DIMENSIONS
        for base in dim.skill_bases
        if base not in _PACK_BASES
    }
    assert not unknown, f"维度声明引用了官方 Pack 里没有的 Skill base：{sorted(unknown)}"


def test_declared_tables_exist_in_the_schema() -> None:
    """承载表必须在本地市场库里真实存在——本地没有的数据面不该被声明成维度。"""
    objects = _schema_objects()
    unknown = {
        table for dim in OFFICIAL_DIMENSIONS for table in dim.tables
        if table not in objects
    }
    assert not unknown, f"维度声明引用了 schema 里没有的表：{sorted(unknown)}"


def test_every_l0_data_domain_has_a_declaration() -> None:
    """L0 的每个数据域都要在声明里有着落——否则该域永远进不了盲点判定。"""
    declared = {dim.key for dim in OFFICIAL_DIMENSIONS}
    assert set(DOMAIN_TASKS) <= declared, (
        f"L0 数据域缺维度声明：{sorted(set(DOMAIN_TASKS) - declared)}"
    )


def test_keys_and_tables_are_disjoint() -> None:
    """维度键唯一、承载表不跨维度（一张表归一个域）——声明自洽，无歧义归属。"""
    keys = [dim.key for dim in OFFICIAL_DIMENSIONS]
    assert len(keys) == len(set(keys))
    tables = [t for dim in OFFICIAL_DIMENSIONS for t in dim.tables]
    assert len(tables) == len(set(tables))
    assert all(dim.label and dim.key for dim in OFFICIAL_DIMENSIONS), "键与标签皆不得为空"
