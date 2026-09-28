"""T-L3-001.3 · 快捷指令注册表（05 §8；GWT-1..5）。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from st_agent.contracts.neutrality import NeutralityGuard
from st_agent.l3 import CommandValidationError
from st_agent.l3.commands import (
    COMMAND_PREFIX,
    DEFAULT_COMMANDS,
    INTENT_KINDS,
    CommandRegistry,
    QuickCommand,
)

STORIES_FIVE = ("盯盘", "回测", "压测", "今日看板", "反思")


class TestGwt1CommandSchema:
    """GWT-1：§8 四字段齐备、形态固定、可往返。"""

    def test_four_fields_are_present_and_round_trip(self) -> None:
        cmd = QuickCommand(name="盯盘", description="按条件持续跟踪标的",
                           intent="configure", param_defaults={"window_days": 30})
        back = QuickCommand.model_validate_json(cmd.model_dump_json())
        assert back == cmd
        assert set(cmd.model_dump()) >= {"name", "description", "intent", "param_defaults"}

    def test_missing_required_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            QuickCommand(name="盯盘", intent="configure")  # 缺 description

    def test_unknown_intent_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            QuickCommand(name="盯盘", description="x", intent="chat")

    def test_intent_vocabulary_matches_05_3_1(self) -> None:
        assert INTENT_KINDS == ("query", "configure", "analyze",
                                "memory_op", "train", "explain")


class TestGwt2SlashResolution:
    """GWT-2：``/`` 触发解析；未命中带原因与可用清单。"""

    def test_hit_carries_template_and_extra_args(self, registry: CommandRegistry) -> None:
        result = registry.resolve("/盯盘 600000 退市风险")
        assert result.envelope.status == "ok"
        assert result.command is not None and result.command.name == "盯盘"
        assert result.command.intent == "configure"
        assert result.params == {"window_days": 30}
        assert result.extra_args == ("600000", "退市风险")

    def test_plain_text_is_not_silently_treated_as_a_command(
        self, registry: CommandRegistry
    ) -> None:
        result = registry.resolve("帮我看看今天的行情")
        assert result.envelope.status == "empty"
        assert result.command is None
        assert COMMAND_PREFIX + "盯盘" in (result.envelope.reason or "")

    def test_unknown_and_bare_prefix_report_available_commands(
        self, registry: CommandRegistry
    ) -> None:
        for text in ("/不存在的指令", "/"):
            result = registry.resolve(text)
            assert result.envelope.status == "empty"
            assert result.envelope.reason
            assert "盯盘" in result.envelope.reason


class TestGwt3NamingGoesThroughTheNeutralityGuard:
    """GWT-3：命名 / 描述过 01 §6 校验，被拒时给出中性建议。"""

    @pytest.mark.parametrize("bad_name", ["老张盯盘", "激进派回测", "盯盘操盘手"])
    def test_person_name_and_personality_and_suffix_are_rejected(
        self, registry: CommandRegistry, bad_name: str
    ) -> None:
        with pytest.raises(CommandValidationError) as err:
            registry.register(QuickCommand(name=bad_name, description="跟踪标的",
                                           intent="query"))
        assert err.value.suggestions, "拒绝时须给出中性建议"
        assert registry.get(bad_name) is None, "被拒的指令不得入表"

    def test_first_person_in_description_is_rejected(self, registry: CommandRegistry) -> None:
        with pytest.raises(CommandValidationError):
            registry.register(QuickCommand(
                name="盯盘", description="我认为这样盯盘更好", intent="query",
            ))

    def test_guard_has_no_bypass_parameter(self) -> None:
        """校验点不可配置关闭（01 §6）——API 形状上的强制。"""
        import inspect

        params = inspect.signature(CommandRegistry.__init__).parameters
        assert not ({"enabled", "bypass", "validate"} & set(params))
        assert not ({"bypass"} & set(inspect.signature(NeutralityGuard.__init__).parameters))


class TestGwt4DefaultCommands:
    """GWT-4：§8 点名的五条预置指令齐备，且逐条过命名校验。"""

    def test_five_named_commands_are_seeded(self, registry: CommandRegistry) -> None:
        names = registry.names()
        assert set(STORIES_FIVE) <= set(names)
        assert registry.list(source="official"), "预置项来源为 official"

    def test_every_default_command_passes_the_guard(self) -> None:
        guard = NeutralityGuard()
        for cmd in DEFAULT_COMMANDS:
            verdict = guard.check_name(cmd.name, description=cmd.description)
            assert verdict.passed, (cmd.name, verdict.findings)

    def test_default_commands_are_all_well_formed(self) -> None:
        for cmd in DEFAULT_COMMANDS:
            assert cmd.intent in INTENT_KINDS
            assert cmd.name and cmd.description


class TestGwt5UpdateChannel:
    """GWT-5：按来源整组替换；用户条目不受影响；校验不被绕过。"""

    def test_replacing_official_keeps_user_and_revalidates(
        self, registry: CommandRegistry
    ) -> None:
        registry.register(QuickCommand(name="自建指令", description="自定义跟踪",
                                       intent="query", source="user"))
        registry.register_source("official", (
            QuickCommand(name="简报", description="生成当日简报", intent="query"),
        ))

        assert registry.names() == ("简报", "自建指令")
        assert registry.list(source="official")[0].name == "简报"

    def test_a_bad_entry_rejects_the_whole_group_and_leaves_the_registry_intact(
        self, registry: CommandRegistry
    ) -> None:
        before = registry.names()
        with pytest.raises(CommandValidationError):
            registry.register_source("official", (
                QuickCommand(name="简报", description="生成当日简报", intent="query"),
                QuickCommand(name="老张小报", description="生成小报", intent="query"),
            ))
        assert registry.names() == before, "整组拒后不得留「换了一半」的中间态"

    def test_source_mismatch_is_rejected(self, registry: CommandRegistry) -> None:
        with pytest.raises(CommandValidationError):
            registry.register_source("official", (
                QuickCommand(name="简报", description="生成当日简报", intent="query",
                             source="user"),
            ))

    def test_duplicate_within_one_source_is_not_silently_overwritten(
        self, registry: CommandRegistry
    ) -> None:
        with pytest.raises(CommandValidationError):
            registry.register(QuickCommand(name="盯盘", description="另一个盯盘",
                                           intent="query"))
