"""生产运行入口 ``python -m st_agent`` 的装配用例（[`T-UI-002.1`]）。

入口把 [`app.build_m3_runtime`](../../src/st_agent/app.py) 与
[`ui.server.serve`](../../src/st_agent/ui/server.py) 装到一起——这就是 M1/M2/M3 关卡
在测试里做的事，故用例落 ``tests/integration``（与 ``test_m1_chat.py`` /
``test_m2_deliberation.py`` / ``test_m3_delivery.py`` 同族，随跨层套件恒跑）。

**用真子进程跑入口**：握手行、就绪后端点可达、父进程看门狗、优雅收尾四件事只有真进程
能证（进程内调用绕过的正是这些面）。子进程环境剥掉 ``LLM_*`` 且 ``cwd`` 不在仓库内，
故不读仓库 ``.env``——用例因此不随本机端点配置而变（同 M1 关卡 A4 口径）。
子进程一律带 ``--ambient-interval 0``：常驻驱动循环自带其专属用例（见本文件末），
而它一旦跑起来会真去调度 Skill / 投当日报纸（[`T-INT-004`]）——那属「主动服务」面，
不该混进「启动与收尾」这组用例。
"""

from __future__ import annotations

import inspect
import io
import json
import os
import subprocess
import sys
import threading
import time
import types
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from st_agent.__main__ import (
    AMBIENT_INTERVAL_SECONDS,
    _build_with_passphrase,
    _read_stdin_passphrase,
    ambient_loop,
    build_ambient_runtime,
    build_parser,
    default_root,
    parent_alive,
    run_backend,
)
from st_agent.l0.storage import (
    MODE_ENCRYPTED,
    StorageCorruptionError,
    StorageOpenError,
    StoragePassphraseRequired,
    StorageSecretsLockedError,
    Store,
    read_mode,
)
from st_agent.ui.shell.notify import send_notification

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
    # `--ambient-interval 0`：常驻驱动循环另有用例（本文件末），此处只要启动 / 收尾面
    command = [
        sys.executable, "-m", "st_agent", "--root", str(root),
        "--handshake", "json", "--ambient-interval", "0",
    ]
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


def _make_encrypted_root(root: Path, passphrase: str = "correct horse battery staple") -> None:
    """建一个**加密**根（用户显式开启加密后的形态；[D-073] 起默认关）。"""
    Store.create(root, passphrase)


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
        assert payload["code"] == "passphrase_required"      # 机器可读：壳据此分辨是否需要口令
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


# ───────────────────────── 口令协商（T-UI-002.4.1；就绪握手之前） ─────────────────────────

_LLM_ENV = {
    "LLM_API_KEY": "sk-local-test-key",
    "LLM_BASE_URL": "http://127.0.0.1:1/v1",
    "LLM_MODEL": "local-test-model",
}
"""触发 `.env` 引导装载（装配期要写凭据库）的三键；`base_url` 指向本机**死端口**，不会真的出网。"""

_MAIN_PASSPHRASE = "correct horse battery staple"
_CREDENTIALS_PASSPHRASE = "credential-passphrase-2026"


def _send(proc: subprocess.Popen[str], line: str) -> None:
    assert proc.stdin is not None
    proc.stdin.write(line + "\n")
    proc.stdin.flush()


def test_entry_negotiates_main_passphrase_then_readies(tmp_path: Path) -> None:
    """GWT-1 · 加密根：先要**主密码**（`kind=main_passphrase`），给对后正常就绪。"""
    root = tmp_path / "store"
    _make_encrypted_root(root)
    workdir = tmp_path / "cwd"
    workdir.mkdir()

    proc = _spawn(root, workdir, "--control-stdin", "--ask-passphrase")
    try:
        ask = json.loads(_read_line(proc))
        assert ask["event"] == "passphrase_required"      # 是请求，不是失败行
        assert ask["kind"] == "main_passphrase"
        assert ask["attempt"] == 1
        assert ask["root"] == str(root)
        assert ask["reason"] and "校验锚" not in ask["reason"]   # 中性，不回显失败细节

        _send(proc, _MAIN_PASSPHRASE)
        assert json.loads(_read_line(proc))["event"] == "ready"

        _send(proc, "stop")
        assert proc.wait(timeout=_STOP_TIMEOUT) == 0
    finally:
        if proc.poll() is None:
            proc.kill()


