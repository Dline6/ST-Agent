"""快捷指令注册表（05 §8）。

``/`` 触发的命令注册表：每条 ＝ **预置意图模板**（跳过部分澄清步骤），schema 含
指令名 / 描述 / 意图模板 / 参数预填四项；命名过
[01 §6](../../../docs/技术架构-v2/01-平台共享契约.md) 中性校验；注册表可随官方
Pack 更新。本模块只产**注册与解析面**，不含菜单渲染。

**意图模板**：:data:`INTENT_KINDS` 是 [05 §3.1](../../../docs/技术架构-v2/05-L3-对话主入口.md)
七类意图的**机器可读副本**（同 [04 §1](../../../docs/技术架构-v2/04-L2-记忆图谱.md)
``NODE_TYPES`` 的写法），供 ``T-L3-002`` 的意图分类复用——**不新造第二套词汇**
（假设 A2）。

**更新通道**（假设 A1）：条目按 :data:`SOURCES` 分组，:meth:`CommandRegistry.register_source`
按来源**整组替换**——官方 Pack 一旦承载命令种子即可直接注入，用户自定义条目不受
影响。Pack 侧承载须先改 [03 §1](../../../docs/技术架构-v2/03-L1-能力底座-Skills与MCP.md)
的包结构（铁律 8），不在本任务范围内，已登记
[L3 册 `A1`](../../../项目管理/遗留问题/L3-遗留问题.md)。

**校验点不可关闭**（[01 §6](../../../docs/技术架构-v2/01-平台共享契约.md)）：
:class:`CommandRegistry` 的构造参数**只有规则库**，无 enabled / bypass 形参——
替换来源同样过校验（GWT-5）。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from st_agent.contracts.neutrality import NeutralityGuard
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l3.errors import CommandValidationError, SessionValidationError

__all__ = [
    "COMMAND_PREFIX",
    "DEFAULT_COMMANDS",
    "INTENT_KINDS",
    "SOURCES",
    "CommandRegistry",
    "CommandResolution",
    "IntentKind",
    "ParamValue",
    "QuickCommand",
]

COMMAND_PREFIX = "/"
"""触发前缀（§8：``/`` 触发的命令注册表）。"""

INTENT_KINDS: tuple[str, ...] = (
    "query", "configure", "analyze", "memory_op", "train", "explain", "investigate",
)
"""§3.1 七类意图的枚举（机器可读副本；派发目标的对应关系见 05 §3.1 表）。"""

SOURCES: tuple[str, ...] = ("official", "user")
"""条目来源分组：官方 Pack / 用户自定义（[`T-ECO-001`](../../../项目管理/tasks/T-ECO-001-分享物类型格式导出流程来源追溯链.md) 的分享物亦落 ``user``）。"""

IntentKind = Literal[
    "query", "configure", "analyze", "memory_op", "train", "explain", "investigate",
]
CommandSource = Literal["official", "user"]
ParamValue = str | int | float | bool


class QuickCommand(BaseModel):
    """一条快捷指令（§8 的四字段 + 来源分组）。"""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=32)
    """指令名（**不含**前导 :data:`COMMAND_PREFIX`；用户以 ``/名称`` 触发）。"""
    description: str = Field(min_length=1, max_length=200)
    intent: IntentKind
    """意图模板（[05 §3.1](../../../docs/技术架构-v2/05-L3-对话主入口.md) 的七类之一）。"""
    param_defaults: dict[str, ParamValue] = Field(default_factory=dict)
    """参数预填：键对齐 [01 §2](../../../docs/技术架构-v2/01-平台共享契约.md) ``SkillDescriptor.parameters``
    的声明名，值只做**默认值覆盖**（假设 A3），不引入新参数体系。"""
    source: CommandSource = "official"

    @model_validator(mode="after")
    def _command_shape(self) -> "QuickCommand":
        if COMMAND_PREFIX in self.name or any(c.isspace() for c in self.name):
            raise SessionValidationError(
                f"指令名不得含前缀 {COMMAND_PREFIX!r} 或空白：{self.name!r}（触发时才加前缀）"
            )
        return self


class CommandResolution(BaseModel):
    """一次 ``/`` 输入的解析结果。"""

    model_config = ConfigDict(frozen=True)

    envelope: ResultEnvelope
    command: QuickCommand | None = None
    params: dict[str, ParamValue] = Field(default_factory=dict)
    """命中指令的参数预填（原样携带，供派发面与用户补充参数合并）。"""
    extra_args: tuple[str, ...] = ()
    """用户在指令名之后给出的自由 token（**位置化**，不猜参数名）。"""


class CommandRegistry:
    """快捷指令注册表（§8）。

    :param rulepack: 中性规则库（可随官方 Pack 替换）；**无** enabled / bypass 形参
        ——[01 §6](../../../docs/技术架构-v2/01-平台共享契约.md) 的校验点不可配置关闭。
    :param commands: 种子；``None`` 即用 :data:`DEFAULT_COMMANDS`。
    """

    def __init__(
        self,
        *,
        rulepack=None,
        commands: Iterable[QuickCommand] | None = None,
    ) -> None:
        self._guard = NeutralityGuard(rulepack) if rulepack is not None else NeutralityGuard()
        self._by_source: dict[str, dict[str, QuickCommand]] = {s: {} for s in SOURCES}
        for command in (DEFAULT_COMMANDS if commands is None else commands):
            self.register(command)

    # ───────────────────────── 注册 ─────────────────────────

    def register(self, command: QuickCommand) -> QuickCommand:
        """登记一条指令；命名 / 描述未过 §6 校验即**显式拒**（不静默改名）。"""
        self._check(command)
        bucket = self._by_source[command.source]
        if command.name in bucket:
            raise CommandValidationError(
                f"指令 {COMMAND_PREFIX}{command.name} 已存在（来源 {command.source}）"
                "——同来源内不静默覆盖，改用 register_source 整组替换"
            )
        bucket[command.name] = command
        return command

    def register_source(
        self, source: str, commands: Iterable[QuickCommand]
    ) -> tuple[QuickCommand, ...]:
        """按来源**整组替换**（§8「可随官方 Pack 更新」）。

        **先全量校验、后一次换入**：任一条未过校验即整组拒、注册表保持原样——
        不留「换了一半」的中间态。其余来源的条目不受影响。
        """
        if source not in SOURCES:
            raise CommandValidationError(f"未知来源 {source!r}（须为 {SOURCES} 之一）")
        incoming: dict[str, QuickCommand] = {}
        for command in commands:
            self._check(command)
            if command.source != source:
                raise CommandValidationError(
                    f"指令 {COMMAND_PREFIX}{command.name} 的来源为 {command.source}，"
                    f"与本次替换的 {source} 不一致"
                )
            if command.name in incoming:
                raise CommandValidationError(
                    f"本次替换里指令名重复：{COMMAND_PREFIX}{command.name}"
                )
            incoming[command.name] = command
        self._by_source[source] = incoming
        return tuple(incoming.values())

    # ───────────────────────── 查询 ─────────────────────────

    def get(self, name: str) -> QuickCommand | None:
        """按名字取指令（不含前缀；跨来源查，同名以 ``official`` 优先）。"""
        for source in SOURCES:
            found = self._by_source[source].get(name)
            if found is not None:
                return found
        return None

    def names(self) -> tuple[str, ...]:
        """全部指令名（``official`` 在前，各自按名排序）。"""
        return tuple(c.name for c in self.list())

    def list(self, *, source: str | None = None) -> tuple[QuickCommand, ...]:
        """列出指令（可按来源过滤）。"""
        if source is not None:
            if source not in SOURCES:
                raise CommandValidationError(f"未知来源 {source!r}（须为 {SOURCES} 之一）")
            return tuple(self._by_source[source][n] for n in sorted(self._by_source[source]))
        return tuple(c for s in SOURCES for c in self.list(source=s))

    # ───────────────────────── 解析 ─────────────────────────

    def resolve(self, text: str) -> CommandResolution:
        """解析一段以 ``/`` 开头的输入。

        未命中 → `empty` 信封并**带原因与可用指令清单**（01 §5；不静默当普通
        消息、不猜最相近的一条）。
        """
        raw = (text or "").strip()
        available = "、".join(COMMAND_PREFIX + n for n in self.names()) or "（无）"
        if not raw.startswith(COMMAND_PREFIX):
            return CommandResolution(envelope=ResultEnvelope.empty(
                f"非快捷指令输入（缺 {COMMAND_PREFIX} 前导）；可用指令：{available}"
            ))
        head, *rest = raw.split()
        name = head[len(COMMAND_PREFIX):]
        if not name:
            return CommandResolution(envelope=ResultEnvelope.empty(
                f"缺少指令名；可用指令：{available}"
            ))
        command = self.get(name)
        if command is None:
            return CommandResolution(envelope=ResultEnvelope.empty(
                f"未知指令 {COMMAND_PREFIX}{name}；可用指令：{available}"
            ))
        return CommandResolution(
            envelope=ResultEnvelope.ok(command),
            command=command,
            params=dict(command.param_defaults),
            extra_args=tuple(rest),
        )

    # ───────────────────────── 内部 ─────────────────────────

    def _check(self, command: QuickCommand) -> None:
        """执行 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md) 执行点 1（命名校验）。

        红-绿已验：关掉本方法时 GWT-3 与 GWT-5 的整组拒用例 FAIL。
        """
        verdict = self._guard.check_name(command.name, description=command.description)
        if not verdict.passed:
            hits = "、".join(f"{f.kind}:{f.matched}" for f in verdict.findings)
            raise CommandValidationError(
                f"指令 {COMMAND_PREFIX}{command.name} 未过中性校验（{hits}）",
                suggestions=self._guard.suggest_names(command.name),
            )


DEFAULT_COMMANDS: tuple[QuickCommand, ...] = (
    QuickCommand(
        name="盯盘", description="按给定条件持续跟踪标的，命中即告知",
        intent="configure", param_defaults={"window_days": 30},
    ),
    QuickCommand(
        name="回测", description="在历史数据上回放给定策略，输出绩效与风险指标",
        intent="analyze",
    ),
    QuickCommand(
        name="压测", description="对给定组合做压力测试，输出各情景下的损失估计",
        intent="analyze",
    ),
    QuickCommand(
        name="今日看板", description="生成当日市场与自选范围的看板",
        intent="query",
    ),
    QuickCommand(
        name="反思", description="回顾近期的决策与反馈，输出可复核的复盘摘要",
        intent="train",
    ),
)
"""§8 点名的五条预置指令（``/盯盘`` ``/回测`` ``/压测`` ``/今日看板`` ``/反思``）。"""
