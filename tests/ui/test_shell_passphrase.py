"""壳侧的口令索取 / 记忆 / 记忆出口（[`T-UI-002.4.2`]）。

GUI 边与凭据库在 CI 里**都不装**，故策略层用替身钉死（`PassphraseResolver` 无 GUI，可直接
单测）；壳↔后端的**协商协议**则用真后端子进程 + 真 stdin 管道跑一遍——「请求 → 回传 → 就绪」
与「取消 → 回收」只有真进程能证。
"""

from __future__ import annotations

import os
import subprocess
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from st_agent.l0.storage import Store
from st_agent.ui.shell import passphrase as passphrase_mod
from st_agent.ui.shell import backend as backend_mod
from st_agent.ui.shell.app import Shell
from st_agent.ui.shell.backend import BackendSpec, start_backend
from st_agent.ui.shell.errors import ShellCancelled, ShellSurfaceUnavailable
from st_agent.ui.shell.passphrase import (
    KeyringVault,
    NativePassphrasePrompt,
    PassphraseRequest,
    PassphraseResolver,
    build_vault,
)

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
_STOP_TIMEOUT = 30.0
_PASS = "correct horse battery staple"
_ROOT = "C:/tmp/store"


def _child_env() -> dict[str, str]:
    """子进程环境：只给 PYTHONPATH / 编码（不继承 LLM_*，故不读仓库 `.env`）。"""
    return {"PYTHONPATH": str(SRC), "PYTHONIOENCODING": "utf-8"}


def _spec(root: Path) -> BackendSpec:
    """后端启动口径：**存储根经 `ST_AGENT_ROOT` 指定**。

    壳不传 `--root`（根由后端按平台惯例自定，[`T-UI-002.1`] 假设 `A2`），故用例**只能**
    用这个环境变量把后端指到临时根上——否则会落到本机真实的默认根上（那既污染开发机，
    也让「要不要口令」取决于开发机的现状）。
    """
    return BackendSpec(
        argv=BackendSpec.default().argv, env={**_child_env(), "ST_AGENT_ROOT": str(root)}
    )


# ───────────────────────── 替身 ─────────────────────────


class FakePrompt:
    """口令框替身：按序返回预置答案（用尽 ⇒ `None`＝取消），并记下每次请求。"""

    def __init__(self, *answers: str | None) -> None:
        self._answers = list(answers)
        self.requests: list[PassphraseRequest] = []

    def ask(self, request: PassphraseRequest) -> str | None:
        self.requests.append(request)
        return self._answers.pop(0) if self._answers else None


class FakeVault:
    """凭据库替身：一个 dict + 调用记录（`delete` 返回「确实删掉了」）。"""

    def __init__(self, **entries: str) -> None:
        self.data = dict(entries)
        self.calls: list[tuple[str, str, str]] = []

    def get(self, account: str) -> str | None:
        self.calls.append(("get", account, ""))
        return self.data.get(account)

    def set(self, account: str, value: str) -> None:
        self.calls.append(("set", account, value))
        self.data[account] = value

    def delete(self, account: str) -> bool:
        self.calls.append(("delete", account, ""))
        return self.data.pop(account, None) is not None


class FakeKeyring:
    """`keyring` 替身（含它自己的 `errors.PasswordDeleteError`）。"""

    class errors:                                     # noqa: N801 —— 模仿 keyring 的属性结构
        class PasswordDeleteError(Exception):
            pass

    def __init__(self) -> None:
        self.data: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        return self.data.get((service, account))

    def set_password(self, service: str, account: str, value: str) -> None:
        self.data[(service, account)] = value

    def delete_password(self, service: str, account: str) -> None:
        if (service, account) not in self.data:
            raise self.errors.PasswordDeleteError("no such entry")
        del self.data[(service, account)]


def _shell(**kwargs: Any) -> tuple[Shell, list[tuple[str, str]]]:
    """最小装配的壳 + 通知记录（只测口令相关动作，故窗口 / 托盘 / 登录项用占位件）。"""
    notes: list[tuple[str, str]] = []
    shell = Shell(
        backend=BackendSpec.default(),
        window=object(),
        tray=object(),
        login_item=object(),
        command=("/opt/st-agent",),
        notifier=lambda title, body: notes.append((title, body)),
        **kwargs,
    )
    shell.handle = types.SimpleNamespace(root=_ROOT)   # type: ignore[assignment]
    return shell, notes