def test_entry_sets_credentials_passphrase_by_tofu(tmp_path: Path) -> None:
    """GWT-2 · 明文根 + LLM 三键：首次**设定**凭据口令（TOFU），此后同根须**同一**口令。"""
    root = tmp_path / "store"
    workdir = tmp_path / "cwd"
    workdir.mkdir()

    def launch() -> subprocess.Popen[str]:
        return _spawn(root, workdir, "--control-stdin", "--ask-passphrase",
                      env_extra=_LLM_ENV)

    # ① 首次：装配期要写凭据 → 索取凭据口令（kind=credentials）并按 TOFU 设定
    proc = launch()
    try:
        ask = json.loads(_read_line(proc))
        assert (ask["event"], ask["kind"]) == ("passphrase_required", "credentials")
        _send(proc, _CREDENTIALS_PASSPHRASE)
        assert json.loads(_read_line(proc))["event"] == "ready"
        _send(proc, "stop")
        assert proc.wait(timeout=_STOP_TIMEOUT) == 0
    finally:
        if proc.poll() is None:
            proc.kill()

    # ② 二次：同一口令通过（凭据分区已建立，校验锚生效）
    proc = launch()
    try:
        assert json.loads(_read_line(proc))["kind"] == "credentials"
        _send(proc, _CREDENTIALS_PASSPHRASE)
        assert json.loads(_read_line(proc))["event"] == "ready"
        _send(proc, "stop")
        assert proc.wait(timeout=_STOP_TIMEOUT) == 0
    finally:
        if proc.poll() is None:
            proc.kill()

    # ③ 三次：**错**口令 ⇒ 再索取（attempt 递增、不回显所给口令），EOF 即取消
    proc = launch()
    try:
        assert json.loads(_read_line(proc))["kind"] == "credentials"
        _send(proc, "wrong-passphrase")
        again = json.loads(_read_line(proc))
        assert again["event"] == "passphrase_required"
        assert again["attempt"] == 2
        assert "wrong-passphrase" not in again["reason"]

        assert proc.stdin is not None
        proc.stdin.close()                                  # EOF ⇒ 取消（不阻塞）
        assert proc.wait(timeout=_STOP_TIMEOUT) == 2
    finally:
        if proc.poll() is None:
            proc.kill()


def test_entry_gives_up_after_bounded_wrong_passphrases(tmp_path: Path) -> None:
    """GWT-3 · 连续给错 3 次 ⇒ 第 4 次**不再请求**，显式失败且**不降级为明文**。"""
    root = tmp_path / "store"
    _make_encrypted_root(root)
    workdir = tmp_path / "cwd"
    workdir.mkdir()

    proc = _spawn(root, workdir, "--control-stdin", "--ask-passphrase")
    try:
        for expected_attempt in (1, 2, 3):
            ask = json.loads(_read_line(proc))
            assert ask["event"] == "passphrase_required"
            assert ask["attempt"] == expected_attempt
            _send(proc, f"nope-{expected_attempt}")

        payload = json.loads(_read_line(proc))
        assert payload["event"] == "error"
        assert payload["code"] == "passphrase_unavailable"
        assert proc.wait(timeout=_STOP_TIMEOUT) == 2
        assert read_mode(root) == MODE_ENCRYPTED            # 存储未被改动、未降级
    finally:
        if proc.poll() is None:
            proc.kill()


