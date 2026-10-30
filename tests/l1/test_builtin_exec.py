"""本地命令原语 ``sk_run_command``（T-AGT-016.1）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from st_agent.l0.exec.command import CommandRunner, _Output
from st_agent.l0.exec.exec_config import set_local_exec_enabled
from st_agent.l0.storage import Store
from st_agent.l1.builtin import CommandAccess, install_builtins
from st_agent.l1.builtin.exec import RUN_COMMAND
from st_agent.l1.runner import SkillRunner
from st_agent.l1.sandbox import SkillSandbox
from st_agent.l1.skills import SkillRegistry

PASS = "correct horse battery staple"
RUN_ID = "sk_run_command_v1.0"


class FakeSpawn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, float, int]] = []
        self.output = _Output(
            exit_code=0, stdout="hello\n", stderr="",
            stdout_chars=6, stderr_chars=0, truncated=False,
            timed_out=False, duration_ms=3,
        )

    def __call__(self, command, cwd, timeout_s, max_chars):
        self.calls.append((command, cwd, timeout_s, max_chars))
        return self.output


class Env:
    def __init__(self, store, runner, fake, sandbox=True):
        self.store = store
        self.runner = runner
        self.fake = fake
        self.sandbox = sandbox

    def run(self, command: str, *, approved=("exec_command",), **params):
        return self.runner.run(
            RUN_ID, {"command": command, **params},
            approved_permissions=approved,
        ).envelope


@pytest.fixture()
def env(tmp_path: Path) -> Env:
    store = Store.create(tmp_path / "root", PASS)
    registry = SkillRegistry(store)
    sandbox = SkillSandbox(store)
    runner = SkillRunner(store, registry, sandbox=sandbox)
    fake = FakeSpawn()
    access = CommandAccess(store, CommandRunner(store, spawn=fake))
    install_builtins(
        registry, runner, context={"commands": access}, primitives=(RUN_COMMAND,)
    )
    return Env(store, runner, fake)


def test_disabled_by_default_executes_nothing(env: Env) -> None:
    out = env.run("echo hi")
    assert out.status == "unavailable"
    assert "默认关闭" in out.reason
    assert env.fake.calls == []


def test_enabled_runs_through_the_sandbox(env: Env) -> None:
    set_local_exec_enabled(env.store, True)
    out = env.run("echo hi", timeout_s=5, max_output_chars=2000)
    assert out.status == "ok"
    assert out.data["stdout"] == "hello\n"
    assert out.data["exit_code"] == 0
    assert env.fake.calls[0][0] == "echo hi"
    assert env.fake.calls[0][2] == 5
    assert env.fake.calls[0][3] == 2000


def test_without_approval_the_command_is_not_run(env: Env) -> None:
    set_local_exec_enabled(env.store, True)
    out = env.run("echo hi", approved=())
    assert out.status == "validation_failed"
    assert "批准" in out.reason
    assert env.fake.calls == []


def test_truncation_is_surfaced(env: Env) -> None:
    set_local_exec_enabled(env.store, True)
    env.fake.output = _Output(
        exit_code=0, stdout="x" * 1000, stderr="",
        stdout_chars=1000, stderr_chars=0, truncated=True,
        timed_out=False, duration_ms=1,
    )
    out = env.run("echo hi", max_output_chars=1000)
    assert out.status == "ok"
    assert out.data["truncated"] is True
    assert len(out.data["stdout"]) == 1000


def test_timeout_is_marked_as_failed(env: Env) -> None:
    set_local_exec_enabled(env.store, True)
    env.fake.output = _Output(
        exit_code=-1, stdout="partial", stderr="",
        stdout_chars=7, stderr_chars=0, truncated=False,
        timed_out=True, duration_ms=1000,
    )
    out = env.run("sleep 30", timeout_s=1)
    assert out.status == "failed"
    assert "超时" in out.reason and "已终止" in out.reason
    assert out.log_ref.startswith("exec-command/")


def test_each_run_is_recorded(env: Env) -> None:
    set_local_exec_enabled(env.store, True)
    assert env.run("echo hi").status == "ok"
    records = [n for n in env.store.list_files("execution_log")
               if n.startswith("exec-command/")]
    assert len(records) == 1


def test_missing_sandbox_is_explicit(tmp_path: Path) -> None:
    store = Store.create(tmp_path / "root", PASS)
    registry = SkillRegistry(store)
    runner = SkillRunner(store, registry)
    fake = FakeSpawn()
    install_builtins(
        registry, runner,
        context={"commands": CommandAccess(store, CommandRunner(store, spawn=fake))},
        primitives=(RUN_COMMAND,),
    )
    set_local_exec_enabled(store, True)
    out = runner.run(
        RUN_ID, {"command": "echo hi"}, approved_permissions=("exec_command",)
    ).envelope
    assert out.status == "unavailable"
    assert "沙箱" in out.reason
    assert fake.calls == []
