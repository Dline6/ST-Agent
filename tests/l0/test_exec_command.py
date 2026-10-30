"""一次性命令执行面（T-AGT-016.1）。"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest

from st_agent.l0.exec.command import CommandRunner, _Output
from st_agent.l0.exec.errors import CommandError

PASS = "correct horse battery staple"


def _python(code: str) -> str:
    argv = [sys.executable, "-c", code]
    if os.name == "nt":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def test_default_spawn_returns_output_and_exit_code() -> None:
    result = CommandRunner().run(_python("import sys; print('hi'); sys.exit(3)"))
    assert result.exit_code == 3
    assert result.stdout.strip() == "hi"
    assert result.timed_out is False
    assert result.truncated is False


def test_output_is_truncated_at_the_cap() -> None:
    result = CommandRunner().run(
        _python("print('x' * 5000)"), max_output_chars=1_000
    )
    assert result.stdout_chars == 1_000
    assert len(result.stdout) == 1_000
    assert result.truncated is True


def test_timeout_terminates_and_marks_the_result() -> None:
    started = time.monotonic()
    result = CommandRunner().run(
        _python("import time; time.sleep(30)"), timeout_s=1
    )
    assert result.timed_out is True
    assert time.monotonic() - started < 20


def test_empty_command_is_rejected() -> None:
    with pytest.raises(CommandError):
        CommandRunner().run("   ")


class FakeSpawn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, float, int]] = []

    def __call__(self, command: str, cwd: str, timeout_s: float, max_chars: int) -> _Output:
        self.calls.append((command, cwd, timeout_s, max_chars))
        return _Output(
            exit_code=0, stdout="out", stderr="err",
            stdout_chars=3, stderr_chars=3, truncated=False,
            timed_out=False, duration_ms=5,
        )


@pytest.fixture()
def store(tmp_path: Path):
    from st_agent.l0.storage import Store

    return Store.create(tmp_path / "root", PASS)


def test_runner_records_every_execution_and_cleans_the_workdir(store) -> None:
    fake = FakeSpawn()
    runner = CommandRunner(store, spawn=fake)
    command = "echo " + "a" * 2_500
    result = runner.run(command, initiator="sk_run_command_v1.0", trace_id="t1")
    assert fake.calls and fake.calls[0][0] == command
    assert not Path(result.cwd).exists(), "一次性工作目录应在执行后清理"
    names = [n for n in store.list_files("execution_log") if n.startswith("exec-command/")]
    assert len(names) == 1
    record = json.loads(store.get("execution_log", names[0]).decode("utf-8"))
    assert record["command"] == command[:2_000]
    assert record["command_truncated"] is True
    assert record["initiator"] == "sk_run_command_v1.0"
    assert record["trace_id"] == "t1"
    assert record["exit_code"] == 0
    assert record["stdout_chars"] == 3
    assert record["stderr_chars"] == 3
