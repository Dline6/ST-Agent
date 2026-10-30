"""本地命令原语 ``sk_run_command``（T-AGT-016.1；[01 §10](../../../docs/技术架构-v2/01-平台共享契约.md)）。

执行路径唯一：``ctx.sandbox.run_command``——由 L1 沙箱核对本 Skill 的
``exec_command`` **已声明且已批准**，越界 / 未批准一律拦截且**不执行**原操作
（01 §10）。原语自身再读 [01 §7](../../../docs/技术架构-v2/01-平台共享契约.md)
的 ``local-exec/enabled``（缺省 **关**）：关闭时在触碰执行器之前 ``unavailable``，
**零执行**、零留痕。

实际进程执行与留痕归 L0 :class:`~st_agent.l0.exec.CommandRunner`：一次性工作目录、
超时终止、stdout / stderr 分别截断、每次落 ``execution_log/exec-command/`` 一条。

**风险**：命令以当前用户身份运行，能访问该用户可访问的路径；cwd 是起始目录而非
jail（进程级隔离未实现）。故出厂清单把 ``skill.sk_run_command`` 显式登记为
``collaborative-required``——循环碰到它即交还用户逐次确认。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from st_agent.contracts.capability_types import ParameterSpec
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.exec.command import (
    COMMAND_LOG_PREFIX,
    DEFAULT_MAX_OUTPUT_CHARS,
    DEFAULT_TIMEOUT_S,
    MAX_OUTPUT_CHARS,
    MAX_TIMEOUT_S,
)
from st_agent.l0.exec.errors import CommandConfigError, CommandError
from st_agent.l0.exec.exec_config import LOCAL_EXEC_DISCLOSURE, get_local_exec_enabled
from st_agent.l1.builtin.models import BuiltinPrimitive

__all__ = ["COMMAND_LOG_PREFIX", "CommandAccess", "RUN_COMMAND"]

_OUTPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "command": {"type": "string"},
        "cwd": {"type": "string"},
        "exit_code": {"type": "integer"},
        "stdout": {"type": "string"},
        "stderr": {"type": "string"},
        "stdout_chars": {"type": "integer"},
        "stderr_chars": {"type": "integer"},
        "truncated": {"type": "boolean"},
        "timed_out": {"type": "boolean"},
        "duration_ms": {"type": "integer"},
    },
}


@dataclass(frozen=True)
class CommandAccess:
    """本地命令的运行态入口（配置 + 执行器）。

    ``runner`` 每次调用现读开关，故设置页开启后对下一次调用立即生效，不需重启。
    """

    store: Any
    runner: Any


def _now() -> datetime:
    return datetime.now().astimezone()


def _number_param(params: dict[str, Any], name: str, default: float,
                  minimum: float, maximum: float) -> float | None:
    raw = params.get(name, default)
    if raw is None or raw == "":
        return default
    if isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value < minimum or value > maximum:
        return None
    return value


def _int_param(params: dict[str, Any], name: str, default: int,
               minimum: int, maximum: int) -> int | None:
    raw = params.get(name, default)
    if raw is None or raw == "":
        return default
    if isinstance(raw, bool):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value < minimum or value > maximum:
        return None
    return value


def _executor(context: Any) -> Any:
    access: CommandAccess = context["commands"]

    def execute(ctx: Any, params: dict[str, Any]) -> ResultEnvelope:
        if ctx.sandbox is None:
            return ResultEnvelope.unavailable(
                "本地命令原语需要执行沙箱核对 exec_command，当前未接线"
                "（组合根未把沙箱注入执行上下文）——本次未执行任何命令",
                last_updated_at=_now(),
            )
        command = str(params.get("command") or "").strip()
        if not command:
            return ResultEnvelope.validation_failed("run_command 需要非空的命令（command）")
        timeout_s = _number_param(
            params, "timeout_s", DEFAULT_TIMEOUT_S, 1.0, MAX_TIMEOUT_S
        )
        if timeout_s is None:
            return ResultEnvelope.validation_failed(
                f"timeout_s 须为 1–{int(MAX_TIMEOUT_S)} 的数值"
            )
        max_chars = _int_param(
            params, "max_output_chars", DEFAULT_MAX_OUTPUT_CHARS, 1_000, MAX_OUTPUT_CHARS
        )
        if max_chars is None:
            return ResultEnvelope.validation_failed(
                f"max_output_chars 须为 1000–{MAX_OUTPUT_CHARS} 的整数"
            )
        try:
            enabled = get_local_exec_enabled(access.store)
        except CommandConfigError as exc:
            return ResultEnvelope.unavailable(
                f"本地命令配置损坏：{exc}", last_updated_at=_now()
            )
        if not enabled:
            return ResultEnvelope.unavailable(
                LOCAL_EXEC_DISCLOSURE, last_updated_at=_now()
            )

        def runner(cmd: str) -> Any:
            return access.runner.run(
                cmd, timeout_s=timeout_s, max_output_chars=max_chars,
                initiator=ctx.skill_id, trace_id=ctx.trace_id,
            )

        try:
            envelope = ctx.sandbox.run_command(command, runner)
        except CommandError as exc:
            return ResultEnvelope.unavailable(
                f"命令无法启动：{exc}", last_updated_at=_now()
            )
        if envelope.status != "ok":
            return envelope
        result = envelope.data
        payload = {
            "command": result.command,
            "cwd": result.cwd,
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "stdout_chars": result.stdout_chars,
            "stderr_chars": result.stderr_chars,
            "truncated": result.truncated,
            "timed_out": result.timed_out,
            "duration_ms": result.duration_ms,
        }
        if result.timed_out:
            return ResultEnvelope.failed(
                f"命令超时（{timeout_s:g} 秒）已终止；输出可能不完整",
                log_ref=f"{COMMAND_LOG_PREFIX}{result.run_id}.json",
            )
        return ResultEnvelope.ok(payload, as_of=_now())

    return execute


RUN_COMMAND = BuiltinPrimitive(
    base="sk_run_command",
    name="执行本地命令",
    description=(
        "在本机一次性执行命令并返回退出码与输出（超时与输出上限受限）。"
        "默认关闭，需在设置中开启本地命令；命令以当前用户身份运行、可访问该用户"
        "可访问的路径，每次执行都会在本地留痕"
    ),
    output_schema=_OUTPUT,
    parameters=(
        ParameterSpec(
            name="command", type="string", default="",
            description="要执行的一条命令（一次性；不支持交互输入）",
        ),
        ParameterSpec(
            name="timeout_s", type="number", default=DEFAULT_TIMEOUT_S,
            min_value=1, max_value=MAX_TIMEOUT_S,
            description=(
                f"超时秒数（1–{int(MAX_TIMEOUT_S)}，缺省 {int(DEFAULT_TIMEOUT_S)}）；"
                "超时终止进程并显式标注"
            ),
        ),
        ParameterSpec(
            name="max_output_chars", type="integer", default=DEFAULT_MAX_OUTPUT_CHARS,
            min_value=1_000, max_value=MAX_OUTPUT_CHARS,
            description=(
                f"stdout / stderr 各自的字符上限（1000–{MAX_OUTPUT_CHARS}，"
                f"缺省 {DEFAULT_MAX_OUTPUT_CHARS}）"
            ),
        ),
    ),
    permissions=("exec_command",),
    offline_level="full",
    requires=("commands",),
    executor=_executor,
)