def test_entry_does_not_retry_non_passphrase_open_failures(tmp_path: Path) -> None:
    """GWT-4 · keyfile 损坏**不是**「给口令就能过」⇒ 立即失败，不回 `passphrase_required`。"""
    root = tmp_path / "store"
    Store.create(root)
    (root / "keyfile.json").write_text("{ 这不是 JSON", encoding="utf-8")
    workdir = tmp_path / "cwd"
    workdir.mkdir()

    proc = _spawn(root, workdir, "--control-stdin", "--ask-passphrase")
    try:
        payload = json.loads(_read_line(proc))
        assert payload["event"] == "error"
        assert payload["code"] == "storage_error"
        assert proc.wait(timeout=_STOP_TIMEOUT) == 2         # 不重试、不等口令
    finally:
        if proc.poll() is None:
            proc.kill()


def test_entry_without_negotiation_fails_fast(tmp_path: Path) -> None:
    """GWT-5 · 未开协商 ⇒ 立即显式失败（给出两种入口的指引），**不阻塞在 stdin**。"""
    root = tmp_path / "store"
    _make_encrypted_root(root)
    workdir = tmp_path / "cwd"
    workdir.mkdir()

    proc = _spawn(root, workdir)                             # 既无 --ask-passphrase 也无 env
    try:
        payload = json.loads(_read_line(proc))
        assert payload["event"] == "error"
        assert payload["code"] == "passphrase_required"
        assert "--ask-passphrase" in payload["reason"] and "--passphrase-env" in payload["reason"]
        assert proc.wait(timeout=_STOP_TIMEOUT) == 2
    finally:
        if proc.poll() is None:
            proc.kill()


def test_entry_keeps_passphrase_out_of_argv_logs_and_disk(tmp_path: Path) -> None:
    """GWT-6 · 口令只走进程管道：不进 argv、不进日志、不落存储根下的任何文件。"""
    secret = "s3cret-passphrase-never-on-disk"
    root = tmp_path / "store"
    _make_encrypted_root(root, secret)
    workdir = tmp_path / "cwd"
    workdir.mkdir()

    proc = _spawn(root, workdir, "--control-stdin", "--ask-passphrase")
    try:
        assert json.loads(_read_line(proc))["event"] == "passphrase_required"
        assert secret not in " ".join(proc.args)             # 不进 argv
        _send(proc, secret)
        assert json.loads(_read_line(proc))["event"] == "ready"
        _send(proc, "stop")
        assert proc.wait(timeout=_STOP_TIMEOUT) == 0

        stream = proc.stderr.read()                          # type: ignore[union-attr]
        assert secret not in stream                          # 不进日志
    finally:
        if proc.poll() is None:
            proc.kill()

    needle = secret.encode("utf-8")
    leaked = [p for p in root.rglob("*") if p.is_file() and needle in p.read_bytes()]
    assert leaked == []                                      # 不落盘（含 keyfile / 清单 / 分区）


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


def test_read_stdin_passphrase_only_strips_line_endings(monkeypatch: pytest.MonkeyPatch) -> None:
    """口令内容**原样**（首尾空白是内容的一部分）；空行 / EOF ⇒ `None`（按取消）。"""
    monkeypatch.setattr(sys, "stdin", io.StringIO("  spaced pass  \n"))
    assert _read_stdin_passphrase() == "  spaced pass  "
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n"))
    assert _read_stdin_passphrase() is None
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert _read_stdin_passphrase() is None


def _runtime_stub() -> Any:
    """只带 `chat` 的运行时替身（`run_backend` 只从运行时取这一个属性）。"""
    return types.SimpleNamespace(chat=object())


def _negotiate(
    fake_build: Any,
    answers: list[str | None],
    *,
    ask: bool = True,
    root: Path = Path("ignored"),
    passphrase: str | None = None,
):
    """跑一次协商（`stream` 收 stdout、`read_passphrase` 按序取 `answers`）。"""
    stream = io.StringIO()
    box = iter(answers)

    def read_passphrase() -> str | None:
        return next(box)

    runtime, failure = _build_with_passphrase(
        root=root, passphrase=passphrase, ask_passphrase=ask,
        stream=stream, json_handshake=True, build_runtime=fake_build, feed=None,
        llm_env=None, dotenv_path=None, read_passphrase=read_passphrase,
    )
    return runtime, failure, stream.getvalue()


