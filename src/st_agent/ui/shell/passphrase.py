"""壳侧的口令**索取 / 记忆 / 回传**（[`T-UI-002.4.2`]；[00 §1.1] / [02 §3] / [D-078]）。

壳只做三件事——**问**（原生口令框）、**记**（操作系统凭据库）、**给**（经两个进程之间的
管道回传）。**不含任何解锁或存储逻辑**：解锁只发生在后端进程内（[00 §1.1]）。

- 策略收在 :class:`PassphraseResolver`：「同一根、同一类口令，本次运行**先查凭据库**；取不到
  或已被后端否掉就弹框；框里拿到的值**写回**凭据库」。它是纯策略、无 GUI，故可离线单测。
- 两个界面 / 平台边（``tkinter`` 口令框、``keyring``）一律**惰性导入**——CI 与发布包都不装
  它们（同 ``[shell]`` extra 的既有口径）：口令框缺 Tk 时**显式**抛，凭据库缺时**显式降级**。

**口令不落任何本地文件**：凭据库副本由操作系统持有（[02 §3]），存储根内没有口令的副本，
日志里也不打口令（凭据库读写失败只记「读/写失败」这一事实）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from st_agent.ui.shell.errors import ShellSurfaceUnavailable

__all__ = [
    "KeyringVault",
    "NativePassphrasePrompt",
    "PassphrasePrompt",
    "PassphraseRequest",
    "PassphraseResolver",
    "PassphraseVault",
    "build_vault",
    "load_keyring",
]

_LOG = logging.getLogger("st_agent.ui.shell.passphrase")

_SERVICE = "st-agent"
"""凭据库的 service 名；与 account（规范化后的存储根）合起来唯一定位一条口令。"""

_KIND_LABEL = {"main_passphrase": "主密码", "credentials": "凭据口令"}
"""口令种类标签（与后端 `__main__._KIND_LABEL` 同义；两侧各自独立，不互相 import）。"""


@dataclass(frozen=True)
class PassphraseRequest:
    """后端的一次口令请求（`event=passphrase_required` 的载荷）。"""

    root: str
    """存储根（后端给出）——既是凭据库的账号键，也是提示文案里的定位信息。"""

    kind: str
    """``main_passphrase``（加密根的主密码）/ ``credentials``（明文根的凭据口令）。"""

    attempt: int = 1
    """本次启动内的第几次索取（第 1 次＝首次；>1 即上一次给的值被后端否掉）。"""

    reason: str = ""
    """后端的中性说明（只讲「需要什么口令」，不含失败细节）。"""

    @property
    def label(self) -> str:
        return _KIND_LABEL.get(self.kind, self.kind)


class PassphrasePrompt(Protocol):
    """口令索取端口（GUI 边的替身面）。``None`` ＝ 用户**取消**。"""

    def ask(self, request: PassphraseRequest) -> str | None: ...


class PassphraseVault(Protocol):
    """口令记忆端口（操作系统凭据库的替身面）。"""

    def get(self, account: str) -> str | None: ...

    def set(self, account: str, value: str) -> None: ...

    def delete(self, account: str) -> bool:
        """删掉该账号的条目；返回是否**确实删掉了**（本来就没有 ⇒ ``False``）。"""
        ...


# ───────────────────────── 操作系统凭据库 ─────────────────────────


def load_keyring() -> Any:
    """惰性取 ``keyring``；未安装即显式抛（附带安装命令）。"""
    try:
        import keyring                       # noqa: PLC0415
    except ImportError as exc:
        raise ShellSurfaceUnavailable(
            "未安装口令记忆依赖 keyring：pip install -e \".[keyring]\""
        ) from exc
    return keyring


def _account(root: str) -> str:
    """账号键＝**规范化后的存储根**（同根同键、跨根不串用，[D-078] ②）。

    规范化只做「展开 ``~`` + 绝对化 + 消解符号链接」；拿不到就退回原串——键退化的代价
    只是「同一根被记成两条」，不会串到别的根上。
    """
    try:
        return str(Path(root).expanduser().resolve())
    except OSError:                          # pragma: no cover - 平台相关
        return root


class KeyringVault:
    """操作系统凭据库（Windows 凭据管理器 / macOS 钥匙串 / Linux Secret Service）。"""

    def __init__(self, *, service: str = _SERVICE, keyring: Any = None) -> None:
        self._keyring = load_keyring() if keyring is None else keyring
        self._service = service

    def get(self, account: str) -> str | None:
        """取口令；**凭据库故障不该拦住启动** ⇒ 记一条日志后按「没有」处理。"""
        try:
            value = self._keyring.get_password(self._service, _account(account))
        except Exception:                    # noqa: BLE001 —— 见 docstring
            _LOG.warning("读取系统凭据库失败，本次改为索取口令", exc_info=True)
            return None
        return value or None

    def set(self, account: str, value: str) -> None:
        """写入口令；失败只降级为「本次运行内可用」，不打断已经开始的启动。"""
        try:
            self._keyring.set_password(self._service, _account(account), value)
        except Exception:                    # noqa: BLE001
            _LOG.warning("写入系统凭据库失败，本条目只在本次运行内有效", exc_info=True)

    def delete(self, account: str) -> bool:
        """删条目；「本来就没有」返回 ``False``，其余故障**上抛**（清除是用户的明确动作）。"""
        try:
            self._keyring.delete_password(self._service, _account(account))
        except Exception as exc:             # noqa: BLE001
            errors = getattr(self._keyring, "errors", None)
            missing = getattr(errors, "PasswordDeleteError", None) if errors else None
            if missing is not None and isinstance(exc, missing):
                return False
            raise
        return True


def build_vault() -> tuple[PassphraseVault | None, str | None]:
    """取口令记忆端口；凭据库不可用时**降级为每次索取**并给出一句**显式**告知。

    降级不是静默（[02 §3]）：调用方应把 ``notice`` 报给用户一次。
    """
    try:
        return KeyringVault(), None
    except ShellSurfaceUnavailable as exc:
        return None, f"系统凭据库不可用，本次及后续启动都会重新索取口令（{exc}）"


# ───────────────────────── 原生口令框 ─────────────────────────


def _load_tk() -> Any:
    """惰性取 ``tkinter``（stdlib，但冻结产物 / 精简发行版可能没有）。"""
    try:
        from tkinter import Tk, simpledialog   # noqa: PLC0415
    except ImportError as exc:
        raise ShellSurfaceUnavailable(
            "本机没有 Tk（python3-tk / 冻结产物未打 Tk），弹不出原生口令框；"
            "可改用 --passphrase-env 提供口令"
        ) from exc
    return Tk, simpledialog


class NativePassphrasePrompt:
    """原生口令框（stdlib ``tkinter.simpledialog``，``show="*"``）。

    调用点在 **pywebview 窗口主循环之前**（[D-078] ①）——此刻没有别的循环占着主线程，
    故与 [`T-UI-002.2`] 假设 `A1`（窗口必须在主线程）不冲突；框关掉即销毁临时 root。
    """

    def __init__(self, *, title: str = "ST Agent") -> None:
        self._title = title

    def ask(self, request: PassphraseRequest) -> str | None:
        tk, simpledialog = _load_tk()
        try:
            root = tk()
        except Exception as exc:             # noqa: BLE001 —— 无显示 / 缺底层库
            raise ShellSurfaceUnavailable(
                f"无法打开原生口令框（{type(exc).__name__}: {exc}）；"
                "可改用 --passphrase-env 提供口令"
            ) from exc
        root.withdraw()
        try:
            value = simpledialog.askstring(
                self._title, _prompt_text(request), show="*", parent=root
            )
        finally:
            root.destroy()
        return value if value is not None and value.strip() else None


def _prompt_text(request: PassphraseRequest) -> str:
    """框内文案：中性、只说「要什么」与「错了就再输」；不回显所给过的口令。"""
    lines = [request.reason or f"请输入{request.label}。"]
    if request.attempt > 1:
        lines.append(f"上一次未通过（第 {request.attempt} 次尝试）。")
    lines.append(f"存储根：{request.root}")
    lines.append("取消将不再启动应用。口令不会被写入本地文件。")
    return "\n".join(lines)


# ───────────────────────── 策略 ─────────────────────────


class PassphraseResolver:
    """「**先查凭据库 → 否则弹框 → 成功后写回**」的策略（无 GUI，可离线单测）。

    每个「(存储根, 口令种类)」在一次运行内**只查一次凭据库**：后端因口令不对而再次索取时
    直接弹框（那条记的值已经不可信），成功后由写回覆盖它。故失效条目的自愈不需要额外状态。
    """

    def __init__(
        self,
        *,
        prompt: PassphrasePrompt,
        vault: PassphraseVault | None = None,
    ) -> None:
        self._prompt = prompt
        self._vault = vault
        self._recalled: set[tuple[str, str]] = set()

    def __call__(self, request: PassphraseRequest) -> str | None:
        key = (request.root, request.kind)
        if key not in self._recalled:
            self._recalled.add(key)
            if self._vault is not None:
                remembered = self._vault.get(request.root)
                if remembered:
                    return remembered
        value = self._prompt.ask(request)
        if value is None:
            return None
        if self._vault is not None:
            self._vault.set(request.root, value)
        return value
