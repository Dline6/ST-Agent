"""T-L1-013.2 · 官方资源包容器与装载入口（01 §13；GWT-1..4）。"""

from __future__ import annotations

import pytest

from st_agent.contracts.registry_types import SemVer
from st_agent.l1.pack import (
    OFFICIAL_RESOURCES,
    SKILL_KIND,
    OfficialPack,
    PackLoaderMissingError,
    PackResourceError,
    ResourceEntry,
    load_official_pack,
)
from st_agent.l1.skills.pack import OFFICIAL_PACK


def _entry(kind: str, *, major: int = 1, minor: int = 0, payload: object = None) -> ResourceEntry:
    return ResourceEntry(kind=kind, version=SemVer(major=major, minor=minor), payload=payload)


class TestGwt1KindDispatch:
    """GWT-1：按 kind 分派；未注册 loader 的 kind 显式拒。"""

    def test_dispatches_each_kind_to_its_loader(self) -> None:
        pack = OfficialPack(entries=())
        pack.add(_entry("command", payload=("a", "b")))
        pack.add(_entry("rulepack", payload={"x": 1}))
        calls: list[str] = []

        def command_loader(entry: ResourceEntry) -> object:
            calls.append("command")
            return tuple(entry.payload)

        def rulepack_loader(entry: ResourceEntry) -> object:
            calls.append("rulepack")
            return dict(entry.payload)  # type: ignore[arg-type]

        results = load_official_pack(
            pack, {"command": command_loader, "rulepack": rulepack_loader}
        )
        assert results == (("a", "b"), {"x": 1})
        assert calls == ["command", "rulepack"]

    def test_unregistered_kind_is_explicitly_rejected(self) -> None:
        pack = OfficialPack(entries=())
        pack.add(_entry("command", payload=()))
        with pytest.raises(PackLoaderMissingError) as exc:
            load_official_pack(pack, {})
        assert exc.value.kind == "command"


class TestGwt2IdempotentLoad:
    """GWT-2：同 kind 同 version 重复装载不重复、不静默覆盖。"""

    def test_same_kind_version_is_not_loaded_twice(self) -> None:
        pack = OfficialPack(entries=())
        pack.add(_entry("command", payload=("seed",)))
        seen: list[object] = []

        def loader(entry: ResourceEntry) -> object:
            seen.append(entry.payload)
            return entry.payload

        first = load_official_pack(pack, {"command": loader})
        second = load_official_pack(pack, {"command": loader})
        assert first == (("seed",),)
        assert second == ()          # 幂等：第二次不再调用 loader
        assert seen == [("seed",)]

    def test_add_is_idempotent_by_kind_and_version(self) -> None:
        pack = OfficialPack(entries=())
        pack.add(_entry("command", payload=("first",)))
        pack.add(_entry("command", payload=("second",)))   # 同 (kind, version)
        entries = pack.entries(kind="command")
        assert len(entries) == 1
        assert entries[0].payload == ("first",)            # 不静默覆盖

    def test_different_version_is_a_distinct_entry(self) -> None:
        pack = OfficialPack(entries=())
        pack.add(_entry("command", major=1, minor=0))
        pack.add(_entry("command", major=1, minor=1))
        assert len(pack.entries(kind="command")) == 2


class TestGwt3SkillSeedIntact:
    """GWT-3：Skill 种子仍生效——容器引用 OFFICIAL_PACK，不复制。"""

    def test_skill_kind_references_official_pack(self) -> None:
        (skill,) = [e for e in OFFICIAL_RESOURCES if e.kind == SKILL_KIND]
        assert skill.payload is OFFICIAL_PACK          # 同一对象（引用，非复制）
        assert len(OFFICIAL_PACK) == 12

    def test_default_pack_carries_skill_kind(self) -> None:
        pack = OfficialPack()                          # 缺省＝L1 自有的 OFFICIAL_RESOURCES
        assert pack.kinds() == (SKILL_KIND,)


class TestGwt4EntryShape:
    """GWT-4：条目形状校验；payload 原样承载（不解读）。"""

    def test_empty_kind_is_rejected(self) -> None:
        with pytest.raises(PackResourceError):
            ResourceEntry(kind="", version=SemVer(major=1, minor=0), payload=None)

    def test_non_semver_version_is_rejected(self) -> None:
        with pytest.raises(PackResourceError):
            ResourceEntry(kind="command", version="1.0", payload=None)  # type: ignore[arg-type]

    def test_payload_is_carried_verbatim(self) -> None:
        payload = object()
        entry = _entry("onboarding_questions", payload=payload)
        assert entry.payload is payload
        assert entry.key == ("onboarding_questions", SemVer(major=1, minor=0))