def test_negotiation_asks_then_succeeds() -> None:
    """GWT-1 · 首次装配判「要口令」⇒ 发 `passphrase_required`（首行），给对后装配成功。"""
    calls: list[str | None] = []

    def fake_build(root: Any, passphrase: Any, **kwargs: Any) -> Any:
        calls.append(passphrase)
        if passphrase != "right":
            raise StoragePassphraseRequired("主密码错误", kind="main_passphrase")
        return _runtime_stub()

    runtime, failure, out = _negotiate(fake_build, ["right"])
    assert failure is None and runtime is not None
    assert calls == [None, "right"]                     # 无口令试一次 → 带口令成功
    first = json.loads(out.strip().splitlines()[0])
    assert first["event"] == "passphrase_required"
    assert first["kind"] == "main_passphrase" and first["attempt"] == 1


def test_negotiation_is_bounded_and_cancellable() -> None:
    """GWT-3 · 有界：请求 3 次后放弃；且用户不给（`None`）即取消、不再请求。"""

    def always_wrong(root: Any, passphrase: Any, **kwargs: Any) -> Any:
        raise StorageSecretsLockedError("凭据分区未解锁")

    _, failure, out = _negotiate(always_wrong, ["a", "b", "c", "d"])
    assert failure is not None and failure.code == "passphrase_unavailable"
    asks = [json.loads(line) for line in out.strip().splitlines()]
    assert [a["attempt"] for a in asks] == [1, 2, 3]     # 第 4 次不再请求
    assert all(a["kind"] == "credentials" for a in asks)

    _, cancelled, out = _negotiate(always_wrong, [None])
    assert cancelled is not None and cancelled.code == "passphrase_required"
    assert "取消" in cancelled.reason
    assert len(out.strip().splitlines()) == 1            # 只请求了一次


def test_negotiation_does_not_retry_non_passphrase_failures() -> None:
    """GWT-4 · 非口令类打开失败**不**触发索取、**不**重试（不白白拖后报错）。"""
    asked = {"n": 0}

    def broken(root: Any, passphrase: Any, **kwargs: Any) -> Any:
        raise StorageOpenError("keyfile 损坏，无法打开存储")

    stream = io.StringIO()

    def read_passphrase() -> str | None:                 # pragma: no cover - 不应被调用
        asked["n"] += 1
        return "unused"

    _, failure = _build_with_passphrase(
        root=Path("ignored"), passphrase=None, ask_passphrase=True,
        stream=stream, json_handshake=True, build_runtime=broken, feed=None,
        llm_env=None, dotenv_path=None, read_passphrase=read_passphrase,
    )
    assert failure is not None and failure.code == "storage_error"
    assert asked["n"] == 0 and stream.getvalue() == ""

    def corrupt(root: Any, passphrase: Any, **kwargs: Any) -> Any:
        raise StorageCorruptionError(types.SimpleNamespace(corrupted=[]))

    _, failure = _build_with_passphrase(
        root=Path("ignored"), passphrase=None, ask_passphrase=True,
        stream=io.StringIO(), json_handshake=True, build_runtime=corrupt, feed=None,
        llm_env=None, dotenv_path=None, read_passphrase=lambda: "unused",
    )
    assert failure is not None and failure.code == "storage_corruption"