def _unavailable(what: str) -> Any:
    def boom(*args: Any, **kwargs: Any) -> Any:      # noqa: ARG001
        raise ShellSurfaceUnavailable(what)

    return boom


# ───────────────────────── GWT-1：真后端协商 ─────────────────────────


@pytest.fixture
def encrypted_root(tmp_path: Path) -> Path:
    root = tmp_path / "store"
    Store.create(root, _PASS)
    return root


def test_shell_supplies_passphrase_through_stdin_pipe(
    encrypted_root: Path, tmp_path: Path
) -> None:
    """GWT-1 · 后端在就绪前要口令 ⇒ 索取端口取值、经 **stdin 管道**回传、随后就绪。"""
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    asked: list[PassphraseRequest] = []

    def resolve(request: PassphraseRequest) -> str | None:
        asked.append(request)
        return _PASS

    handle = start_backend(
        _spec(encrypted_root),
        parent_pid=os.getpid(),
        cwd=workdir,
        resolve_passphrase=resolve,
    )
    try:
        assert handle.alive and handle.port > 0 and handle.token
        assert Path(handle.root).resolve() == encrypted_root.resolve()
        assert [request.kind for request in asked] == ["main_passphrase"]
        assert asked[0].attempt == 1
        assert asked[0].reason                       # 后端给了中性说明
        assert _PASS not in " ".join(handle.process.args)     # 口令**不进 argv**
    finally:
        if handle.alive:
            handle.stop()


