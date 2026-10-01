"""T-L1-013.3 · 快捷指令种子经官方 Pack 分发（01 §13 / 05 §8；GWT-1 / GWT-2）。"""

from __future__ import annotations

from st_agent.l1.pack import OfficialPack, load_official_pack
from st_agent.l3.commands import (
    COMMAND_KIND,
    DEFAULT_COMMANDS,
    CommandRegistry,
    official_command_entry,
)

STORIES_FIVE = ("盯盘", "回测", "压测", "今日看板", "反思")


def _empty_registry() -> CommandRegistry:
    return CommandRegistry(commands=())


def test_official_command_entry_shape() -> None:
    entry = official_command_entry()
    assert entry.kind == COMMAND_KIND == "command"
    assert entry.payload is DEFAULT_COMMANDS          # 引用（非复制）


def test_pack_dispatch_registers_official_commands() -> None:
    """GWT-1：官方指令集来自 Pack，5 条预置齐全。"""
    pack = OfficialPack(entries=())
    pack.add(official_command_entry())
    registry = _empty_registry()

    load_official_pack(pack, {
        COMMAND_KIND: lambda entry: registry.register_source("official", entry.payload),
    })
    official = registry.list(source="official")
    assert tuple(c.name for c in official) == tuple(sorted(STORIES_FIVE))
    assert set(registry.names()) >= set(STORIES_FIVE)


def test_pack_dispatch_replaces_official_group_only() -> None:
    """GWT-2：整组替换——用户自定义条目不受影响（05 §8 / T-L3-001.3 口径）。"""
    from st_agent.l3.commands import QuickCommand

    registry = _empty_registry()
    registry.register(QuickCommand(name="我的看板", description="自建看板",
                                   intent="query", source="user"))
    pack = OfficialPack(entries=())
    pack.add(official_command_entry())

    load_official_pack(pack, {
        COMMAND_KIND: lambda entry: registry.register_source("official", entry.payload),
    })
    assert [c.name for c in registry.list(source="user")] == ["我的看板"]
    assert len(registry.list(source="official")) == 5