def test_negotiation_probes_established_credentials_before_assembly(tmp_path: Path) -> None:
    """开局探测：明文根**凭据已建立** ⇒ **先**索取，装配一次就过（不靠「失败一次再试」）。

    这正是装配幂等（端点已存在即整组跳过）留下的缺口：不探测则装配能过、口令却始终没到位，
    失败被推到**第一次读凭据**时（如首次 LLM 调用）。
    """
    root = tmp_path / "store"
    Store.create(root)                      # 明文根
    Store.open(root, "established-pass")    # 首次解锁即 TOFU 出凭据校验锚

    seen: list[str | None] = []

    def fake_build(any_root: Any, passphrase: Any, **kwargs: Any) -> Any:
        seen.append(passphrase)
        if passphrase != "established-pass":
            raise StorageSecretsLockedError("凭据分区未解锁")
        return _runtime_stub()

    runtime, failure, out = _negotiate(fake_build, ["established-pass"], root=root)
    assert failure is None and runtime is not None
    assert seen == ["established-pass"]                 # 一次装配成功，没有先失败一次
    first = json.loads(out.strip().splitlines()[0])
    assert (first["event"], first["kind"], first["attempt"]) == (
        "passphrase_required", "credentials", 1
    )


def test_negotiation_skips_roots_that_need_no_passphrase(tmp_path: Path) -> None:
    """免口令的明文根（凭据分区**从未建立**）⇒ 不探测出需求、**不**索取、不凭空造口令。"""
    root = tmp_path / "store"
    Store.create(root)                      # 明文根、无凭据
    seen: list[str | None] = []

    def fake_build(any_root: Any, passphrase: Any, **kwargs: Any) -> Any:
        seen.append(passphrase)
        return _runtime_stub()

    runtime, failure, out = _negotiate(fake_build, [], root=root)
    assert failure is None and runtime is not None
    assert seen == [None] and out == ""                 # 没发过任何事件


def test_negotiation_disabled_fails_with_guidance() -> None:
    """GWT-5 · 未开协商 ⇒ 一个 `passphrase_required` 失败（带两种入口指引），不曾索取。"""
    stream = io.StringIO()

    def needs_passphrase(root: Any, passphrase: Any, **kwargs: Any) -> Any:
        raise StoragePassphraseRequired("该存储为加密格式", kind="main_passphrase")

    _, failure = _build_with_passphrase(
        root=Path("ignored"), passphrase=None, ask_passphrase=False,
        stream=stream, json_handshake=True, build_runtime=needs_passphrase, feed=None,
        llm_env=None, dotenv_path=None, read_passphrase=lambda: "unused",
    )
    assert failure is not None
    assert failure.code == "passphrase_required"
    assert "--ask-passphrase" in failure.reason
    assert stream.getvalue() == ""                       # 没有发事件


# ── 常驻驱动与 M3 组合根的接线（[`T-INT-004`] GWT-11）────────────────────────
# 入口装的是**生产 M3 组合根**、并注入表现层的原生通知端口；「多久 tick 一次」由
# 常驻循环决定，判定全在 `tick` 内（[07 §5](../../docs/技术架构-v2/07-L5-主动触达.md)）。
# 真子进程一律 `--ambient-interval 0`（见 `_spawn`），故这组用例专测接线本身。


class _TickingRuntime:
    """只记 `tick` 时刻的运行时替身。"""

    def __init__(self, *, boom: bool = False) -> None:
        self.ticks: list[datetime] = []
        self.boom = boom

    def tick(self, now: datetime) -> None:
        self.ticks.append(now)
        if self.boom:
            raise RuntimeError("替身：某一次循环里的缺陷")


def test_entry_defaults_to_the_production_M3_root() -> None:
    """`run_backend` 的缺省装配口是生产 M3 组合根（不是照旧 M2）。"""
    default = inspect.signature(run_backend).parameters["build_runtime"].default
    assert default is build_ambient_runtime