def test_shell_cancel_reclaims_backend_without_orphan(
    encrypted_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GWT-4 · 用户取消 ⇒ `ShellCancelled`，且**子进程被收掉**（不留孤儿）。"""
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    spawned: list[subprocess.Popen[str]] = []
    real_popen = subprocess.Popen

    def spy(*args: Any, **kwargs: Any) -> Any:
        process = real_popen(*args, **kwargs)
        spawned.append(process)
        return process

    monkeypatch.setattr(backend_mod.subprocess, "Popen", spy)

    with pytest.raises(ShellCancelled):
        start_backend(
            _spec(encrypted_root),
            parent_pid=os.getpid(),
            cwd=workdir,
            resolve_passphrase=lambda request: None,
        )
    assert len(spawned) == 1
    assert spawned[0].poll() is not None             # 已回收，无孤儿


def test_shell_without_resolver_fails_on_passphrase_request(
    encrypted_root: Path, tmp_path: Path
) -> None:
    """壳没接口令索取面 ⇒ 显式失败（不静默、不空窗），且后端被收掉。"""
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    from st_agent.ui.shell.errors import ShellError

    with pytest.raises(ShellError, match="没有接入口令索取面"):
        start_backend(
            _spec(encrypted_root),
            parent_pid=os.getpid(),
            cwd=workdir,
            resolve_passphrase=None,
        )


# ───────────────────────── GWT-2/3：策略（凭据库优先 → 弹框 → 写回） ─────────────────────────


def test_resolver_uses_vault_on_first_try_without_prompting() -> None:
    """GWT-2 · 凭据库有值 ⇒ **一次框都不弹**，直接回传。"""
    prompt = FakePrompt()                                  # 备了零个答案：一旦弹框就返回 None
    vault = FakeVault(**{_ROOT: "remembered"})
    resolve = PassphraseResolver(prompt=prompt, vault=vault)

    assert resolve(PassphraseRequest(_ROOT, "credentials", 1)) == "remembered"
    assert prompt.requests == []
    assert ("get", _ROOT, "") in vault.calls


def test_resolver_prompts_and_refreshes_stale_vault_value() -> None:
    """GWT-3 · 凭据库的值被后端否掉（`attempt>1`）⇒ **改弹框**，成功后**写回**覆盖。"""
    prompt = FakePrompt("fresh")
    vault = FakeVault(**{_ROOT: "stale"})
    resolve = PassphraseResolver(prompt=prompt, vault=vault)

    assert resolve(PassphraseRequest(_ROOT, "credentials", 1)) == "stale"
    assert resolve(PassphraseRequest(_ROOT, "credentials", 2)) == "fresh"
    assert vault.data[_ROOT] == "fresh"                    # 失效条目就地更新
    assert len(prompt.requests) == 1


def test_resolver_without_vault_prompts_and_cancel_returns_none() -> None:
    """没有凭据库 ⇒ 每次都问；取消（`None`）原样上抛，不当作空口令。"""
    prompt = FakePrompt(None)
    resolve = PassphraseResolver(prompt=prompt, vault=None)

    assert resolve(PassphraseRequest(_ROOT, "main_passphrase", 1)) is None
    assert len(prompt.requests) == 1
    assert prompt.requests[0].label == "主密码"


# ───────────────────────── GWT-5：记忆的出口（托盘） ─────────────────────────


def test_tray_forgets_remembered_passphrase() -> None:
    """GWT-5 · 清除动作删掉本根的条目，并给出回执；此后由弹框接管。"""
    vault = FakeVault(**{_ROOT: "remembered"})
    shell, notes = _shell(vault=vault)

    shell.forget_passphrase()

    assert ("delete", _ROOT, "") in vault.calls
    assert vault.data == {}
    assert notes and "已清除" in notes[0][1]


def test_tray_forget_reports_when_nothing_was_remembered() -> None:
    """没有条目时如实说明（不假装清过了）；未启用凭据库时同样如实说明。"""
    shell, notes = _shell(vault=FakeVault())
    shell.forget_passphrase()
    assert notes and "没有记住过" in notes[0][1]

    shell, notes = _shell(vault=None)
    shell.forget_passphrase()
    assert notes and "未启用系统凭据库" in notes[0][1]


def test_tray_forget_reports_backend_failure() -> None:
    """凭据库故障**上抛到回执**（清除是用户的明确动作，不能静默失败）。"""
    class Broken(FakeVault):
        def delete(self, account: str) -> bool:
            raise OSError("credential store locked")

    shell, notes = _shell(vault=Broken())
    shell.forget_passphrase()
    assert notes and "清除已记住的口令失败" in notes[0][1]


# ───────────────────────── GWT-6：凭据库缺失 ⇒ 显式降级；无 Tk ⇒ 显式抛 ─────────────────────────


def test_build_vault_is_explicit_either_way(monkeypatch: pytest.MonkeyPatch) -> None:
    """GWT-6 · 凭据库不可用 ⇒ 返回 `None` **并给出一句显式告知**（不静默降级）。"""
    monkeypatch.setattr(passphrase_mod, "load_keyring",
                        _unavailable("未安装口令记忆依赖 keyring：pip install -e \".[keyring]\""))
    vault, notice = build_vault()
    assert vault is None
    assert notice is not None and "凭据库不可用" in notice
    assert "keyring" in notice                            # 告知里点名缺什么


def test_build_vault_on_this_host_is_never_two_blanks() -> None:
    """本机装没装 `keyring` 都对：**要么**有端口、**要么**有告知，不留「两空」。"""
    vault, notice = build_vault()
    assert (vault is None) != (notice is None)


def test_keyring_unavailable_is_raised_by_the_vault_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(passphrase_mod, "load_keyring", _unavailable("未安装口令记忆依赖 keyring"))
    with pytest.raises(ShellSurfaceUnavailable):
        KeyringVault()


def test_native_prompt_fails_explicitly_without_tk(monkeypatch: pytest.MonkeyPatch) -> None:
    """GWT-6 · 无 Tk ⇒ **显式**抛（点名怎么绕：`--passphrase-env`），不静默什么都不问。"""
    monkeypatch.setattr(
        passphrase_mod, "_load_tk",
        _unavailable("本机没有 Tk（python3-tk / 冻结产物未打 Tk），弹不出原生口令框"),
    )
    with pytest.raises(ShellSurfaceUnavailable, match="Tk"):
        NativePassphrasePrompt().ask(PassphraseRequest(_ROOT, "credentials", 1))


def test_keyring_vault_keys_by_normalised_root() -> None:
    """账号键＝**规范化后的根**：`~` 与绝对路径落同一条；跨根不串用。"""
    keyring = FakeKeyring()
    vault = KeyringVault(keyring=keyring)

    vault.set("~/st-agent", "secret-a")
    assert vault.get(str(Path("~/st-agent").expanduser())) == "secret-a"
    vault.set("/other", "secret-b")
    assert vault.get("/other") == "secret-b" and vault.get("~/st-agent") == "secret-a"
    assert vault.delete("~/st-agent") is True
    assert vault.get("~/st-agent") is None
    assert vault.delete("~/st-agent") is False           # 本来就没有 ⇒ False


def test_keyring_vault_degrades_on_backend_failure() -> None:
    """凭据库读写故障**不拦启动**：读按「没有」处理（并留一条日志）。"""
    class Broken(FakeKeyring):
        def get_password(self, service: str, account: str) -> str | None:
            raise OSError("no secret service on this host")

    class BrokenWriter(FakeKeyring):
        def set_password(self, service: str, account: str, value: str) -> None:
            raise OSError("read-only keyring")

    assert KeyringVault(keyring=Broken()).get(_ROOT) is None
    KeyringVault(keyring=BrokenWriter()).set(_ROOT, "x")   # 不抛：本次运行内仍可用


# ───────────────────────── run_shell：端到端装配（真后端 + 注入端口） ─────────────────────────


class MiniWindow:
    """最小窗口替身（`run()` 立即返回，故 `run_shell` 会走完收尾序列）。"""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def create(self, url: str, *, on_close_requested: Any) -> None:
        self.urls.append(url)

    def show(self) -> None: ...
    def hide(self) -> None: ...
    def destroy(self) -> None: ...
    def run(self) -> None: ...


class MiniTray:
    def __init__(self) -> None:
        self.items: list[Any] = []
        self.stopped = False

    def install(self, items: Any) -> None:
        self.items = list(items)

    def run_detached(self) -> None: ...
    def stop(self) -> None:
        self.stopped = True


class MiniLoginItem:
    def is_enabled(self) -> bool:
        return False

    def enable(self, command: Any) -> None: ...
    def disable(self) -> None: ...


def _run_shell(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **kwargs: Any
) -> tuple[int, list, MiniWindow, MiniTray]:
    """跑一次 `run_shell`（真后端 + 注入端口）。

    `monkeypatch.chdir` 是必要的：壳**不设后端的 cwd**（沿用壳自己的 cwd），而 `.env` 引导
    装载按 `<cwd>/.env` 取值——不 chdir 的话子进程会读到**仓库的 `.env`**，于是「要不要口令」
    取决于开发机恰好的端点配置（本用例要测的是「本根要不要口令」，不是那个）。
    """
    from st_agent.ui.shell.app import run_shell

    monkeypatch.chdir(tmp_path)
    notes: list[tuple[str, str]] = []
    window, tray = MiniWindow(), MiniTray()
    code = run_shell(
        backend_spec=_spec(root),
        window=window,
        tray=tray,
        login_item=MiniLoginItem(),
        notifier=lambda title, body: notes.append((title, body)),
        parent_pid=os.getpid(),
        **kwargs,
    )
    return code, notes, window, tray


def test_run_shell_negotiates_then_reclaims(
    encrypted_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """端到端：真后端要口令 → 框里取值 → 写回凭据库 → 开窗 → 退出即回收（无孤儿）。"""
    prompt, vault = FakePrompt(_PASS), FakeVault()
    code, notes, window, tray = _run_shell(
        encrypted_root, tmp_path, monkeypatch, prompt=prompt, vault=vault
    )

    assert code == 0
    assert len(prompt.requests) == 1 and prompt.requests[0].kind == "main_passphrase"
    assert list(vault.data.values()) == [_PASS]          # 成功的口令写回凭据库
    assert window.urls and window.urls[0].count("#t=") == 1     # 开窗地址带令牌 fragment
    assert tray.stopped and not notes                    # 无异常告知，托盘已停


def test_run_shell_reports_cancel_without_opening_a_window(
    encrypted_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """取消 ⇒ 中性说明 + 退出码 0；**不开空窗口**、不启动后端。"""
    code, notes, window, _ = _run_shell(
        encrypted_root, tmp_path, monkeypatch, prompt=FakePrompt(None), vault=None
    )

    assert code == 0
    assert notes and "已取消" in notes[0][1]
    assert window.urls == []


def test_run_shell_announces_keyring_degradation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """凭据库不可用 ⇒ **显式告知一次**（不是静默降级），随后照常按弹框路径走。"""
    monkeypatch.setattr(passphrase_mod, "load_keyring",
                        _unavailable("未安装口令记忆依赖 keyring：pip install -e \".[keyring]\""))
    root = tmp_path / "store"                            # 明文根：不需要口令，直接起得来
    Store.create(root)

    code, notes, window, _ = _run_shell(root, tmp_path, monkeypatch, prompt=FakePrompt())

    assert code == 0
    assert any("系统凭据库不可用" in body for _, body in notes)
    assert window.urls                                       # 降级不影响启动


# ───────────────────────── 真凭据库 / 真包装层的边 ─────────────────────────


def test_native_prompt_masks_input_and_maps_cancel_to_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """包装层：`show="*"` 掩码输入；取消 / 全空白 ⇒ `None`（不把空串当口令）；框关即销毁。"""
    seen: dict[str, Any] = {}
    answer: list[str | None] = [None]

    class FakeTk:
        def withdraw(self) -> None:
            seen["withdrawn"] = True

        def destroy(self) -> None:
            seen["destroyed"] = True

    class FakeSimpleDialog:
        @staticmethod
        def askstring(title: str, prompt: str, show: Any = None, parent: Any = None) -> Any:
            seen.update(title=title, prompt=prompt, show=show, parent=parent)
            return answer[0]

    monkeypatch.setattr(passphrase_mod, "_load_tk", lambda: (FakeTk, FakeSimpleDialog))

    assert NativePassphrasePrompt().ask(PassphraseRequest(_ROOT, "credentials", 2)) is None
    assert seen["show"] == "*" and seen["withdrawn"] and seen["destroyed"]
    assert "第 2 次" in seen["prompt"] and _ROOT in seen["prompt"]     # 文案：中性 + 定位到根

    answer[0] = "   "                                   # 只有空白也算没给
    assert NativePassphrasePrompt().ask(PassphraseRequest(_ROOT, "credentials")) is None
    answer[0] = "typed"
    assert NativePassphrasePrompt().ask(PassphraseRequest(_ROOT, "credentials")) == "typed"


def test_native_prompt_reports_missing_display(monkeypatch: pytest.MonkeyPatch) -> None:
    """有 tkinter 但**没有显示**（`Tk()` 抛）：显式抛并给绕法，不静默什么都不问。"""
    def boom_tk() -> Any:
        raise RuntimeError("no display name and no $DISPLAY environment variable")

    class FakeSimpleDialog:
        @staticmethod
        def askstring(*args: Any, **kwargs: Any) -> Any:   # pragma: no cover - 不会走到
            raise AssertionError("不该弹框")

    monkeypatch.setattr(passphrase_mod, "_load_tk", lambda: (boom_tk, FakeSimpleDialog))
    with pytest.raises(ShellSurfaceUnavailable, match="--passphrase-env"):
        NativePassphrasePrompt().ask(PassphraseRequest(_ROOT, "credentials"))


def test_run_shell_remembers_passphrase_in_real_credential_store(
    encrypted_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """装了 `[keyring]` 时才跑：第二次启动**一次框都不弹**（口令真落在系统凭据库）。

    用**一次性 service 名**跑真实凭据库后端，用完即删——不碰 `st-agent` 的正典条目。
    """
    pytest.importorskip("keyring")
    vault = KeyringVault(service="st-agent-selftest")
    account = str(encrypted_root)
    try:
        first = FakePrompt(_PASS)
        assert _run_shell(encrypted_root, tmp_path, monkeypatch, prompt=first, vault=vault)[0] == 0
        assert len(first.requests) == 1
        assert vault.get(account) == _PASS               # 真的写进了系统凭据库

        second = FakePrompt()                            # 零答案：一旦弹框就会取消
        assert _run_shell(encrypted_root, tmp_path, monkeypatch, prompt=second, vault=vault)[0] == 0
        assert second.requests == []                     # 免重复输入
    finally:
        vault.delete(account)
