"""生产运行入口 ``python -m st_agent`` 的装配用例（[`T-UI-002.1`]）。

入口把 [`app.build_m2_runtime`](../../src/st_agent/app.py) 与
[`ui.server.serve`](../../src/st_agent/ui/server.py) 装到一起——这就是 M1/M2 关卡
在测试里做的事，故用例落 ``tests/integration``（与 ``test_m1_chat.py`` /
``test_m2_deliberation.py`` 同族，随跨层套件恒跑）。

**用真子进程跑入口**：握手行、就绪后端点可达、父进程看门狗、优雅收尾四件事只有真进程
能证（进程内调用绕过的正是这些面）。子进程环境剥掉 ``LLM_*`` 且 ``cwd`` 不在仓库内，
故不读仓库 ``.env``——用例因此不随本机端点配置而变（同 M1 关卡 A4 口径）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from st_agent.__main__ import default_root, parent_alive
from st_agent.l0.storage import MODE_ENCRYPTED, Store, read_mode

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
_READY_TIMEOUT = 90.0
_STOP_TIMEOUT = 30.0

_PASSPHRASE_ENV = "ST_AGENT_TEST_PASSPHRASE"


def _child_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("LLM_")}
    env["PYTHONPATH"] = str(SRC)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


class Backend:
    """一个真入口子进程 + 它的就绪握手。"""

    def __init__(self, proc: subprocess.Popen[str], ready: dict[str, Any]) -> None:
        self.proc = proc
        self.ready = ready

    @property
    def base_url(self) -> str:
        return f"http://{self.ready['host']}:{self.ready['port']}"

    def request(self, path: str, *, data: dict[str, Any] | None = None) -> dict[str, Any]:
        body = None if data is None else json.dumps(data).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            method="GET" if body is None else "POST",
            headers={"X-ST-Token": self.ready["token"]},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def stop_gracefully(self, timeout: float = _STOP_TIMEOUT) -> int:
        """走壳的控制通道（stdin 管道）优雅停——不依赖控制台（Windows GUI 壳无控制台）。"""
        assert self.proc.stdin is not None
        self.proc.stdin.write("stop\n")
        self.proc.stdin.flush()
        return self.proc.wait(timeout=timeout)


def _spawn(
    root: Path, workdir: Path, *extra: str, env_extra: dict[str, str] | None = None
) -> subprocess.Popen[str]:
    command = [sys.executable, "-m", "st_agent", "--root", str(root), "--handshake", "json"]
    command += list(extra)
    env = _child_env()
    env.update(env_extra or {})
    return subprocess.Popen(
        command,
        cwd=str(workdir),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )


def _read_line(proc: subprocess.Popen[str]) -> str:
    line = proc.stdout.readline()          # type: ignore[union-attr]
    if not line:
        raise AssertionError(
            "入口未输出任何行即结束："
            f"rc={proc.poll()} stderr={proc.stderr.read()[:800]}"   # type: ignore[union-attr]
        )
    return line


@pytest.fixture
def backend(tmp_path: Path) -> Iterator[Backend]:
    """起一个入口子进程（壳口径：``--control-stdin`` + 管道）并读就绪行；用例结束必回收。"""
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    proc = _spawn(tmp_path / "store", workdir, "--control-stdin")
    try:
        ready = json.loads(_read_line(proc))
        assert ready.get("event") == "ready", ready
        yield Backend(proc, ready)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=_STOP_TIMEOUT)


# ───────────────────────── GWT-1：入口把真组合根接上回环面 ─────────────────────────


def test_entry_emits_handshake_with_all_fields(backend: Backend) -> None:
    """GWT-1 · 握手是**首行**、字段齐备、且指向真实回环地址。"""
    assert set(backend.ready) == {"event", "host", "port", "token", "pid", "root", "version"}
    assert backend.ready["host"] == "127.0.0.1"
    assert isinstance(backend.ready["port"], int) and backend.ready["port"] > 0
    assert backend.ready["pid"] == backend.proc.pid
    assert Path(backend.ready["root"]).is_dir()
    assert backend.ready["token"]


def test_entry_serves_health_and_wired_chat(backend: Backend) -> None:
    """GWT-1 · ``/api/health`` 恒 ok；``/api/chat`` **走真门面**（不再是「未接入」）。"""
    health = backend.request("/api/health")
    assert health["status"] == "ok"
    assert health["data"]["service"] == "st-agent-ui"

    chat = backend.request("/api/chat", data={"action": "post", "text": "你好"})
    assert chat["status"] in {
        "ok", "empty", "unavailable", "dependency_failed", "validation_failed", "failed",
    }
    # 门面已注入：失败原因来自真链路（如端点不可用），而**不是**「未接入对话门面」
    assert "未接入对话门面" not in (chat.get("reason") or "")
    assert "render" in chat


def test_entry_rejects_wrong_token_and_host(backend: Backend) -> None:
    """GWT-5 · 守卫生效：无令牌 / 错 Origin 一律拒，且**不复用旧端口口径**。"""
    with pytest.raises(urllib.error.HTTPError) as no_token:
        urllib.request.urlopen(f"{backend.base_url}/api/health", timeout=10)
    assert no_token.value.code == 401

    request = urllib.request.Request(
        f"{backend.base_url}/api/health",
        headers={"X-ST-Token": backend.ready["token"], "Origin": "http://evil.example"},
    )
    with pytest.raises(urllib.error.HTTPError) as bad_origin:
        urllib.request.urlopen(request, timeout=10)
    assert bad_origin.value.code == 403


def test_entry_stops_gracefully_on_control_channel(backend: Backend) -> None:
    """GWT-3 · 壳经 stdin 投递停止指令 ⇒ 优雅收尾（退出码 0 + 末行 `stopped`）。"""
    assert backend.stop_gracefully() == 0
    tail = backend.proc.stdout.read()      # type: ignore[union-attr]
    assert json.loads(tail.strip().splitlines()[-1]) == {"event": "stopped"}


def test_entry_stops_on_stdin_eof(tmp_path: Path) -> None:
    """GWT-3 · 管道被关（壳被强杀）⇒ 同样优雅收尾，不留孤儿。"""
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    proc = _spawn(tmp_path / "store", workdir, "--control-stdin")
    try:
        assert json.loads(_read_line(proc))["event"] == "ready"
        assert proc.stdin is not None
        proc.stdin.close()
        assert proc.wait(timeout=_STOP_TIMEOUT) == 0
    finally:
        if proc.poll() is None:
            proc.kill()


# ───────────────────────── GWT-2：父进程看门狗 ─────────────────────────


def test_entry_exits_when_parent_gone(tmp_path: Path) -> None:
    """GWT-2 · 父（壳）进程消失 ⇒ 后端自行优雅退出（Windows 无进程组连带语义）。"""
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    parent = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    proc = _spawn(tmp_path / "store", workdir, "--parent-pid", str(parent.pid))
    try:
        ready = json.loads(_read_line(proc))
        assert ready["event"] == "ready"
        assert parent_alive(parent.pid)

        parent.terminate()
        parent.wait(timeout=_STOP_TIMEOUT)
        assert not parent_alive(parent.pid)

        assert proc.wait(timeout=15) == 0
        tail = proc.stdout.read()          # type: ignore[union-attr]
        assert json.loads(tail.strip().splitlines()[-1]) == {"event": "stopped"}
    finally:
        for handle in (proc, parent):
            if handle.poll() is None:
                handle.kill()
                handle.wait(timeout=_STOP_TIMEOUT)


# ───────────────────────── GWT-4：加密根 fail-closed ─────────────────────────


def _make_encrypted_root(root: Path) -> None:
    """建一个**加密**根（用户显式开启加密后的形态；[D-073] 起默认关）。"""
    Store.create(root, "correct horse battery staple")


def test_entry_fails_closed_on_encrypted_root(tmp_path: Path) -> None:
    """GWT-4 · 加密根无口令 ⇒ 显式失败（非零退出 + `event=error`），不降级为明文。"""
    root = tmp_path / "store"
    _make_encrypted_root(root)
    workdir = tmp_path / "cwd"
    workdir.mkdir()

    proc = _spawn(root, workdir)
    try:
        line = _read_line(proc)
        payload = json.loads(line)
        assert payload["event"] == "error"
        assert "主密码" in payload["reason"]
        assert proc.wait(timeout=_STOP_TIMEOUT) == 2
        assert read_mode(root) == MODE_ENCRYPTED
    finally:
        if proc.poll() is None:
            proc.kill()


def test_entry_opens_encrypted_root_with_passphrase_env(tmp_path: Path) -> None:
    """GWT-4 · 口令只经环境变量传入即可打开（不落 argv）。"""
    root = tmp_path / "store"
    _make_encrypted_root(root)
    workdir = tmp_path / "cwd"
    workdir.mkdir()

    proc = _spawn(
        root, workdir, "--control-stdin", "--passphrase-env", _PASSPHRASE_ENV,
        env_extra={_PASSPHRASE_ENV: "correct horse battery staple"},
    )
    try:
        assert json.loads(_read_line(proc))["event"] == "ready"
        assert proc.stdin is not None
        proc.stdin.write("stop\n")
        proc.stdin.flush()
        assert proc.wait(timeout=_STOP_TIMEOUT) == 0
    finally:
        if proc.poll() is None:
            proc.kill()


# ───────────────────────── 纯函数面 ─────────────────────────


def _slashes(path: object) -> str:
    """把路径统一成 ``/`` 分隔再比对。

    `default_root` 在**当前主机**上拼平台路径：Linux 宿主上跑 `win32` 分支时，
    `Path(r"C:\\Users\\u\\...")` 里的反斜杠只是普通字符，`Path` 的 `==` 因此按
    ``\\`` 与 ``/`` 两种分隔符比较就会不等——那是**宿主的**事，与被测语义无关。
    """
    return str(path).replace("\\", "/")


@pytest.mark.parametrize(
    "system,env_extra,expected",
    [
        ("win32", {"LOCALAPPDATA": r"C:\Users\u\AppData\Local"},
         r"C:\Users\u\AppData\Local\STAgent"),
        ("darwin", {}, "/Users/u/Library/Application Support/STAgent"),
        ("linux", {}, "/home/u/.local/share/st-agent"),
        ("linux", {"XDG_DATA_HOME": "/data"}, "/data/st-agent"),
    ],
)
def test_default_root_platform_conventions(
    system: str, env_extra: dict[str, str], expected: str
) -> None:
    """GWT-1 · 默认根按平台惯例；`ST_AGENT_ROOT` 覆盖优先于一切。"""
    env = {"HOME": "/Users/u" if system == "darwin" else "/home/u"}
    env.update(env_extra)
    assert _slashes(default_root(env, system)) == _slashes(expected)
    env["ST_AGENT_ROOT"] = str(Path("/elsewhere") / "root")
    assert _slashes(default_root(env, system)) == "/elsewhere/root"


def test_default_root_uses_cwd_independent_env(tmp_path: Path) -> None:
    """默认根不依赖 `cwd`（壳在任意工作目录拉起后端都得落同一处）。"""
    assert _slashes(default_root({"HOME": "/home/u", "XDG_DATA_HOME": ""}, "linux")) == (
        "/home/u/.local/share/st-agent"
    )


def test_parent_alive_tracks_real_processes() -> None:
    """GWT-2 · 探针只探测不发送信号：本进程存活、已退出进程为假。"""
    assert parent_alive(os.getpid())
    assert not parent_alive(0)
    victim = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        assert parent_alive(victim.pid)
    finally:
        victim.terminate()
        victim.wait(timeout=_STOP_TIMEOUT)
    deadline = time.monotonic() + 10
    while parent_alive(victim.pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not parent_alive(victim.pid)