def test_ambient_runtime_injects_the_native_notify_port(monkeypatch) -> None:
    """原生通知端口由入口注入（表现层实现）——`app` 自己不 import 表现层。"""
    import st_agent.__main__ as entry

    seen: dict[str, Any] = {}

    def fake_build(root: Any, passphrase: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        seen["root"] = root
        return _runtime_stub()

    monkeypatch.setattr(entry, "build_m3_runtime", fake_build)
    runtime = build_ambient_runtime("root", "pass", llm_env={})

    assert runtime is not None
    assert seen["notify"] is send_notification
    assert seen["root"] == "root" and seen["llm_env"] == {}


def test_ambient_loop_ticks_until_stopped() -> None:
    """常驻循环反复调 `tick(now)`；`stop` 置位即退（循环只决定「何时」）。"""
    runtime = _TickingRuntime()
    stop = threading.Event()
    moment = datetime(2026, 9, 28, 8, 30, tzinfo=timezone.utc)
    thread = ambient_loop(runtime, stop, interval=0.01, clock=lambda: moment)
    try:
        assert thread is not None
        deadline = time.monotonic() + 5
        while len(runtime.ticks) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(runtime.ticks) >= 3, "循环应持续推进"
        assert set(runtime.ticks) == {moment}, "时刻取自注入的时钟（判定可离线复算）"
    finally:
        stop.set()
        thread.join(timeout=5)
    assert not thread.is_alive()


def test_ambient_loop_survives_a_broken_tick() -> None:
    """一次 `tick` 抛错不让常驻服务停摆，也不被吞（追溯进 stderr）。"""
    runtime = _TickingRuntime(boom=True)
    stop = threading.Event()
    thread = ambient_loop(runtime, stop, interval=0.01)
    try:
        deadline = time.monotonic() + 5
        while len(runtime.ticks) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(runtime.ticks) >= 2, "抛错后循环继续"
    finally:
        stop.set()
        thread.join(timeout=5)


def test_ambient_loop_is_absent_without_a_tick_face() -> None:
    """M1 / M2 组合根没有 `tick` 面 ⇒ 不起循环（**不假装有主动服务**）。"""
    stop = threading.Event()
    assert ambient_loop(_runtime_stub(), stop, interval=0.01) is None
    assert ambient_loop(_TickingRuntime(), stop, interval=0) is None


def test_ambient_interval_flag_is_wired() -> None:
    """CLI 面给出间隔；缺省即模块常量（`0` 关闭）。"""
    assert build_parser().parse_args([]).ambient_interval == AMBIENT_INTERVAL_SECONDS
    assert build_parser().parse_args(["--ambient-interval", "5"]).ambient_interval == 5.0


def test_production_entry_runs_the_ambient_duty_cycle(tmp_path, monkeypatch) -> None:
    """真装配 + 真常驻循环：生产入口装配 M3、tick 一轮、优雅收尾。

    只把**原生通知端口**换成不弹窗的替身（`send_notification` 会真弹系统通知）——
    其余全真：真 M3 组合根、真调度、真日报生成与投递、真本机回环服务。
    """
    import st_agent.__main__ as entry

    monkeypatch.setattr(entry, "send_notification", lambda *a, **k: None)
    monkeypatch.setattr(sys, "stdin", io.StringIO("stop" + chr(10)))
    root, workdir = tmp_path / "store", tmp_path / "cwd"
    workdir.mkdir()
    stream = io.StringIO()

    code = run_backend(
        root=root, host="127.0.0.1", port=0, json_handshake=True,
        passphrase="m3-entry-throwaway",          # 新根的凭据分区恒加密（02 §2.2）
        control_stdin=True, out=stream, ambient_interval=0.05,
    )
    assert code == 0
    lines = [json.loads(line) for line in stream.getvalue().strip().splitlines()]
    assert lines[0]["event"] == "ready"
    assert lines[-1]["event"] == "stopped"

    # 常驻循环真的跑过一轮：当日报纸已生成并投出（到点判定 + 渠道投递 + 留痕回写）
    report = Store.open(root, "m3-entry-throwaway").get(
        "execution_log",
        f"daily_report/{datetime.now().astimezone().date().isoformat()}.json",
    )
    assert report, "常驻循环应生成当日报纸"
    assert json.loads(report.decode("utf-8"))["delivered_channel"] == "desktop"

